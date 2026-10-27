# SPDX-License-Identifier: GPL-2.0-or-later
# LION 修改版 (LION modified) — 安装任务
# Copyright (C) 2026 xiaoshunxing
"""安装时把配置节登记为“仅基础配置”（NVDA 2026.3+）。

NVDA 2026.3 新增 `config.configSections.registerSection`：它把插件配置节写入
`%APPDATA%\\nvda\\customSections.yaml`，下次启动时在 `ConfigManager` 构造之前加载；
`isBaseOnly=True` 让该节只存在于基础配置，从而**不随配置档案（Profile）或触发器变化**。

本模块刻意不导入插件自身的任何模块（`globalPlugins.*`）：安装/升级时旧版本模块可能
仍在 `sys.modules` 里（NVDA 2026.3 的 #20715 才修复清理），保持自包含最安全。
`SPEC` 必须与 `globalPlugins/lion/__init__.py` 的 `confspec` 保持一致（测试会断言）。

不提供 `onUninstall`，也不调用 `unregisterSection`：升级首启时旧版本的 `onUninstall`
会先执行，若在那里注销会误删新版本刚登记的 YAML。
"""

SECTION = "LION-modified"

#: 与 globalPlugins/lion/__init__.py 的 confspec 逐项一致（测试用 AST 断言）。
SPEC = {
	# ---- 外部 OCR 服务 ----
	"serviceUrl": "string(default='http://127.0.0.1:23353')",
	"model": "string(default='/ocr')",
	"serviceToken": "string(default='')",
	"requestTimeout": "float(min=1.0, max=120.0, default=15.0)",
	"imageFormat": "integer(min=0, max=1, default=1)",
	"visualize": "boolean(default=False)",
	"scoreThresh": "float(min=0.0, max=1.0, default=0.0)",
	"detLimitSideLen": "integer(min=0, max=4000, default=0)",
	"notifyOnError": "boolean(default=True)",
	# ---- 多选区识别 ----
	"regions": "string(default='[]')",
	"mergeCapture": "boolean(default=False)",
	"queueSpeech": "boolean(default=False)",
	# ---- 识别参数 ----
	"threshold": "float(min=0.0, max=1.0, default=0.5)",
	"interval": "float(min=0.0, max=10.0, default=1.0)",
	"regexFilters": "string(default='')",
}


def _registerBaseOnlySection() -> None:
	"""把本插件配置节登记为 base-only；API 不存在（NVDA < 2026.3）时静默跳过。"""
	import config

	configSections = getattr(config, "configSections", None)
	if configSections is None:
		return
	configSections.registerSection(SECTION, SPEC, isBaseOnly=True)


def onInstall():
	"""NVDA 安装/更新完成后调用；注册失败不能中断安装。"""
	try:
		_registerBaseOnlySection()
	except Exception:  # noqa: BLE001 - 安装任务不能抛异常，否则安装会被回滚
		try:
			from logHandler import log

			log.debugWarning("LION-modified: failed to register base-only config section", exc_info=True)
		except Exception:  # noqa: BLE001 - 连日志都拿不到时也要保证安装继续
			pass
