# 真实适配边界与管理员付款

运行模式由唯一配置 `XGIFT_MODE=simulated|live` 决定；其他值拒绝启动 worker。订单建立时冻结模式，数据库与模型禁止修改。切换站点模式不转换已有订单；API/订单列表显示原订单模式。升级前所有已有订单按模拟标记，因为此前唯一 worker 为模拟实现。

默认 simulated，不连接 X/Stripe。live worker 每次工作重新读取和验证管理后台保存的 x-auth、stripe-key、proxy 加密配置。缺少 X 会话/支付发布密钥时停止，不回退到模拟成功。常规 live worker 只核验、建立付款链接、查询结果；没有付款卡资料时进入等待付款。

X 用户资格通过直连检查；报价和链接创建共用同一配置 x_region HTTP/SOCKS 出口。环境代理和重定向关闭。代理池仅使用明确配置的 HTTP/HTTPS/SOCKS5/SOCKS5H；其他协议需外部代理转换。支付节点发生传输错误后在当前 worker 进程内冷却 60 秒。只读 Stripe 查询/init 最多尝试三次，可切换可用节点；tokenization/confirm 和 X 创建链接均绝不重放。节点冷却不是持久化的跨进程调度。没有经过真实环境验证的卡池轮换/付款节点轮换承诺。

管理员可在可信服务器交互控制台运行：

```
.venv\Scripts\python.exe manage.py pay_order <完整订单UUID>
```

只允许明确指定的 live 排队/链接已创建/等待付款订单。控制台显示冻结金额（最小货币单位）、商户、客户与月数，要求再次输入完整订单 ID 授权，然后通过隐藏输入采集卡号、有效期、CVC、账单姓名/邮箱。禁止命令行参数传卡资料，非交互输入拒绝。资料仅用于该订单的一次 tokenization/confirm，结束后释放字典引用，永不存入数据库、日志或配置（Python 字符串无法承诺物理内存归零）。这是可信本机管理员流程，不是网站用户付款接口。

源码的商户页面明确拒绝 saved-card/setup/recurring，tokenization 每单需要 CVC，因此未实现也未宣称无人值守卡付款或复用 payment_method。付款前持久化禁止重试标记、稳定幂等键及付款锁；卡 tokenization 也在这个保守边界之后。失败/超时、银行交互验证或不完整结果进入待核实，禁止自动重复付款。一个授权 provider 无法用于常规多订单 worker。需要只读核实已有付款尝试时，使用 `manage.py reconcile_order <订单UUID>`；它仅查询原订单，模式不匹配会拒绝。

Stripe 在付款前完整核验 session、merchant、客户成功/取消地址、live 单次付款模式、套餐/月数、数量、币种、单价、小计、总额、到期金额、checksum 与明确 null payment_intent。完成结果必须包含完整价格/客户绑定以及正确 received amount。已完成 Checkout 的 init 若不可用，或仅返回缩减结果，首版保守进入待核实；不会用缺乏绑定的缩减 payload 宣称成功。目前没有持久化完整 Stripe completion proof，因此人工浏览器付款的远端成功可能仍需管理员核对。

X GraphQL IDs 来自上游提交 ea9df0e，属于非公开接口快照。验收证据仅为 HTTPX MockTransport 本地响应验证，未使用真实账户凭据或真实付款。开发中一个预检测试曾误用默认客户端，可能向 X 报价查询发出带合成凭据的只读请求；等待后立即中止，未获得可用验证结果。测试现已统一阻断非 MockTransport 的 HTTPX 请求。真实账号资格、区域价格、3DS/银行交互、真实扣款/退款、节点地理一致性和持续 API 兼容性均未验证。真实验收必须由用户配置账户并明确授权指定订单和金额。

适配器参考 mizorewww/xgift 的 MIT 源码；版权与完整许可保存在 LICENSE.upstream。

创建付款链接前会持久化不可逆 `checkout_creation_started` 审计标记。进程在远端接受请求后、本地保存 session 前中断时，后续 worker 或即时付款命令将订单置为待核实，绝不创建第二条链接。标记不会由管理员付款流程清除；替换链接必须经过独立审计和明确核验流程。
