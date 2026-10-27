# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""LION 修改版 lite 的安装任务：把配置节登记为基础配置（NVDA 2026.3+）。

NVDA 2026.3 新增 `config.configSections.registerSection`：插件在安装时把自己的
配置节写进 `customSections.yaml`，NVDA 下次启动时先于配置管理器加载，
`isBaseOnly=True` 表示该节只存在于基础配置、**不再被配置档案覆盖**。
更早的版本没有这套 API，直接跳过；插件运行时的 `config.conf.spec[...]` 兜底仍然有效。

这里**任何情况下都不向外抛异常**：安装任务抛异常会让 NVDA 回滚整个插件安装，
所以整段逻辑用 try/except 包住，失败只写 debugWarning。
"""

import config

#: 本插件的配置节，与插件 `__init__.py` 的 CONFIG_SECTION 保持一致。
SECTION = "LION-modified-lite"

#: 与插件 `__init__.py` 的 confspec 完全一致（17 项），两处必须同步修改。
SPEC = {
	# ---- 外部 OCR 服务（3.3.7 起：地址是基址，端点由 model 决定） ----
	"serviceUrl": "string(default='http://127.0.0.1:23353')",
	# 模型/OCR 端点：端点路径（如 /ocr、/ocr-acc、/layout-parsing）或完整 URL
	"model": "string(default='/ocr')",
	"serviceToken": "string(default='')",
	"requestTimeout": "float(min=1.0, max=120.0, default=15.0)",
	# 0 = PNG，1 = JPEG（JPEG 更快、更省带宽）
	"imageFormat": "integer(min=0, max=1, default=1)",
	# 服务端返回可视化图；开启会明显变慢
	"visualize": "boolean(default=False)",
	"scoreThresh": "float(min=0.0, max=1.0, default=0.0)",
	# 0 = 不发送 textDetLimitSideLen，用服务端配置
	"detLimitSideLen": "integer(min=0, max=4000, default=0)",
	"notifyOnError": "boolean(default=True)",
	# ---- 原有识别参数（与原版一致） ----
	"cropUp": "integer(min=0, max=100, default=0)",
	"cropLeft": "integer(min=0, max=100, default=0)",
	"cropRight": "integer(min=0, max=100, default=0)",
	"cropDown": "integer(min=0, max=100, default=0)",
	"target": "integer(min=0, max=3, default=1)",
	"threshold": "float(min=0.0, max=1.0, default=0.5)",
	"interval": "float(min=0.0, max=10.0, default=1.0)",
	"regexFilters": "string(default='')",
}


def onInstall() -> None:
	"""插件安装时调用：登记配置节。

	2026.1/2026.2 没有 configSections API → 直接返回。
	任何异常都被吞掉：抛出会让 NVDA 判定安装失败并回滚。
	"""
	try:
		configSections = getattr(config, "configSections", None)
		if configSections is None:
			return
		configSections.registerSection(SECTION, SPEC, isBaseOnly=True)
	except Exception:  # noqa: BLE001 - 安装任务绝不能抛异常，否则整包安装回滚
		try:
			from logHandler import log

			log.debugWarning("LION lite: 安装时登记配置节失败", exc_info=True)
		except Exception:  # noqa: BLE001 - 连日志都不可用时保持静默
			pass
