# 小白部署教程系列

给第一次部署网站的人准备的图文教程。每篇教程附带对应的项目源码，按顺序完成本地体验、服务器部署和上线验收。

## 教程目录

| 篇目 | 适合谁 | 入口 |
| --- | --- | --- |
| 01 · X Premium 礼品兑换站（Windows） | 想先用模拟模式体验，再学习公网和真实模式部署 | [从零开始看教程](projects/xgift-python/docs/新手部署教程.md) · [查看项目说明](projects/xgift-python/README.md) |

后续教程会放在 `projects/` 下，每个项目保留自己的源码、截图和部署说明。

## 怎么开始

点击上方教程入口。想直接下载文件，可点仓库右上角 **Code → Download ZIP**；第一篇项目位于 `projects/xgift-python/`，请把该文件夹完整复制出来再按教程操作。

第一篇默认是**模拟模式**，不连接 X 或支付服务。真实接口代码尚未使用真实账户、真实扣款和目标服务器完成端到端验收；相关条件与单笔验收步骤见教程第 14 章。请勿把真实会话、卡资料、数据库、主密钥或服务器配置提交到仓库。

## 项目来源与许可

第一篇教程及其配套项目参考了 [mizorewww/x_gift_bot](https://github.com/mizorewww/x_gift_bot)；部分真实接口适配逻辑依据上游 MIT 许可源码改写。`projects/xgift-python/` 中由本仓库作者编写的教程和代码采用 MIT 许可，详见该目录的 [LICENSE](projects/xgift-python/LICENSE)；上游版权及许可声明保存在 [LICENSE.upstream](projects/xgift-python/LICENSE.upstream)。此许可说明仅适用于第一篇项目，后续项目分别声明来源与许可。
