# LION 修改版（LION modified）

[![tests](https://github.com/xiaoshunxing/LION-modified/actions/workflows/tests.yml/badge.svg)](https://github.com/xiaoshunxing/LION-modified/actions/workflows/tests.yml)
[![license: GPL-2.0-or-later](https://img.shields.io/badge/license-GPL--2.0--or--later-blue.svg)](COPYING.txt)

我们基于 [LION](https://github.com/anmi2004/LION) 修改了识别后端：从内置 PaddleOCR-json 换成
**外部 PaddleOCR HTTP 服务**，并加入多区域识别、分区去重、排队朗读、自定义模型/端点。
本插件与 NV Access 及原版作者没有隶属关系；许可为 **GPL-2.0-or-later**。

本仓库首个 GitHub 发布为 **3.3.7**；公开版本迭代为 **3.3.1–3.3.7**，当前版本 **3.3.7**。
完整变更见 [CHANGELOG.md](CHANGELOG.md)，Release 页面附有历史版本摘要。

## 两个版本

| | 完整版 `LION-modified` | lite `LION-modified-lite` |
|---|---|---|
| 识别区域 | 多区域，可勾选、排序 | 单区域，接近原版 |
| 去重 | 经典 / 稳定后朗读 / 跟读增量 / 关闭 | 经典相似度 |
| 排队朗读 | 有 | 无 |
| 设置项 | 多 | 少，接近原版 |
| 客户端能力 | 相同：模型/端点、模型列表、PaddleOCR-VL 文本提取 | 相同 |

两个插件可以同时安装（ID、包名、配置节都不同）；默认热键相同，给其中一个换键即可。

## 功能

- 定时识别屏幕指定区域，只朗读变化明显的文本；适用于字幕、游戏对话和无法直接读取的界面文字。
- 完整版另有多区域、分区去重和排队朗读，差异见上表。
- 外部服务：服务地址 + 模型/端点，支持常见端点或自定义 URL。
- “测试 / 获取模型”：探活 → 模型列表 → 令牌校验 → 快端点识别测试。

## 环境要求

- NVDA 2026.1 或更高（2026.2 已测试）。
- 推荐在本机部署 PaddleOCR HTTP 服务：延迟低，令牌不必出本机。
  下文以 `http://127.0.0.1:23353` 为例；请按实际部署替换地址、端点和令牌。
  - `/ocr`：常见快速端点示例，适合屏幕字幕；
  - `/layout-parsing`：文档解析端点示例，较慢，需要加大请求超时；
  - `/ocr-acc`、自定义端点或完整 URL 也可以。
- 本插件不自带识别引擎、模型或 Pillow；截图与编码使用 NVDA 自带的库。

## 安装

1. 打开 [Releases](https://github.com/xiaoshunxing/LION-modified/releases)，下载最新的 `.nvda-addon`。
2. 在 NVDA 中打开该文件安装，重启 NVDA。
3. 打开 NVDA 菜单 → 选项 → 设置 → **LION 修改版**，填写服务地址、端点与令牌。

详细说明见 [中文手册](src/doc/zh_CN/readme.md) / [English guide](src/doc/en/readme.md)。

## 构建与测试

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -p 'test_*.py'

# 指定 Python 解释器（也可用 LION_PYTHON 环境变量）
pwsh -File scripts\build_addon.ps1 -Variant full -Python python
pwsh -File scripts\build_addon.ps1 -Variant lite -Python python
```

- 离线测试：`$env:LION_SKIP_SERVICE_PROBE = '1'`、`$env:LION_TEST_LIVE = '0'`。
- 在线测试令牌：`LION_TEST_TOKEN` 或 `LION_TEST_TOKEN_FILE`；不要把令牌写进仓库。

## 目录

```text
src/                 完整版插件源（manifest、globalPlugins/lion、doc、locale）
variants/lite/src/   清亮版插件源（globalPlugins/lionlite；客户端打包时从完整版复制）
scripts/             构建与文档生成脚本
tests/               离线测试、NVDA 桩、vendored configobj
docs/                开发文档与发布说明
```

开发文档：[中文](docs/开发文档-中文.md) / [English](docs/DevGuide-EN.md)。

## 许可与致谢

- 基于 [LION](https://github.com/anmi2004/LION)（作者：Stefan Moisei、Cary-rowen、anmi2004、hwf1324）。
- 许可：**GPL-2.0-or-later**，完整协议见 [COPYING.txt](COPYING.txt)；修改说明与许可信息见 [NOTICE.md](NOTICE.md)。
- 修改版作者：**xiaoshunxing**（GitHub：[@xiaoshunxing](https://github.com/xiaoshunxing)）。
- 测试用的 vendored configobj 为 BSD-3-Clause，仅用于测试，不进入插件包。
- 插件不包含 OCR 引擎、模型或其他二进制依赖。

## 免责声明

本插件基于 LION 修改，与 NV Access 及原版作者没有隶属关系；按 GPL 协议“不提供担保”分发。

## English summary

LION modified is our fork of [LION](https://github.com/anmi2004/LION) for NVDA. It keeps the screen-reader
workflow but talks to an external PaddleOCR HTTP service instead of bundling an OCR engine. It adds multiple
OCR areas, per-area de-duplication strategies, queued speech, and configurable models/endpoints. Two add-ons
are provided: the full `LION-modified` and the simpler `LION-modified-lite`. Licensed under
**GPL-2.0-or-later**; see [COPYING.txt](COPYING.txt) and [NOTICE.md](NOTICE.md). We are not affiliated with
NV Access or the original authors.

AI 创作声明：本文案由 AI 参与创作。

代码声明：本修改版代码在原版 LION 基础上由 AI 参与编写。

模型版本：DeepSeek-V4.1-Flash。

详见 [AI-DISCLOSURE.md](AI-DISCLOSURE.md)。

AI disclosure: This text was created with AI participation.

Code disclosure: The modified code is based on the original LION and was written with AI participation.

Model version: DeepSeek-V4.1-Flash.
