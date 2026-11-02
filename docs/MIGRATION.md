# 从旧版（v0）迁移到 v1

v1 改成了端到端加密，旧客户端和新服务器不能混用。按下面的顺序迁移，旧数据可以完整带过来。

## 1. 部署 v1 服务器

按 [DEPLOY.md](DEPLOY.md) 部署。可以用新机器，也可以复用旧服务器。复用时先停掉旧服务：

* 停止旧的 `egoserver-run`，也就是 gunicorn 进程；
* 删除旧的 nginx 站点 `/etc/nginx/sites-enabled/egorovsystem` 并重载 nginx，Tunnel 会取代它；
* 旧服务器上的 Django `SECRET_KEY` 已经随 PyPI 包公开过。v1 不再使用它，旧服务停掉后即失效。

## 2. 导入旧数据

旧数据库在旧服务器 Python 环境的 site-packages 里。在旧服务器上找到它：

```bash
python3 -c "import egoserver, os; print(os.path.join(os.path.dirname(egoserver.__file__), 'db.sqlite3'))"
```

把这个文件拷到新服务器，先试运行，再正式导入：

```bash
alias ego-admin='sudo runuser -u egorov -- env EGOROV_DATA_DIR=/var/lib/egorov /opt/egorov/bin/egoserver-admin'
ego-admin import-legacy /root/old-db.sqlite3 --dry-run
ego-admin import-legacy /root/old-db.sqlite3
```

导入时要输入主密码，数据在服务器上加密后才写入。

* 变量和函数都会导入，函数类型（cmd 或 python）保留；
* 旧的访问密码**不会**导入，v1 改用设备令牌；
* 名字不符合 v1 规则的条目会被列出并跳过，需要在后台手动重建。v1 的名字只能用字母、数字、`_`、`.`、`-`，最多 128 个字符。

导入完成后删除拷贝过来的旧数据库文件，它是明文的。

## 3. 更换曾经明文传输过的秘密

旧版通过明文 HTTP 传输主密码和变量值。如果曾在公共 Wi-Fi 等不可信网络上使用过，建议在各服务商处重新生成这些密钥，再到后台更新对应变量。

## 4. 为每台机器创建设备令牌

在后台“设备”页为每台机器单独创建一个令牌。令牌只显示一次，页面上会给出这台机器的配置命令。

## 5. 升级每台机器的客户端

```bash
pip install -U egorovsystem
```

然后把 `EGOROV_PWD` 改成新的设备令牌。`EGOROV_SERVER` 可以只写域名，例如 `egorov.example.com`，默认走 https。

YTools 依赖 egorovsystem，它用到的 `Egorov`、`get_variable` 在 v1 中保持不变，无需修改。

使用 egolocal 的机器，升级后重新运行一次 `egolocal-run`。

## 行为变化一览

| 方面 | v0 | v1 |
| --- | --- | --- |
| 传输 | 明文 HTTP | HTTPS，且内容端到端加密 |
| `EGOROV_PWD` | 共享的 10 位密码 | 每台机器一个设备令牌，可限定范围、可吊销 |
| `${变量}` 替换 | 服务器上做 | 客户端做，语义相同：只替换存在且可读的变量 |
| `EGO 名字 参数…` | 忽略参数，丢弃退出码 | 参数会传给命令，退出码原样返回 |
| python 类型的函数 | 用 PATH 里的 `python3` 运行 | 用运行 EGO 的同一个解释器运行，PEP 723 脚本走 `uv run` |
| `Egorov["名字"]` | 任何错误都会转去查同名函数 | 只有变量不存在时才查函数 |
| egolocal | 无鉴权，允许任意网页跨域读取 | 需要本机令牌文件，拒绝浏览器请求 |
| 管理后台 | Django admin | 新的端到端加密后台，支持两步验证 |
| `egoserver-setnginx` | 写 nginx 配置 | 已移除，改用 Cloudflare Tunnel |
