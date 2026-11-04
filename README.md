# EgorovSystem

在不同设备之间同步变量、密钥和常用命令的小工具。v1 是端到端加密的：服务器、Cloudflare 和数据库里都只有密文。

```python
from egorovsystem import Egorov

openai_key = Egorov["openai_keys"]
```

```bash
EGO deploy_blog          # 运行保存在服务器上的命令
```

## 工作方式

* **服务器**只保存密文。变量的值、函数内容、备注和主密码都不会以明文出现在服务器上。
* **管理后台**在浏览器里加密和解密，主密码从不发给服务器。界面是深色学院风，支持两步验证、审计日志和密钥轮换。浏览器通过一次两步验证后，48 小时内登录只需要主密码。
* **每台设备**有自己的令牌，可以限定能读哪些条目，可以随时停用或吊销。
* **部署**推荐用 Cloudflare Tunnel：服务器不开放任何入站端口，egoserver 只监听本机。

细节见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。

## 快速开始

### 服务器

在一台 Debian 或 Ubuntu 服务器上：

```bash
curl -fsSLO https://raw.githubusercontent.com/FFTYYY/EgorovSystem/main/deploy/install-server.sh
sudo TUNNEL_TOKEN='eyJ...' ENABLE_FIREWALL=1 bash install-server.sh
```

完整步骤和 Cloudflare 的安全设置见 [docs/DEPLOY.md](docs/DEPLOY.md)。

### 客户端

```bash
pip install -U egorovsystem
```

没有 Python 的机器可以用安装脚本，它会通过 uv 自动下载 Python：

```bash
sh deploy/install-client.sh                                  # macOS / Linux
powershell -ExecutionPolicy Bypass -File deploy/install-client.ps1   # Windows
```

然后在后台“设备”页创建令牌，并配置：

```bash
export EGOROV_SERVER=egorov.example.com
export EGOROV_PWD='ego1....'
EGO --whoami
```

不想把令牌放进环境变量时，运行一次 `egolocal-run`。之后同一台机器上的程序会通过它读取。

## 在手机上使用

在手机上打开管理后台，界面会换成适合触屏的样式：底部是标签栏，点一行打开详情，排序用拖动。它也可以安装成应用：

* **Android**：Chrome 会提示安装，也可以在“更多”里点“安装到主屏幕”。
* **iPhone / iPad**：在 Safari 里点分享按钮，选择“添加到主屏幕”。

发布新版本后，已安装的应用回到前台时会自动刷新到新版本。如果正在编辑，就先在底部提示，不打断你。

安装后的应用只缓存界面文件，不缓存任何数据。没有网络时它也能打开，但会提示无法连接服务器。和电脑上一样，闲置 15 分钟后保险库自动锁定，切到后台的时间也算在内。

## 用法

| 写法 | 作用 |
| --- | --- |
| `Egorov["name"]` | 读取变量；没有这个变量时返回同名函数 |
| `get_variable("name")` | 读取变量 |
| `get_function("name")` | 返回 `{"type", "content", "note"}`，`${变量}` 已替换 |
| `set_variable("name", "value")` | 在当前进程里覆盖一个变量，方便测试 |
| `EGO name [参数…]` | 运行函数，参数会传给命令，返回命令的退出码 |
| `EGO --get name [-o 文件]` | 只输出变量的值，不带任何其他内容 |
| `EGO --show name [-o 文件]` | 输出函数内容（`${变量}` 已替换） |
| `EGO --help name` | 输出变量或函数的备注；同名的变量和函数都存在时两个都列出 |
| `EGO --list` / `--whoami` | 列出可读条目、查看本机设备 |
| `egolocal-run [--port 11451]` | 启动本机代理 |

函数里可以用 `${变量名}` 引用变量，客户端在运行前替换。不存在的名字原样保留，所以 `${HOME}` 这类 shell 写法不受影响。

`--get` 和 `--show` 的输出被重定向或接管道时，是逐字节的原始内容（UTF-8，换行不转换，结尾不加换行），在终端上显示时才补一个换行。Windows PowerShell 5.1 的 `>` 会把输出转成 UTF-16，看起来每个字符之间多了空格，所以在 PowerShell 5.1 里请用 `-o 文件`，例如 `EGO --get key-hahn -o hahn.pem`。用 `-o` 写出的文件在 macOS 和 Linux 上权限是 600。

python 类型的函数用运行 EGO 的同一个解释器执行。如果脚本带有 [PEP 723](https://peps.python.org/pep-0723/) 依赖声明，并且机器上装了 uv，就用 `uv run` 执行，依赖会自动安装。

## 服务器命令

| 命令 | 作用 |
| --- | --- |
| `egoserver-init` | 创建管理员和保险库，打印恢复密钥 |
| `egoserver-run` | 启动服务器，默认 `127.0.0.1:7999` |
| `egoserver-admin info` | 查看状态 |
| `egoserver-admin backup <目录> --keep 14` | 备份数据库，数据库本身已加密 |
| `egoserver-admin reset-password` | 用恢复密钥重置主密码 |
| `egoserver-admin disable-totp` | 关闭两步验证 |
| `egoserver-admin import-legacy <旧库>` | 导入 v0 的数据 |

## 从旧版升级

见 [docs/MIGRATION.md](docs/MIGRATION.md)。旧客户端在服务器升级后会收到明确的升级提示。

## 开发

```bash
python -m venv .venv
.venv/bin/pip install -e ".[server,dev]"
.venv/bin/pytest
```

`tests/test_crypto_interop.py` 用 Node 运行浏览器端的加密代码，与 Python 实现逐字节比对。没装 Node 时这个测试会跳过。

`tests/test_admin_mobile.py` 用 Playwright 驱动 Chrome，检查手机和桌面两种布局。没装 Playwright 或 Chrome 时这个测试会跳过。

## 许可

MIT。管理后台自带的字体使用 SIL Open Font License，许可证随字体文件一起分发。
