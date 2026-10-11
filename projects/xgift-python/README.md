# X · Premium Gifts：Windows 单机兑换站

独立 Python/Django 项目，不调用原 Go 程序，不导入旧数据。当前默认**模拟模式**：完整兑换、持久化 worker、运营后台、公开链接队列、人工补单预览/停止/历史均可本地演示。真实接口仅通过本地 HTTP fixtures 验证；真实资格、报价、赠送、扣款均未现场验收。

> 🐣 **第一次接触编程？** 直接看 [零基础搭建教程](docs/新手部署教程.md)：从安装 uv 到域名 HTTPS、Windows 服务、备份恢复，全流程带截图和 FAQ。下面这份 README 面向已经熟悉命令行的部署者。
>
> **真实模式状态**：源码包含真实接口适配，但尚未使用真实账户、真实扣款及目标服务器完成端到端验收。请先按教程用单笔指定订单核对资格、金额、付款和交付，再考虑对客户开放。

## 安装与本地模拟

需要 Windows、Python 3.12+、uv。已验证 Windows / Python 3.14.7 / Django 6.0.9。所有命令在项目目录执行；安装路径可以包含空格。

一条命令起步（自动装依赖、迁移、建管理员、生成演示兑换码并打开网站+worker 两个窗口）：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\quickstart.ps1
```

手动执行等价步骤：

```powershell
uv sync --locked
New-Item -ItemType Directory -Force private | Out-Null
# 本地先使用默认模拟配置；不要直接使用外网 HTTPS 示例。
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py setup_xgift
.venv\Scripts\python.exe manage.py demo_setup --count 5
.venv\Scripts\python.exe manage.py collectstatic --noinput
.venv\Scripts\python.exe manage.py serve_xgift
# 在另一个终端运行；Ctrl+C 在当前操作结束后停止
.venv\Scripts\python.exe manage.py run_worker
```

访问 `http://127.0.0.1:8000/`、`/admin/` 和 `/operator/`。`demo_setup` 会建立模拟套餐与演示兑换码；也可以用在后台手工建立模拟套餐（商户/产品仅用于模拟）后生成、移动、导出兑换码。worker 串行执行订单；结果未知进入待核实，禁止自动重新付款。后台的原始 Django 管理页面及公开页静态样式均通过 WhiteNoise 提供，DEBUG=False 不依赖开发服务器。

初始化隐藏密码输入；重复运行不覆盖密码/密钥。已有加密配置**或兑换码**时丢失密钥会拒绝初始化，必须恢复原密钥。不要删除密钥来重置账户。

## 生产环境与 HTTPS

复制 `.env.example` 为 `private/server.env`，UTF-8，一行 `名称=值`，路径含空格可加双引号；不做 shell 变量展开、不支持行尾注释。布尔值仅接受 true/false/1/0。配置 `XGIFT_ENV_FILE` 指向该文件后，所有管理命令与服务使用相同数据库和密钥。服务固定读取项目 `private/server.env`。

```powershell
Copy-Item -LiteralPath .env.example -Destination private\server.env
# 私下编辑：真实域名、独立数据路径、HTTPS 来源及长随机 DJANGO_SECRET_KEY
$env:XGIFT_ENV_FILE=(Resolve-Path -LiteralPath private\server.env).Path
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py setup_xgift
.venv\Scripts\python.exe manage.py collectstatic --noinput
.venv\Scripts\python.exe manage.py check --deploy
```

部署数据到专用目录，例如 `C:/X data/db.sqlite3` 和 `C:/X data/master.key`；会话签名密钥可独立设置，未设置时从主密钥派生。环境文件中默认 simulated，不能因为提供了凭据自动切换真实模式。开启 live 还必须有经过验证的加密 X/支付配置，缺配置时 worker 拒绝处理。

安装 Caddy 后将 `deploy/Caddyfile` 域名替换为真实域名，在服务器配置 DNS、80/443 外部连通与 Caddy 服务。Caddy自动申请 HTTPS；不向外开放 Waitress 的 8000。网站固定监听 127.0.0.1。HTTPS 配置开启安全 cookie、强制 HTTPS、HSTS、允许主机和 CSRF 来源。首次部署应确认该域名所有子域都支持 HTTPS，再启用 HSTS；生产配置为长期 HTTPS。

**代理限流信任边界**：仅在提供的 Caddy 配置下启用 XGIFT_TRUST_CADDY。Caddy 覆盖单值 X-XGift-Client-IP，应用只接受本机代理，并用该真实地址限流，不信任用户自行提交的 X-Forwarded-For。不要在 Caddy 前直接增加 CDN/其他代理而沿用此配置；当前单网站进程的内存限流重启清空，不支持多站点进程共享限流。本机其他进程属于可信服务器边界。

## Windows 服务

仅在目标服务器管理员终端执行；本次研发**没有注册或启动任何系统服务**、没有修改防火墙。脚本默认使用权限较少且无密码的 `NT AUTHORITY\LocalService`；网站/worker 各一个 pywin32 包装服务。请先完成以上初始化。

```powershell
.\deploy\install.ps1
.\deploy\start.ps1
.\deploy\stop.ps1
```

安装脚本限制 private、数据库和密钥专用目录 ACL，授予 LocalService 读写数据、读取程序权限，同时保留 SYSTEM/Administrators。原有文件 ACL 同步处理。若使用专用服务账户，在 `services.msc` 私下修改两个服务登录身份、赋予“作为服务登录”，并相应授予程序读取和数据/密钥读写权限；勿把密码放命令行。不得授予普通用户程序写权限：程序代码运行在服务账户下。pywin32 的宿主依赖、账户授权、服务器版 Windows 行为均须在目标机验证；开发机普通进程启停不等于服务验收。

这两个脚本只管理 `XGiftWeb` 和 `XGiftWorker`。公网部署还需把 Caddy 单独注册为第三个开机启动的服务，并从外网完成 HTTPS 验收；具体命令见[零基础搭建教程第 9 章](docs/新手部署教程.md#9-第七步注册为-windows-服务)。

停止服务通过私有控制文件等待网站/worker当前操作结束；不强杀付款中的进程。普通进程也可传 `--stop-file "C:/private path/worker.stop"`，创建该文件后正常退出（下次启动前删除旧文件）。异常断电由订单租约/付款证据恢复到待核实，人工核验原订单后再决策；补单运行批次在租约过期后标记 interrupted，不自动继续支付。服务停止较慢时不要强制结束它。

## 备份与恢复

先停两个服务（普通进程也必须停）。使用 SQLite backup API 生成一致快照，另复制原 32 字节主密钥；不复制运行中数据库文件，不导出明文凭据，不覆盖旧备份。

```powershell
.\deploy\stop.ps1
.\deploy\backup.ps1 -Destination 'D:\X backup\2026-10-11'
.\deploy\start.ps1
```

备份目录仅当前账户/SYSTEM/Administrators 可访问；私下另行备份 `private/server.env` 中的独立会话密钥与运维配置，并离线加密保存。备份持有密钥即能解密数据库，勿上传公共目录。脚本检查注册服务已停；未注册的普通进程是否退出由操作者核实。

恢复时使用 `deploy/restore.ps1`（或 `python -m deploy.restore`）：它会校验备份密钥与数据库完整性，把现有数据库/密钥整体改名保留（含 `-wal`/`-shm` 残留），复制备份后运行 `manage.py check`。

```powershell
.\deploy\stop.ps1
.\deploy\restore.ps1 -Source 'D:\X backup\2026-10-11'
.\deploy\start.ps1
```

确认没有旧数据库连接/WAL/SHM残留；恢复正确 env/会话密钥，运行 install 脚本重设账户 ACL。不要调用 setup 生成新密钥替代旧密钥。保留 review/禁止重试记录，先查询原远端付款结果，绝不自动重付。先在隔离目录演练恢复；原部署数据保持可回滚。

## 文件布局与验证边界

`config/` 设置与入口；`gift/` 页面、状态机、独立集成与命令；`tests/` 自动验证；`deploy/` 服务、PowerShell（`quickstart.ps1` 一键起步、`restore.ps1` 离线恢复）、Caddy、备份；`docs/` [零基础教程](docs/新手部署教程.md)、[方案](docs/spec.md)、[计划](docs/plan.md)、[真实集成限制](docs/live-integrations.md)、[补单限制](docs/recovery.md)。运行时 `.venv/`、`private/`、`work/`、`staticfiles/`、数据库、key、env均忽略，不应打包进交付源码。已有的 `docs/*preview.png` 是清除凭据的模拟页面截图。

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe manage.py check
```

Windows 本机普通进程已验证含空格环境路径、DEBUG=False 静态资源、运营生成/移动/授权 POST 导出、浏览器兑换→worker模拟成功→刷新、正常停止与一致数据库/密钥备份。模拟成功只证明本地流程。`check --deploy` 在安全模拟配置下已无警告；本地纯 HTTP 默认设置会报告 HTTPS/cookie/HSTS 提示，应按上面的生产配置部署。

**真实能力边界**：真实 worker 可创建付款链接、查询已知订单；单笔付款需要管理员即时输入、只驻留内存的 CVC 等资料，不持久化 CVC/银行验证码，不支持无人值守保存卡片付款。真实批量 pay 预览不开放，失效链接真实替换仍缺少商户明确未付款证据；不能绕过这些限制。真实只读与真实赠送付款验收需要用户提供有效配置、明确测试订单/金额及授权。本次未连接 X/商户、未付款，目标服务器服务/账户/HTTPS/外网验收仍未验证。

## 项目来源与许可

本教程及其配套的 Python/Django 项目在功能设计和部分真实接口适配方面参考了 [mizorewww/x_gift_bot](https://github.com/mizorewww/x_gift_bot)。其中部分 X、Stripe 适配逻辑依据上游 MIT 许可源码改写。本项目并非上游项目的官方 Python 版本。

本目录中由本仓库作者编写的教程和代码采用 MIT 许可，详见 [LICENSE](LICENSE)。上游项目的版权及 MIT 许可声明保存在 [LICENSE.upstream](LICENSE.upstream)。感谢上游作者 mizorewww 公开项目和实现。

上述许可说明仅适用于 `projects/xgift-python/`。本仓库中的其他项目分别声明其来源与许可。
