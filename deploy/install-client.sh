#!/usr/bin/env sh
# Install the Egorov client (EGO, egolocal-run) on macOS or Linux.
# Works on machines without Python: uv downloads a standalone Python if needed.
#
#   sh install-client.sh
#   EGOROV_SOURCE='git+https://github.com/FFTYYY/EgorovSystem' sh install-client.sh
set -eu

EGOROV_SOURCE="${EGOROV_SOURCE:-egorovsystem>=1.0}"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

echo "==> Installing the Egorov client"
uv tool install --force --python 3.12 "$EGOROV_SOURCE"
uv tool update-shell >/dev/null 2>&1 || true

echo
echo "Done. Open a new terminal, then point this machine at your server:"
echo "  export EGOROV_SERVER=egorov.example.com"
echo "  export EGOROV_PWD='ego1....'      # a device token from the admin UI"
echo "  EGO --whoami"
echo
echo "To use Egorov[\"name\"] from your own Python code, also run in that environment:"
echo "  pip install -U egorovsystem"
