# XGift Python Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 从零交付可部署在 Windows 的 Python X Premium 兑换网站。

**Architecture:** Django 页面和接口共用数据库业务服务，独立 worker 领取持久化任务。X/支付模块隔离于订单状态机，模拟模式使用相同状态机。Waitress 和 worker 由两个 Windows 服务运行，Caddy 提供 HTTPS。

**Tech Stack:** Python、Django、SQLite、cryptography、HTTPX（含 SOCKS 支持）、Waitress、pytest-django、pywin32、Caddy。实施时核实受支持版本并锁定依赖。

**Spec:** [项目方案](spec.md)。

## Global Constraints

- 部署目标为 Windows，运行时不依赖 Go 或 Node.js。
- 从零开始，不迁移旧数据库。
- 银行验证码与 CVC 不持久化；日志不得包含凭据或完整卡号。
- 付款提交间隔至少 30 秒；未知结果不能自动重新提交。
- 服务重启不自动继续扣款；模拟成功不代表真实赠送验证通过。
- 真实付款验收必须另有明确指定的测试订单和金额。

## Review Focus

1. 相同兑换码同时提交：只创建一个有效订单（任务 2）。
2. 付款已发出但本地未保存结果：重启转待核实，不重付（任务 3）。
3. 无权访问其他客户：不可猜测令牌不替代权限检查（任务 4）。
4. 主密钥损坏或丢失：拒绝解密，不覆盖既有配置（任务 1）。
5. Windows 路径有空格、服务中断或备份恢复：正确启动并保留待核实状态（任务 7）。

## 文件结构

项目位于 `outputs/xgift-python/`，原仓库只读参考位于 `work/upstream/`，基准提交为 `ea9df0eca7e1d1485a0f0681278530082366acaa`。

- `config/{settings,urls,wsgi}.py`：站点配置和入口；`manage.py`、`pyproject.toml`、依赖锁文件。
- `gift/{models,admin,urls}.py`、`gift/migrations/`：数据、管理员入口和路由。
- `gift/services/{vault,codes,orders,queue,recovery,catalog}.py`：加密、兑换和订单业务。
- `gift/integrations/{types,simulated,x_client,stripe_client,routes}.py`：外部调用边界。
- `gift/views/{public,operator}.py`、`gift/forms.py`、`gift/templates/gift/`、`gift/static/gift/`：页面和接口。
- `gift/management/commands/{setup_xgift,run_worker,serve_xgift}.py`：配置、worker、网站运行。
- `deploy/{windows_service.py,install.ps1,start.ps1,stop.ps1,backup.ps1,Caddyfile}`：Windows 部署。
- `tests/test_*.py`、`.env.example`、`README.md`：测试与使用说明。

## Task 1：可启动站点与加密配置

**Files:** `config/`、`manage.py`、`pyproject.toml`、`gift/models.py`、`gift/services/vault.py`、`gift/management/commands/setup_xgift.py`、`tests/test_setup.py`、`tests/test_vault.py`。

**Interfaces:** `seal(value: bytes, purpose: str) -> bytes`、`unseal(payload: bytes, purpose: str) -> bytes`；Secret 保存名称与密文；站点 `/healthz` 返回 `{"ok": true}`。

- [x] 写失败测试：`test_healthz` 断言 200 和 ok=true；`test_vault_wrong_key` 断言解密失败且旧密文未被修改；`test_secret_not_in_logs` 断言日志中无测试凭据。
- [x] 运行 `python -m pytest tests/test_setup.py tests/test_vault.py -q`，确认目标行为未实现导致失败。
- [x] 建立 Django 项目及数据库迁移；用 AES-GCM、随机 nonce 和 purpose 关联数据实现接口；初始化生成独立密钥与管理员，重复初始化不得覆盖。
- [x] 运行上述测试及 `python manage.py check`，预期全通过、无系统检查错误；记录验证结果并提交此任务。

## Task 2：兑换码、套餐、批次和订单模型

**Files:** `gift/models.py`、`gift/migrations/`、`gift/services/{codes,catalog,orders}.py`、`tests/test_codes.py`。

**Interfaces:** `generate_codes(plan_id: int, count: int, folder_id: int | None) -> list[str]`；`redeem(code: str, username: str) -> Order`；模型 Plan、Folder、Code、Order、AuditEvent。Order 包含 UUID、访问令牌摘要、套餐快照、客户、状态、远端会话 ID。

- [x] 写失败测试：同码同客户重复调用返回相同订单 ID；同码不同客户拒绝；停用码不创建订单；文件数据库上的并发提交只产生一个订单；生成码数据库不含明文且授权解密等于原码。
- [x] 运行 `python -m pytest tests/test_codes.py -q`，确认失败。
- [x] 实现高熵码、摘要查找、加密副本、用户名标准化、数量边界、原子兑换和唯一约束；金额用整数最小货币单位，创建订单冻结套餐。
- [x] 再运行测试，预期全部通过；记录验证结果并提交。

## Task 3：持久化任务与模拟赠送

**Files:** `gift/integrations/{types,simulated}.py`、`gift/services/{orders,queue}.py`、`gift/management/commands/run_worker.py`、`tests/test_worker.py`、`tests/test_payment_state.py`。

**Interfaces:** Provider 的 `eligibility(username: str) -> str`、`quote(recipient: str, plan: PlanSnapshot) -> Quote`、`create_checkout(order: OrderSnapshot) -> Checkout`、`inspect(checkout: Checkout) -> PaymentResult`、`confirm(checkout: Checkout, payment_method: str, key: str) -> PaymentResult`；上述不可变类型均定义在 `types.py`。`process_next(provider: Provider) -> bool`、`reconcile(order_id: UUID, provider: Provider) -> Order`。

- [x] 写失败测试：两个 worker 仅一个领取任务；模拟完整兑换到 success；确认超时进入 review；模拟提交后进程崩溃再恢复 confirm 调用总数仍为 1；任意两次付款调用间隔至少 30 秒；网络调用期间数据库能完成独立短事务。
- [x] 运行 `python -m pytest tests/test_worker.py tests/test_payment_state.py -q`，确认失败。
- [x] 建立任务租约、原子领取、稳定幂等键、持久化付款记录及全局锁。提交前记录不可自动重放标记；恢复时已进入提交边界的订单仅查询。模拟 Provider 可注入拒付、超时和未知结果；测试使用可控时钟。
- [x] 重跑测试，预期全部通过；记录验证结果并提交。

## Task 4：用户页面与运营后台

**Files:** `gift/views/`、`gift/forms.py`、`gift/urls.py`、`gift/admin.py`、模板与静态资源、`tests/test_public.py`、`tests/test_operator.py`。

**Interfaces:** `POST /api/redeem` 返回订单访问令牌；`GET /api/orders/<uuid>` 验证对应令牌后返回经过过滤的状态；`/admin/` 管理套餐、批次、兑换码、客户和统计。

- [x] 写失败测试：无效码与用户名返回字段错误；其他订单的令牌返回拒绝；匿名用户不能导出或查看卡密；带 CSRF 验证的后台写入拒绝无 token 请求；限流生效；非工作人员不能查看敏感配置。
- [x] 运行 `python -m pytest tests/test_public.py tests/test_operator.py -q`，确认失败。
- [x] 实现中文响应式兑换页、状态轮询和后台操作；会话登录、CSRF、限流、脱敏输出；敏感操作记录审计，查看明文与导出均严格授权。
- [x] 运行测试并在浏览器完成模拟兑换、批次移动和导出，预期流程可用；记录验证结果并提交。

## Task 5：真实 X、支付和代理适配

**Files:** `gift/integrations/{x_client,stripe_client,routes}.py`、`gift/services/catalog.py`、`tests/test_x_client.py`、`tests/test_stripe_client.py`、`tests/test_routes.py`。

**Consumes/Produces:** 消费任务 3 的 Provider 与类型；实现真实 Provider；配置每次流程加载并校验，禁止用模拟配置执行真实付款。

- [x] 写失败测试：X Cookie 无效拒绝；账户资格失败不创建链接；报价币种/金额不符拒绝；付款商户/套餐/客户/金额任一不符不 confirm；confirm 超时不重放；禁止重试标记保持；X 资格检查直连、报价和链接使用相同出口；HTTP/SOCKS 配置与节点冷却可复现。
- [x] 运行 `python -m pytest tests/test_x_client.py tests/test_stripe_client.py tests/test_routes.py -q`，确认失败。
- [x] 对照基准源码移植必要请求、响应解析、报价与会话核验。使用本地 HTTP 模拟响应验证，不调用真实付款。区分只读查询与写请求，禁止隐式环境代理和意外重定向。
- [x] 核实原支付流程要求的 CVC/token 生命周期：仅接收内存中的即时付款资料或使用已验证可复用的支付方式；如果该商户必须每单重新提供 CVC，则采用管理员即时输入并明确报告无人值守限制，不持久化 CVC，不伪造 token 可复用能力。
- [x] 重跑测试，预期全通过；真实资格/报价等只读验证需已有用户配置；未验证项单独记录，不声称真实赠送已通过；提交此任务。

## Task 6：公开链接排队与人工补单

**Files:** `gift/services/{queue,recovery}.py`、`gift/views/{public,operator}.py`、相关模板、`tests/test_link_queue.py`、`tests/test_recovery.py`。

**Interfaces:** `preview_recovery(order_ids: list[UUID], mode: str) -> RecoveryPreview`；`start_recovery(preview_id: UUID, actor_id: int) -> RecoveryBatch`；`stop_recovery(batch_id: UUID) -> None`。模式为 links 或 pay；预览有效期 10 分钟。

- [x] 写失败测试：公开队列取消不付款；其他客户不可读取队列票据；links 模式 confirm 次数为 0；预览过期/配置改变拒绝启动；未知结果、禁止重试订单不能通过补单重付；停止只阻止后续订单；重启批次为 interrupted。
- [x] 运行 `python -m pytest tests/test_link_queue.py tests/test_recovery.py -q`，确认失败。
- [x] 实现持久化队列、预览快照、确认授权和批次处理。复用任务 3 的锁和付款边界；替换链接保留原会话、明确失败证据和人工未扣款确认，每原会话最多 3 次人工重试。
- [x] 重跑测试并浏览器验证预览/停止与客户查询，预期全通过；记录验证结果并提交。

## Task 7：Windows 部署、备份与整体验收

**Files:** `gift/management/commands/serve_xgift.py`、`deploy/`、`.env.example`、`README.md`、`tests/test_deployment.py`。

**Interfaces:** `python manage.py serve_xgift` 使用 Waitress 监听 127.0.0.1；pywin32 包装网站和 worker 为两个服务；PowerShell 脚本以 LiteralPath 和参数数组处理路径。

- [x] 写失败测试：含空格路径配置正确解析；缺少真实配置不启用真实 Provider；付款中断后备份/恢复保持 review；备份包含一致数据库快照和密钥且不会把明文凭据写日志。
- [x] 运行 `python -m pytest tests/test_deployment.py -q`，确认失败。
- [x] 编写服务包装、安装启动停止脚本、Caddy 模板和备份恢复说明；服务账户限制数据与密钥 ACL；默认模拟模式；生产关闭 DEBUG 并设置域名、HTTPS 和可信来源。
- [x] 运行 `python -m pytest -q`、`python manage.py check`、`python manage.py check --deploy`，解释仅与本地模拟环境有关的配置提示。
- [x] 在当前 Windows 环境启动网站与 worker，完成浏览器模拟兑换及重启恢复；服务注册若无管理员权限则明确记录未验证，不能把普通进程启动描述为服务验收。
- [x] 生成交付说明，区分模拟验收、真实只读验收和真实付款验收；记录验证结果并提交。

## 执行与审阅

推荐在当前会话由主助手逐任务实施，保持状态机与接口的一致性。真实支付资料是否支持无人值守需以任务 5 的证据为准，不影响先完成完整模拟站点与 Windows 部署。

本计划覆盖已确认方案；用户审阅并选择当前会话实施或子代理实施后开始编写产品代码。


## 交付验收说明（2026-10-11）

任务 1–7 实现/自动测试/本地模拟已完成；上面勾选表示有任务报告证据，并不表示真实环境通过。Task5 真实只读/真实付款、Task6 真实替换和批量付款、Task7 用户服务器系统服务及外网HTTPS未验收。任务7服务注册按授权边界未在开发机执行，普通进程已验证。最终独立审阅仍待主助手执行。
