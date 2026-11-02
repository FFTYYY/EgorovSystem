#!/usr/bin/env bash
# Install or upgrade the Egorov server on a dedicated Debian or Ubuntu machine.
# Safe to run again: it upgrades the package and restarts the service.
#
#   sudo bash install-server.sh
#   sudo EGOROV_SOURCE=./egorovsystem-1.0.0-py3-none-any.whl bash install-server.sh
#   sudo TUNNEL_TOKEN='eyJ...' bash install-server.sh      # also connect Cloudflare Tunnel
#   sudo ENABLE_FIREWALL=1 bash install-server.sh          # also block all inbound ports except SSH
#
# Settings (environment variables):
#   EGOROV_SOURCE    pip requirement, wheel or source directory (default: egorovsystem[server]>=1.0)
#   EGOROV_PORT      local port the server listens on (default: 7999, bound to 127.0.0.1 only)
#   TUNNEL_TOKEN     token of a remotely managed Cloudflare Tunnel
#   INSTALL_CLOUDFLARED  1 to install cloudflared without a token (register it later yourself)
#   ENABLE_FIREWALL  1 to enable ufw with only SSH allowed in
set -euo pipefail

EGOROV_SOURCE="${EGOROV_SOURCE:-egorovsystem[server]>=1.0}"
PORT="${EGOROV_PORT:-7999}"
PREFIX=/opt/egorov
DATA=/var/lib/egorov
SERVICE_USER=egorov

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root, e.g. sudo bash $0"
command -v apt-get >/dev/null || die "this script supports Debian and Ubuntu (apt-get not found)"
command -v systemctl >/dev/null || die "systemd is required"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "EGOROV_PORT must be a number"

# ------------------------------------------------------------------ packages
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv curl ca-certificates iproute2 >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || die "Python 3.10 or newer is required, found $(python3 --version 2>&1)"

# ------------------------------------------------------------------ user and data
log "Preparing service user '$SERVICE_USER' and $DATA"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$DATA" "$DATA/backups"

# ------------------------------------------------------------------ application
log "Installing egorovsystem into $PREFIX"
[ -x "$PREFIX/bin/python" ] || python3 -m venv "$PREFIX"
"$PREFIX/bin/python" -m pip install -q --upgrade pip
if [ -e "$EGOROV_SOURCE" ]; then
  SPEC="$(realpath "$EGOROV_SOURCE")[server]"
else
  SPEC="$EGOROV_SOURCE"
fi
"$PREFIX/bin/python" -m pip install -q --upgrade "$SPEC"
VERSION="$("$PREFIX/bin/python" -c 'import egorovsystem; print(egorovsystem.__version__)')"
log "egorovsystem $VERSION installed"

# ------------------------------------------------------------------ systemd
log "Writing systemd units"
HARDENING='NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
CapabilityBoundingSet=
UMask=0077'

cat >/etc/systemd/system/egorov.service <<EOF
[Unit]
Description=Egorov server (end-to-end encrypted vault)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
Environment=EGOROV_DATA_DIR=$DATA
ExecStart=$PREFIX/bin/egoserver-run --host 127.0.0.1 --port $PORT
Restart=on-failure
RestartSec=3
StateDirectory=egorov
StateDirectoryMode=0700
$HARDENING

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/egorov-backup.service <<EOF
[Unit]
Description=Daily encrypted backup of the Egorov database

[Service]
Type=oneshot
User=$SERVICE_USER
Group=$SERVICE_USER
Environment=EGOROV_DATA_DIR=$DATA
ExecStart=$PREFIX/bin/egoserver-admin backup $DATA/backups --keep 14
StateDirectory=egorov
StateDirectoryMode=0700
$HARDENING
EOF

cat >/etc/systemd/system/egorov-backup.timer <<EOF
[Unit]
Description=Daily encrypted backup of the Egorov database

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload

# ------------------------------------------------------------------ first-time setup
as_service() { runuser -u "$SERVICE_USER" -- env EGOROV_DATA_DIR="$DATA" "$@"; }
INITIALIZED=1
as_service "$PREFIX/bin/egoserver-admin" info >/dev/null 2>&1 || INITIALIZED=0
if [ "$INITIALIZED" = 0 ] && [ -t 0 ]; then
  log "Creating the admin account and the vault"
  as_service "$PREFIX/bin/egoserver-init" && INITIALIZED=1
fi

if [ "$INITIALIZED" = 1 ]; then
  systemctl enable -q egorov.service egorov-backup.timer
  systemctl restart egorov.service
  systemctl start egorov-backup.timer
  for _ in $(seq 1 20); do
    curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || die "the service did not start; see: journalctl -u egorov"
  log "Egorov is running on 127.0.0.1:$PORT"
fi

# ------------------------------------------------------------------ Cloudflare Tunnel
if [ -n "${TUNNEL_TOKEN:-}" ] || [ "${INSTALL_CLOUDFLARED:-0}" = 1 ]; then
  log "Installing cloudflared from Cloudflare's apt repository"
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    >/etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  apt-get install -y -qq cloudflared >/dev/null
  if [ -f /etc/systemd/system/cloudflared.service ]; then
    log "cloudflared is already installed as a service; leaving its token unchanged"
  elif [ -n "${TUNNEL_TOKEN:-}" ]; then
    cloudflared service install "$TUNNEL_TOKEN"
  fi
  if [ -f /etc/systemd/system/cloudflared.service ]; then
    systemctl enable -q --now cloudflared
  else
    log "cloudflared installed; connect it with: sudo cloudflared service install <tunnel token>"
  fi
fi

# ------------------------------------------------------------------ firewall
if [ "${ENABLE_FIREWALL:-0}" = 1 ]; then
  log "Enabling ufw: deny all inbound except SSH"
  apt-get install -y -qq ufw >/dev/null
  # sshd itself, plus ssh.socket for systemd socket activation (Ubuntu 22.10+)
  SSH_PORTS="$( {
      ss -tlnpH 2>/dev/null | awk '/"sshd"/ {n = split($4, a, ":"); print a[n]}'
      systemctl show -p Listen ssh.socket 2>/dev/null | grep -oE '[0-9]+ \(Stream\)' | tr -dc '0-9\n'
    } | grep -E '^[0-9]+$' | sort -u)"
  if [ -z "$SSH_PORTS" ]; then
    # Do not guess: a wrong port here would lock you out of the server.
    die "could not find the port sshd listens on; skipping the firewall. Configure ufw by hand."
  fi
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  for p in $SSH_PORTS; do ufw allow "$p/tcp" >/dev/null; log "  SSH stays open on port $p"; done
  ufw --force enable >/dev/null
fi

# ------------------------------------------------------------------ summary
echo
log "Done."
if [ "$INITIALIZED" = 0 ]; then
  echo "The vault is not initialized yet. Run this once, then start the service:"
  echo "  sudo runuser -u $SERVICE_USER -- env EGOROV_DATA_DIR=$DATA $PREFIX/bin/egoserver-init"
  echo "  sudo systemctl enable --now egorov.service egorov-backup.timer"
fi
if [ -z "${TUNNEL_TOKEN:-}" ] && [ ! -f /etc/systemd/system/cloudflared.service ]; then
  echo "Next: create a Cloudflare Tunnel whose public hostname points to http://localhost:$PORT,"
  echo "then run this script again with TUNNEL_TOKEN set. See docs/DEPLOY.md."
fi
echo "Maintenance: sudo runuser -u $SERVICE_USER -- env EGOROV_DATA_DIR=$DATA $PREFIX/bin/egoserver-admin --help"
