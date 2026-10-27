# NOTICE / 修改声明

我们基于 **LION** 修改了这个 NVDA 插件，与 NV Access 及原版作者没有隶属关系。

## 上游信息

| 项 | 值 |
|---|---|
| 上游仓库 | https://github.com/anmi2004/LION |
| 核对提交 | `216e3e4091fd112aff3da088a677831e96081c13` |
| 原版作者 | Stefan Moisei、Cary-rowen、anmi2004、hwf1324 |
| 上游许可 | **GPL-2.0-or-later** |
| 协议全文 | [COPYING.txt](COPYING.txt)（GNU GPL v2 全文） |

## 修改声明（GPLv2 §2(a)）

我们基于上游 LION 修改，主要修改包括：

- 识别后端由内置 PaddleOCR-json 改为**外部 PaddleOCR HTTP 服务**（`ocrClient.py`）；
- 多识别区域、分区去重策略、排队朗读（完整版）；
- 自定义模型/OCR 端点与模型列表发现；
- 界面文案精简；
- 清亮版 lite：单区域、原版式界面，客户端与完整版相同，作为独立插件发布；
- 移除上游内置的 PaddleOCR-json 引擎与未使用的捆绑 PIL。

修改者：**xiaoshunxing**（GitHub：[@xiaoshunxing](https://github.com/xiaoshunxing)）。

## 文件级标注

- 新增/重写文件（无上游对应或已大幅重写）：`ocrClient.py`、`regions.py`、`dedup.py`、
  `variants/lite/*`、`scripts/*`、`tests/*`、`docs/*`；
- 被修改的上游文件：`__init__.py`、`lionGui.py`、`manifest.ini`、`doc/*`、`locale/*`；
- 被修改的上游文件保留原版权头，并在本文件中列出修改内容。

## 依赖与许可

| 组件 | 许可 | 与插件的关系 |
|---|---|---|
| LION（NVDA 插件，anmi2004/LION） | GPL-2.0-or-later | 本插件的上游；修改后仍按 GPL-2.0-or-later 分发 |
| NVDA（NV Access） | GPL-2.0-or-later | 运行宿主；插件只调用其公开 API |
| Pillow（PIL） | HPND / PIL license | **不打包**；运行时使用 NVDA 自带的 PIL 截图/编码 |
| configobj（vendored，仅测试用） | BSD-3-Clause | `tests/vendor/configobj/`，只用于离线测试解析 manifest/confspec，**不进入插件包**；许可见 `tests/vendor/configobj/LICENSE` |
| PaddleOCR / PaddleX | Apache-2.0 | 外部 HTTP 服务，**不随插件分发**；由你自行部署 |
| Python 标准库 | PSF License | 插件只依赖标准库 |

## 源码提供

本插件的对应源码位于：

- 本仓库：https://github.com/xiaoshunxing/LION-modified
- GitHub Releases 中的对应 tag 源码压缩包（与安装包同一版本）

再分发本修改版（或其衍生作品）时请：

1. 附带 `COPYING.txt`（GPL-2.0 全文）与本声明；
2. 保留上游作者与我们的版权/署名信息；
3. 若继续修改，在显著位置注明修改内容与日期。

## 免责声明

本插件基于 LION 修改，与 NV Access 及原版作者没有隶属关系；按 GPL 协议“不提供担保”分发。

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
