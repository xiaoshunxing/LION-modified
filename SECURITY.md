# Security Policy

## 支持范围

当前支持 **3.3.x** 公开版本；当前版本为 **3.3.8**。

## 报告安全问题

请通过 GitHub 仓库的 **Security → Report a vulnerability** 私下报告，不要开公开 issue。

报告时请提供：

- 受影响的版本与插件（完整版 / lite）；
- 复现步骤；
- 影响说明；
- 必要的日志，但**不要包含服务令牌、个人路径或其他敏感信息**。

## 令牌与网络

- 插件只访问你在设置里配置的 OCR 服务地址，不做遥测或其它外发请求；
- 服务令牌保存在 NVDA 配置中，由你在本地管理；不要提交到仓库或粘贴到 issue；
- 如果服务监听局域网地址，请用防火墙和令牌保护，并评估网络暴露面；
- 如果怀疑令牌泄漏，请在服务端更换令牌，并更新 NVDA 设置。

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
