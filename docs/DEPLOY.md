# 部署 Egorov 服务器

推荐方案：一台专用的 Debian 或 Ubuntu 服务器，通过 **Cloudflare Tunnel** 对外提供 HTTPS。

```
浏览器 / 设备 ──HTTPS──▶ Cloudflare 边缘 ──隧道──▶ cloudflared ──▶ 127.0.0.1:7999 egoserver ──▶ SQLite（只有密文）
```

为什么选 Tunnel：

* 服务器上的 cloudflared 主动向外连接 Cloudflare，**服务器不需要开放任何入站端口**，源站 IP 也不会暴露。
* 不需要自己申请和续期证书，TLS 由 Cloudflare 负责，隧道本身也是加密的。
* egoserver 只监听 127.0.0.1，除了 cloudflared 没有人能连到它。

> Cloudflare 能看到经过它的 HTTPS 流量，但 Egorov 是端到端加密的：变量的值、函数内容和主密码都不会以明文出现在网络上。

## 需要准备

* 一台专用服务器，系统为 Debian 12 或 Ubuntu 22.04 及以上，有 root 权限。
* 一个托管在 Cloudflare 上的域名，下文以 `egorov.example.com` 为例。

## 第 1 步：在 Cloudflare 创建隧道

Cloudflare 面板的菜单名称偶尔会调整，以下按 2026 年的界面描述。

1. 打开 Cloudflare 面板，进入 **Networking → Tunnels**，选择 **Create a tunnel**。
2. 类型选 **Cloudflared**，名字填 `egorov`。
3. 操作系统选 **Debian**，Ubuntu 也选它。页面会给出一条安装命令，**不要运行它**，只复制 `service install` 后面那串以 `eyJ` 开头的长令牌。选哪个系统都不影响令牌，安装脚本会替你完成安装。
4. 在隧道的 **Routes** 里选择 **Add route → Published application**：
   * Subdomain 填 `egorov`，Domain 选你的域名；
   * Service URL 填 `http://localhost:7999`。

隧道令牌相当于服务器的身份凭据，不要发给别人。

## 第 2 步：在服务器上安装

```bash
curl -fsSLO https://raw.githubusercontent.com/FFTYYY/EgorovSystem/main/deploy/install-server.sh
sudo TUNNEL_TOKEN='eyJ...' ENABLE_FIREWALL=1 bash install-server.sh
```

脚本会：

1. 安装 Python 和依赖，创建系统用户 `egorov`；
2. 把 egorovsystem 装进 `/opt/egorov` 的独立虚拟环境；
3. 提示你设置管理员用户名和**主密码**，然后打印一次**恢复密钥**。请把恢复密钥抄在纸上或存进密码管理器；
4. 写入加固过的 systemd 服务，数据放在 `/var/lib/egorov`，权限只对服务用户开放；
5. 开启每日备份，保留最近 14 份；
6. 从 Cloudflare 官方 apt 源安装 cloudflared，并用你的令牌连上隧道；
7. 设置了 `ENABLE_FIREWALL=1` 时启用 ufw，只放行 SSH 入站。

还没发布到 PyPI 之前，可以先把构建好的 wheel 拷到服务器上安装：

```bash
sudo EGOROV_SOURCE=./egorovsystem-1.0.0-py3-none-any.whl TUNNEL_TOKEN='eyJ...' bash install-server.sh
```

脚本可以重复运行，升级时也用它。

## 第 3 步：Cloudflare 安全设置

| 位置 | 设置 | 原因 |
| --- | --- | --- |
| SSL/TLS → Edge Certificates | 开启 **Always Use HTTPS**；**Minimum TLS Version** 设为 1.2；开启 TLS 1.3 | 拒绝明文和旧协议 |
| SSL/TLS → Edge Certificates | 开启 **HSTS**，max-age 至少 6 个月。只有当域名下所有子域都支持 HTTPS 时才勾选 includeSubDomains | 浏览器以后只走 HTTPS |
| Zero Trust → Access controls → Applications | 选 **Create new application → Self-hosted and private**，用 **Add public hostname** 添加两个路径：`egorov.example.com/admin` 和 `egorov.example.com/api/v1/admin`。策略设为只允许你的邮箱，登录方式用一次性验证码 | 后台多一道与 Egorov 无关的门。**不要**覆盖 `/api/v1/device`，否则设备无法访问 |
| Security → WAF → Rate limiting rules | 对 `/api/v1/admin/login` 和 `/api/v1/admin/prelogin` 限流，按套餐允许的最严格值设置 | 和服务端自身的限流叠加 |
| Speed / Scrape Shield / Zaraz | 对这个子域关闭 **Rocket Loader**、**Email Address Obfuscation**、**Zaraz** 和 Web Analytics 的自动注入 | 这些功能会往页面里插脚本。CSP 会拦截它们，而且端到端加密的后台不应该被任何人改动 |
| Security → Bots | 如果设备访问时收到 403 或质询页面，关闭 **Bot Fight Mode**，或为 `/api/v1/device/*` 设置例外 | 设备是命令行程序，过不了浏览器质询 |

## 第 4 步：验证

1. 浏览器打开 `https://egorov.example.com/admin/`，用主密码登录。建议立即在“设置”里开启两步验证。
2. 在“设备”里为一台电脑创建令牌，在那台电脑上：

   ```bash
   export EGOROV_SERVER=egorov.example.com
   export EGOROV_PWD='ego1....'
   EGO --whoami
   ```

3. 检查响应头里有 CSP 和 HSTS：`curl -sI https://egorov.example.com/admin/`。
4. 在服务器上确认没有对外监听的端口：`sudo ss -tlnp`，应只看到 `127.0.0.1:7999` 和 sshd。

## 日常维护

以下命令都在服务器上运行。为了简洁，先定义一个别名：

```bash
alias ego-admin='sudo runuser -u egorov -- env EGOROV_DATA_DIR=/var/lib/egorov /opt/egorov/bin/egoserver-admin'
```

| 任务 | 命令 |
| --- | --- |
| 查看状态 | `ego-admin info`、`systemctl status egorov`、`journalctl -u egorov` |
| 升级 | 重新运行 `install-server.sh` |
| 立即备份 | `ego-admin backup /var/lib/egorov/backups` |
| 忘记主密码 | `ego-admin reset-password`，需要恢复密钥 |
| 手机丢了，无法两步验证 | `ego-admin disable-totp` |
| 让所有后台会话下线 | `ego-admin revoke-sessions` |
| 导入旧版数据 | `ego-admin import-legacy /path/to/old/db.sqlite3`，见 MIGRATION.md |

**备份放到别处。** 每日备份在 `/var/lib/egorov/backups`，和数据库在同一块盘上。备份本身是加密的，可以放心用 rclone 等工具同步到任何网盘。

**从备份恢复。**

```bash
sudo systemctl stop egorov
sudo cp /var/lib/egorov/backups/egorov-YYYYmmdd-HHMMSS.sqlite3 /var/lib/egorov/egorov.sqlite3
sudo chown egorov:egorov /var/lib/egorov/egorov.sqlite3
sudo rm -f /var/lib/egorov/egorov.sqlite3-wal /var/lib/egorov/egorov.sqlite3-shm
sudo systemctl start egorov
```

## 备选方案：不用 Tunnel

如果不能用 Tunnel，可以让 Cloudflare 直接连源站：

1. 在 **SSL/TLS → Origin Server** 生成 Origin CA 证书，有效期最长 15 年，装到 nginx 或 Caddy 上，反向代理到 `127.0.0.1:7999`；
2. SSL/TLS 模式设为 **Full (strict)**；
3. 开启 **Authenticated Origin Pulls**，让源站只接受带 Cloudflare 客户端证书的连接；
4. 防火墙只对 Cloudflare 的 IP 段开放 443。

这条路需要自己维护 nginx 和证书，源站 IP 也有被发现的可能，所以默认不推荐。注意源站拉取认证和 Tunnel 不能同时使用，Tunnel 本身已经认证了连接。
