# Contributing

欢迎提交 issue 和 pull request。提交代码即表示同意以我们的 **GPL-2.0-or-later** 许可发布你的贡献。

## 开发环境

- Windows + PowerShell 7（`pwsh`）；
- Python 3.12 或更高；
- NVDA 2026.2（做安装/面板冒烟测试时需要）；
- 推荐在本机部署 PaddleOCR HTTP 服务；在线测试示例地址 `http://127.0.0.1:23353`，请按实际部署修改。

## 测试

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -p 'test_*.py'
```

- 完全离线：`$env:LION_SKIP_SERVICE_PROBE = '1'`、`$env:LION_TEST_LIVE = '0'`；
- 在线测试令牌：`LION_TEST_TOKEN` 或 `LION_TEST_TOKEN_FILE`；
- 令牌、个人路径、`tests/artifacts/` 和 `dist/` 不要提交。

## 打包

```powershell
pwsh -File scripts\build_addon.ps1 -Variant full -Python python
pwsh -File scripts\build_addon.ps1 -Variant lite -Python python
```

也可以用 `$env:LION_PYTHON` 指定解释器。产物写入 `dist/`，不进入 git。

## 代码风格

- Python 使用 **tab** 缩进，与现有文件一致；
- 注释与界面文案以中文为主，英文文档同步更新；
- 不引入新的运行时依赖；插件只依赖 Python 标准库和 NVDA 自带库；
- 修改 `src/` 时，同步检查 `variants/lite/src/`（lite 的客户端从完整版复制，不存副本）；
- 修改界面字符串时，同步更新 `.po`，并重新编译 `.mo`。

## 翻译

1. 编辑 `src/locale/zh_CN/LC_MESSAGES/nvda.po`（lite 对应 `variants/lite/src/locale/...`）；
2. 用 `Tools/i18n/msgfmt.py` 重新生成 `nvda.mo`；
3. 运行测试，确认 `test_manifest_and_config` / `test_lite_variant` 的翻译覆盖用例通过。

## 版本号

- 主版本沿用上游 **3.3**；
- 修改版用 **3.3.N** 递增，当前 **3.3.7**；
- 版本变更要同步 `manifest.ini`、locale manifest、readme、docs 与 `test_package.EXPECTED_VERSION`。

发布步骤见 [docs/发布流程.md](docs/发布流程.md)。

## Pull request

- 先开 issue 说明问题/需求，再提交 PR；
- PR 描述写清改了什么、怎么验证、是否影响打包或翻译；
- 确保本地测试通过；
- 不要提交令牌、个人绝对路径、`dist/`、`tests/artifacts/`、`build/` 或 `__pycache__/`；
- 不要改变插件 ID、配置节、上游作者署名与 GPL 许可。

## 安全问题

请不要在公开 issue 里贴令牌或敏感日志，按 [SECURITY.md](SECURITY.md) 私下报告。

AI 创作声明：本文案由 AI 参与创作。

代码声明：本修改版代码在原版 LION 基础上由 AI 参与编写。

模型版本：DeepSeek-V4.1-Flash。
