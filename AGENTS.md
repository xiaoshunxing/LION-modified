# AGENTS.md — LION 修改版维护工作流与代理规则

> 本文件是本仓库**唯一权威的维护/发布流程入口**，面向维护者与自动化编码代理。
> 开始任何修改前，先读本文件；再按需读 [开发文档](docs/开发文档-中文.md) 与 [发布流程](docs/发布流程.md)。
> 命令默认在仓库根目录执行，使用 PowerShell 7（`pwsh`）。本文件不绑定具体版本号，`<version>` 表示待发布版本。

---

## 1. 项目事实与结构

- NVDA 全局插件：完整版 `LION-modified` 与 lite `LION-modified-lite`，共用同一发布号；两个插件可同时安装。
- 完整版源码：`src/globalPlugins/lion/`；lite 源码：`variants/lite/src/globalPlugins/lionlite/`。
- lite 的 `ocrClient.py` **不保存副本**：打包时从完整版复制到 staging（`build/lite/`）。
- 识别后端是**外部 PaddleOCR HTTP 服务**（默认示例 `http://127.0.0.1:23353`），插件不内置引擎、模型或 PIL。
- 配置节：`LION-modified` / `LION-modified-lite`，与原版 LION 的 `lion` 完全独立。
- 最低 NVDA 2026.1；当前 `lastTestedNVDAVersion = 2026.3`（以 [src/manifest.ini](src/manifest.ini) 为准）。
- 许可 GPL-2.0-or-later；保留上游作者与 [NOTICE.md](NOTICE.md)、[COPYING.txt](COPYING.txt)。
- 产物：`dist/*.nvda-addon` 与 `*.files.txt`；`dist/`、`build/`、`tests/artifacts/` 均不入库。

## 2. 硬性约束（违反即需返工）

- **不改变**插件 ID、配置节名、上游作者署名、GPL 许可；不新增运行时依赖（只用标准库 + NVDA 自带库）。
- 不提交：令牌、`vscode`/本机绝对路径、`dist/`、`build/`、`tests/artifacts/`、`__pycache__`、日志。
- Python 代码用 **tab** 缩进，注释以中文为主；新增/修改界面字符串必须可翻译。
- 改 `src/` 时同步检查 `variants/lite/src/`，保持完整版与 lite 行为/客户端一致。
- `installTasks.py` 必须自包含：内联 `SPEC`、**不 import `globalPlugins.*`**、不写 `onUninstall`、不调用 `unregisterSection`。
- 配置键增删时，两个变体的 `installTasks.py` 的 `SPEC` 与插件 `confspec` 必须同步（测试用 AST 断言）。
- 界面字符串改动必须同步 `.po` 并重编 `.mo`；`Project-Id-Version` 随版本更新。
- **公开文档不得恢复旧的三行 AI 声明**（关于 AI 参与、代码与模型标注的字面行）；统一使用 dsh 徽章：
  - Markdown 文档：Shields.io 官方链接徽章（见第 7 节）；
  - 插件手册：本地官方 PNG 副本 `../dsh-badge.png`（由 `doc/dsh-badge.png` 随包分发）；
  - `manifest.ini` / locale manifest：纯文本等价行 `Powered by dsh — https://github.com/deepseek-ai/deepseek-harness`。
- 徽章样式、配色、Logo、项目链接**不得改写**；不得自行重绘。
- 默认**只本地提交**；`git push`、GitHub Release、内部归档复制都必须得到明确批准。
- 已发布的 tag 不移动；需要修正就发新的补丁版本。

## 3. 固定工作流（0–15 步）

### 0. 读取与计划

- 读本文件 → [开发文档](docs/开发文档-中文.md)（架构/测试/参数）→ [发布流程](docs/发布流程.md)（发布细节）。
- 检查：`git status`、`git log --oneline -5`、`git tag --sort=-v:refname | Select-Object -First 3`。
- 确认当前版本（`src/manifest.ini`）、`dist/` 是否有当前版本产物、测试基线、目标 NVDA 版本。
- 大改（跨模块、影响配置格式、发布）先给出计划并获批准，再动代码。

### 1. 影响面盘点

- 变更类型：行为/代码、界面字符串、配置键、去重/阈值、文档、本地化、NVDA 版本兼容、打包/CI。
- 对每类列出：要改的文件、要补的测试、是否影响版本号/翻译/徽章/产物。
- 参考第 5 节矩阵，避免漏改。

### 2. 分工与并行

- 需要多人/多代理时，先建共享任务（subject、description、write scope、依赖），**写域互不重叠**。
- 可并行的典型拆分：完整版代码、lite 代码、中文文档、英文文档、内部归档。
- **打包、提交、tag、发布、最终验证必须由 Lead 单独执行**；teammate 不得打包/提交/动 `dist/`。
- teammate 失败时 Lead 接管其写域并补齐；最终以 Lead 的完整测试与 diff 复核为准。

### 3. 代码实现

- 先完整版，再 lite；共享逻辑不要复制到 lite。
- 异常路径：单区域/单次失败不影响其它区域；退出路径不抛异常；日志用 `logHandler` 且避免刷屏。
- 向后兼容：旧配置自动迁移；新字段给默认值；读取用带默认值的 `get`，避免缺键 KeyError。
- 面板注册/移除保持幂等；轮询等待用可唤醒事件，保证“停止识别”及时生效。

### 4. 配置与兼容专项（NVDA 2026.3+）

- 新增/修改配置键 → 同步 `src/installTasks.py` 与 `variants/lite/src/installTasks.py` 的 `SPEC`（测试断言一致）。
- 配置节固定为基础配置（base-only）：
  - 安装时 `config.configSections.registerSection(..., isBaseOnly=True)`；运行时 `ensureBaseOnlySection()` 兜底；
  - **下次启动生效**；不写 `onUninstall`、不调用 `unregisterSection`；
  - NVDA 2026.1/2026.2 无该 API，保持普通 profile-aware 行为（文档说明）；
  - base-only 生效后忽略并提示旧配置档案里的本插件值，不自动改用户档案。
- 兼容 2026.1 时必须保留 `config.conf.spec[...]` 兜底。

### 5. 去重与逐区域阈值专项

- `classic` 语义：与上次播报文本的相似度**低于**阈值才朗读，**数值越低越严格**。
- 区域可选 `classicThreshold`（0–1 或 `null`=跟随全局 `threshold`）；解析用 `regions.resolveClassicThreshold()`。
- `commit` / `incremental` / `off` **不读**阈值：它们用固定常量与各自的 `dedupHoldMs`。
- 排查“某区域不播报”：确认去重方式、阈值、是否开排队朗读；debug 日志会按区域输出有效阈值与判定结果。
- 改动去重/阈值必须同步 `tests/test_dedup.py`、`tests/test_regions.py`、`tests/test_plugin_integration.py` 与文档。

### 6. 本地化

1. 新界面字符串加入 `src/locale/zh_CN/LC_MESSAGES/nvda.po`（lite 对应 `variants/lite/src/locale/...`）。
2. 更新 `Project-Id-Version` 为当前版本。
3. 重编 `nvda.mo`：优先 `msgfmt.py`；环境没有时用等价的临时 PO→MO 工具（临时脚本放系统临时目录，不进仓库）。
4. 跑翻译覆盖测试：`test_manifest_and_config`、`test_lite_variant`。
5. 修改界面文案后重打两个包。

### 7. 文档与署名

1. 先中文后英文：README、CHANGELOG、`docs/release-notes/v<version>.md`、开发文档、中英手册同步。
2. 手册改动后由打包脚本重新生成 `src/doc/*/readme.html`（不要手改 HTML）。
3. 版本发布说明包含：本版变更、版本迭代摘要、下载链接、环境要求、许可。
4. 署名规则（三形态，见第 2 节）：Markdown 链接徽章；手册本地 PNG 徽章；manifest 纯文本等价行。
5. 例外：上游 LION 原文、GPL/BSD 许可证原文、第三方源码不加徽章（在 [AI-DISCLOSURE.md](AI-DISCLOSURE.md) 说明）。
6. 文档测试会检查徽章、旧声明字面量、链接与计数；改文档后跑完整测试。

### 8. 版本同步

- 版本规则：主版本沿用上游 `3.3`，修改版 `3.3.N` 递增；两个变体共用发布号；tag 形如 `v3.3.N`。
- 发版时同步（漏一处测试会失败）：
  - `src/manifest.ini`、`variants/lite/src/manifest.ini`：`version`、描述标题、`changelog`、`lastTestedNVDAVersion`；
  - 两个 `locale/zh_CN/manifest.ini` 的标题与 changelog；
  - 两个 `.po` 的 `Project-Id-Version`（并重编 `.mo`）；
  - 两份完整版手册与两份 lite 手册的版本引用；
  - `CHANGELOG.md`、`docs/release-notes/v<version>.md`；
  - `README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`docs/发布流程.md`、`docs/开发文档-中文.md`、`docs/DevGuide-EN.md`、issue 模板示例；
  - `tests/test_package.py` 的 `EXPECTED_VERSION`、`tests/test_manifest_and_config.py` 版本断言、`tests/test_lite_variant.py` 的 `LITE_VERSION`；
  - 包内文件清单变化时同步 `NOTICE.md`。
- 改完用 `git grep -n '<旧版本号>'` 全仓库核对，历史版本条目保留。
- `lastTestedNVDAVersion` 只在实际验证新版 NVDA 后提升；最低版本不得随意提高。

### 9. 打包与测试（**先打包，再跑全量测试**）

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\build_addon.ps1 -Variant full -Python python
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\build_addon.ps1 -Variant lite -Python python
```

- 执行策略拒绝未签名脚本时加 `-ExecutionPolicy Bypass`；解释器用 `-Python` 或 `$env:LION_PYTHON` 指定。
- 打包会重新生成 `src/doc/*/readme.html`、创建 `build/lite/` staging，并在 `dist/` 生成两个包与 `.files.txt`。
- **先测试后打包会静默跳过产物用例**（包级 18 项 + lite bundle 4 项），失败不会被发现。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:LION_SKIP_SERVICE_PROBE = '1'
$env:LION_TEST_LIVE = '0'
python -m unittest discover -s tests -p 'test_*.py'
```

- 离线跑；需要在线用例时用 `LION_TEST_TOKEN` / `LION_TEST_TOKEN_FILE`，令牌不得入库。
- 预期：全绿；无 `dist/` 时产物用例整体 skip 属正常，但发布前必须带 `dist/` 再跑一次。
- 产物人工复核：包内无 `PaddleOCR/`、`PIL/`、`__pycache__`、本机绝对路径、令牌、旧署名；`.files.txt` 与包内容一致；体积在预算内；`doc/*/readme.html` 含徽章。

### 10. 提交、tag 与产物

```powershell
git add -A
git status --short      # 确认不含 dist/、build/、tests/artifacts/、__pycache__
git diff --check
git commit -m "Release LION modified <version>"
git tag -a v<version> -m "LION modified <version>"
```

- **amend 之后必须删除并重打 tag**，然后重新生成 source zip 与哈希（tag 必须指向最终提交）。
- 生成源码包与校验值：

```powershell
git archive --format=zip --prefix=LION-modified-<version>/ -o dist/LION-modified-<version>-source.zip v<version>
Get-FileHash -Algorithm SHA256 dist\*.nvda-addon, dist\LION-modified-<version>-source.zip
```

- `dist/SHA256SUMS.txt` 为人工格式：`<大写SHA256>  <文件名>  <字节数> bytes`，每次发版重新生成。
- 校验报告写在 `tests/artifacts/build-report*.json`（入库忽略）。

### 11. 发布（需明确批准）

- `git push origin main`、`git push origin v<version>`（仅在获准时）。
- GitHub Release：标题 `LION 修改版 <version>（完整版 + lite）`，正文用 `docs/release-notes/v<version>.md`；
  附件：两个 `.nvda-addon`、两个 `.files.txt`、source zip、`SHA256SUMS.txt`；勾选 latest。
- 发布后从 Release 重新下载附件核对 SHA256 与体积，确认 CI 在默认分支与 tag 上均通过。
- 历史版本只展示变更、不附旧包。

### 12. 内部归档（仓库外，需批准）

1. 新包与 `.files.txt` 复制到内部资料库 `01-发布物/`，更新其 `SHA256SUMS.txt`。
2. 更新 `历史版本映射.md`；上一版移入 `01-发布物/历史版本/` 并更新该目录清单。
3. 在 `06-执行记录/` 记录提交号、tag、Release id、CI run、哈希、测试结果。
4. 更新 `00-工作总结/` 的状态与时间线；相对路径保持有效，不写本机路径。

### 13. 发布后验证

- 在真实 NVDA 安装两个包（至少完整版 + lite），重启 NVDA。
- 冒烟清单：设置面板出现、`NVDA+Alt+N` 启停、区域编辑/保存、合并区域截图、配置档案切换后设置不变、逐区域阈值生效、中文界面文案、帮助页徽章显示。
- 从 Add-on Store 路径更新插件时确认不残留旧模块；记录验证结果与问题。

### 14. 回滚与热修

- 优先发新的补丁版本；**不移动已发布 tag**。
- 本地回滚：`git revert <commit>` 或修复后重打；撤回未发布 tag 用 `git tag -d`。
- 已发布 Release 有问题：删除/替换附件并重算 `SHA256SUMS.txt`；必要时标记 pre-release。
- `dist/` 与内部归档历史包只增不删，保留可回滚。

### 15. 周期维护

- NVDA 新版本发布 → 查 release notes / 新增 API / 破坏性变更，评估后更新 `lastTestedNVDAVersion`、CI 矩阵与文档。
- 定期跑全量测试（含打包）与 CI；检查依赖零新增、包体积、翻译覆盖。
- 可选后续：自动 Release 工作流、NVDA Add-on Store 提交（需单独申请权限）。
- 流程/命令/发布边界变化时，同步更新本文件、[开发文档](docs/开发文档-中文.md)、[发布流程](docs/发布流程.md) 并跑文档测试。

## 4. 命令速查

```powershell
# 环境
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'      # 关键：避免 src/ 下生成 __pycache__ 导致打包体检失败
$env:LION_SKIP_SERVICE_PROBE = '1'
$env:LION_TEST_LIVE = '0'
$env:LION_PYTHON = '<python 可执行文件路径>'   # PATH 的 python 不可用时

# 打包（先 full、再 lite）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\build_addon.ps1 -Variant full -Python python
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\build_addon.ps1 -Variant lite -Python python

# 全量测试（打包之后）
python -m unittest discover -s tests -p 'test_*.py'

# 单模块测试（调试用）
python -m unittest discover -s tests -p 'test_plugin_integration.py'

# 版本一致性
git grep -n '<旧版本号>'

# 源码包与哈希
git archive --format=zip --prefix=LION-modified-<version>/ -o dist/LION-modified-<version>-source.zip v<version>
Get-FileHash -Algorithm SHA256 dist\LION-modified-<version>.nvda-addon, dist\LION-modified-lite-<version>.nvda-addon, dist\LION-modified-<version>-source.zip
```

## 5. 变更类型与必做事项

| 变更类型 | 必做 |
|---|---|
| 行为/代码（完整版） | 改 `src/globalPlugins/lion/`；补 `tests/test_plugin_integration.py` 等；同步 lite 与文档 |
| lite 行为 | 改 `variants/lite/src/globalPlugins/lionlite/`；补 `tests/test_lite_variant.py`；与完整版行为对齐 |
| 配置键 | 改 confspec + 两个 `installTasks.py` SPEC + GUI + `onSave` + 文档/测试计数 |
| 界面字符串 | 改 `lionGui.py`；同步 `.po`/`Project-Id-Version`/重编 `.mo`；重打两个包 |
| 去重/阈值 | 改 `dedup.py`/`regions.py`/GUI；补 dedup/regions/integration 用例；更新开发文档 |
| 文档/脚本 | 更新本文件、README/CONTRIBUTING、开发文档、发布流程；跑文档测试 |
| 本地化 | 改 `.po`、重编 `.mo`、跑翻译覆盖测试 |
| NVDA 版本 | 更新 `lastTestedNVDAVersion`（两个 manifest）；检查 API/CI；更新文档 |
| 打包/CI | 改 `scripts/build_addon.ps1`、`.github/workflows/tests.yml`；更新测试与文档 |
| 发布 | 按第 8–13 步；需明确批准 |

## 6. 版本、NVDA 兼容与配置节

- 版本：`3.3.N`；两变体共用；`version` 出现在 manifest、locale、手册、测试常量；tag 不移动。
- 兼容：最低 2026.1；2026.3 提供 base-only 配置节 API，2026.1/2 保留旧行为。
- base-only 规则：安装 `registerSection(..., isBaseOnly=True)` + 运行时 `ensureBaseOnlySection()` 兜底；**不调用 `unregisterSection`**（升级首启会误删 YAML）；旧 profile 值忽略并 log。
- 配置格式变更必须迁移旧数据、补迁移测试，并保持默认行为不变。

## 7. 文档、本地化与 dsh 徽章

- 文档语言：中文为准，英文同步（开发文档为 `docs/DevGuide-EN.md`）。
- 手册：`src/doc/zh_CN/readme.md`、`src/doc/en/readme.md` 及 lite 对应文件；HTML 由打包脚本生成。
- dsh 徽章三种形态：
  - Markdown（README、docs、release-notes、Issue 模板等）：
    `[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)`
  - 插件手册：`[![powered by dsh](../dsh-badge.png)](https://github.com/deepseek-ai/deepseek-harness)`（本地官方 PNG，`doc/dsh-badge.png` 随包分发，121×20 展示）
  - manifest/locale manifest：`Powered by dsh — https://github.com/deepseek-ai/deepseek-harness`
- 徽章放在文档末尾；不得改样式/链接；旧的三行 AI 声明不得恢复。
- 例外：上游原文、GPL/BSD 许可原文、第三方源码不加徽章；说明见 [AI-DISCLOSURE.md](AI-DISCLOSURE.md)。

## 8. Agent Teams 协作约定

- Lead 负责：计划、任务拆分（write scopes 互斥）、集成、打包、全量测试、提交/tag、最终答复。
- teammate 负责：自己写域内的实现/测试/文档；**不打包、不提交、不动 `dist/`**；完成后报告改动文件与测试结果。
- 拆分建议：完整版代码 / lite / 中文文档 / 英文文档 / 内部归档；测试文件也分写域。
- 并行期间出现他人文件的暂时失败属正常；各自只跑自己相关模块，最终由 Lead 跑全量。
- teammate 失败或超时：Lead 接管写域补齐；不得让半成品进入提交。
- 反模式：同一文件多人并行写、teammate 打包、打包前跑全量、直接改 `dist/`、未批准 push/Release。

## 9. 发布边界与审批

- 默认只本地提交；`git push`、GitHub Release、内部归档复制、发新版本都必须有明确批准。
- 不发布半成品；不移动已发布 tag；不发未在本地通过全量测试的包。
- 发布前确认：版本一致、两包齐全、全量测试通过、哈希已生成、Release 正文与附件齐全、CI 通过。

## 10. 常见故障与回滚

| 现象 | 处理 |
|---|---|
| `python` 不可用/退出码 9009 | PATH 的 python 可能是 Store 占位；用 `-Python`/`$env:LION_PYTHON` 指定真实解释器 |
| PowerShell 拒绝执行脚本 | 加 `-ExecutionPolicy Bypass` |
| 包级用例全被 skip | 先打包；`dist/` 没有当前版本产物时产物用例整体跳过 |
| 打包体检失败提示 `__pycache__` | 删除 `src/**/__pycache__`；测试与打包时设置 `PYTHONDONTWRITEBYTECODE=1` |
| 中文乱码/翻译缺失 | 改 `.po` 后重编 `.mo`；确认 `Project-Id-Version` 与覆盖测试 |
| 文档测试报徽章/旧声明失败 | 按第 7 节修正，不得恢复旧声明 |
| 版本断言失败 | 按第 8 节全量同步版本号与测试常量 |
| tag 与提交不一致 / 哈希变化 | amend 后重打 tag，重新生成 source zip 与 `SHA256SUMS.txt` |
| Release 附件哈希不符 | 重新上传并从 Release 下载复核，不沿用旧哈希 |
| 配置档案里旧值不生效 | base-only 生效后只读基础配置；重新保存设置；2026.1/2 仍随档案 |
| 并行编辑冲突/覆盖 | 回到写入方复核；按 write scope 重新应用，最终由 Lead 复核 diff |

## 11. 完成标准（DoD）

- 行为符合需求，边界/异常路径有测试；全量测试通过（打包后、离线、无 FAIL）。
- 两个包均可构建，包内条目/体积/翻译/文档结构通过检查。
- 版本、changelog、release-notes、中英手册、开发文档、测试常量一致。
- 文档导航正确、链接有效、徽章规范、无旧声明/本机路径/令牌。
- 内部归档指针（如涉及）已更新；发布物可复核。
- 提交与 tag 一致；`git status` 干净；未越权 push/Release。
- 最终答复说明：改了什么、如何验证、遗留风险、下一步（发布/归档是否需要批准）。

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
