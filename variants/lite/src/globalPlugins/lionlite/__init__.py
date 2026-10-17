# SPDX-License-Identifier: GPL-2.0-or-later
# LION 修改版 lite（LION modified lite）— NVDA global plugin
# 本文件基于 LION（作者 Stefan Moisei / Cary-rowen / anmi2004 / hwf1324）修改，
# 以"单区域、原版式界面"的简洁版为基线，客户端对齐完整版 3.3.7。
# 遵循 GNU GPL v2 或更高版本发布（见 COPYING.txt）。
#
# Copyright (C) 2025-2026 LION contributors
# Copyright (C) 2026 xiaoshunxing — 外部服务接入与简洁版

import re
import threading
import time
from difflib import SequenceMatcher

import addonHandler
import api
import config
import globalPluginHandler
import gui
import locationHelper
import logHandler
import scriptHandler
import tones
import ui
from PIL import ImageGrab

addonHandler.initTranslation()

from .lionGui import LIONSettingsPanel  # noqa: E402 - 必须在 initTranslation 之后
from .ocrClient import HttpOcrClient  # noqa: E402

active = False
prevString = ""

#: 与 NVDA 自带 PIL 一致，按物理像素抓屏
try:
	import ctypes

	ctypes.windll.user32.SetProcessDPIAware()
	_user32 = ctypes.windll.user32
	resX, resY = _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1)
except Exception:  # noqa: BLE001 - 非 Windows 或调用失败时退化为 PIL 默认尺寸
	logHandler.log.debugWarning("LION lite: SetProcessDPIAware failed", exc_info=True)
	resX, resY = 0, 0

#: 本插件的配置节。与完整版 "LION-modified"、原版 "lion" 都相互独立。
CONFIG_SECTION = "LION-modified-lite"

confspec = {
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
config.conf.spec[CONFIG_SECTION] = confspec


def makeOcrClient(cfg=None) -> HttpOcrClient:
	"""按当前设置构造一个 OCR 客户端。"""
	cfg = config.conf[CONFIG_SECTION] if cfg is None else cfg
	return HttpOcrClient(
		url=cfg.get("serviceUrl", ""),
		model=cfg.get("model", ""),
		token=cfg.get("serviceToken", ""),
		timeout=cfg.get("requestTimeout", 15.0),
		visualize=cfg.get("visualize", False),
		score_thresh=cfg.get("scoreThresh", 0.0),
		det_limit_side_len=cfg.get("detLimitSideLen", 0),
		image_format="PNG" if int(cfg.get("imageFormat", 1) or 0) == 0 else "JPEG",
	)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = _("LION modified lite")

	def __init__(self):
		super(GlobalPlugin, self).__init__()
		self.ocr = makeOcrClient()
		# 连续失败只在第一次提示，避免每 1 秒刷屏
		self._errorReported = False
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(LIONSettingsPanel)

	def terminate(self):
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(LIONSettingsPanel)
		except ValueError:
			pass
		global active
		active = False

	def isScreenCurtainRunning(self):
		from screenCurtain import screenCurtain

		return screenCurtain.enabled

	@scriptHandler.script(description=_("Toggle OCR"), gestures=["kb:NVDA+Alt+N"])
	def script_ReadLiveOcr(self, gesture):
		global active
		if self.isScreenCurtainRunning() and not active:
			ui.message(_("Please disable screen curtain before using OCR."))
			return
		tones.beep(222, 333)
		if not active:
			active = True
			# 每次启动都按最新设置重建客户端，设置改动无需重启 NVDA
			self.ocr = makeOcrClient()
			self._errorReported = False
			ui.message(_("lion started"))
			threading.Thread(target=self.ocrLoop, daemon=True).start()
		else:
			active = False
			ui.message(_("lion stopped"))

	def getDynamicTargetRect(self):
		cfg = config.conf[CONFIG_SECTION]
		target_type = cfg["target"]
		if target_type == 0:
			obj = api.getNavigatorObject()
		elif target_type == 1:
			obj = locationHelper.RectLTWH(0, 0, resX, resY)
		elif target_type == 2:
			obj = api.getForegroundObject()
		else:
			obj = api.getFocusObject()

		base_rect = obj.location if hasattr(obj, "location") else locationHelper.RectLTWH(0, 0, resX, resY)
		return self.cropRectLTWH(base_rect)

	def cropRectLTWH(self, r: locationHelper.RectLTWH):
		if not r or r.width <= 0 or r.height <= 0:
			return locationHelper.RectLTWH(0, 0, resX, resY)
		cfg = config.conf[CONFIG_SECTION]
		left = r.left + int(r.width * cfg["cropLeft"] / 100.0)
		top = r.top + int(r.height * cfg["cropUp"] / 100.0)
		width = r.width - int(r.width * (cfg["cropLeft"] + cfg["cropRight"]) / 100.0)
		height = r.height - int(r.height * (cfg["cropUp"] + cfg["cropDown"]) / 100.0)
		return locationHelper.RectLTWH(
			max(r.left, min(left, r.left + r.width)),
			max(r.top, min(top, r.top + r.height)),
			max(10, min(width, resX - left)),
			max(10, min(height, resY - top)),
		)

	def ocrLoop(self):
		global active
		while active:
			try:
				self.OcrScreen()
			except Exception as error:  # noqa: BLE001 - 单轮失败不应终止循环
				logHandler.log.error("LION lite: OCR loop error: %s", error, exc_info=True)
			if not active:
				break
			time.sleep(max(0.05, float(config.conf[CONFIG_SECTION]["interval"])))

	def filterText(self, text: str) -> str:
		"""按设置里的正则过滤识别结果，并规整空白。

		识别结果是用空格拼成一行的，所以**行锚点 `^`/`$` 匹配不到单行**；
		正则往往只删掉一部分，删完留下的多余空格会被折叠，避免空内容被当成"文本变化"。
		"""
		for pattern in config.conf[CONFIG_SECTION]["regexFilters"].splitlines():
			if pattern.strip():
				try:
					text = re.sub(pattern.strip(), "", text)
				except re.error:
					# 无效正则忽略即可，不要让整轮识别失败
					continue
		return re.sub(r"\s+", " ", text).strip()

	def reportFailure(self, message: str):
		"""首次失败时提示用户，之后只记日志。"""
		if not config.conf[CONFIG_SECTION]["notifyOnError"]:
			return
		if self._errorReported:
			return
		self._errorReported = True
		ui.message(_("LION: OCR service unavailable. %s") % message)

	def OcrScreen(self):
		global prevString
		try:
			rect = self.getDynamicTargetRect()
			img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.left + rect.width, rect.top + rect.height))
			res = self.ocr.runBytes(self.ocr.encodeImage(img))
			code = res.get("code")
			if code != 100:
				self.reportFailure(str(res.get("data", "")) or self.ocr.lastErrorBrief)
				return
			self._errorReported = False
			all_text = " ".join([line["text"] for line in res["data"]])
			if not all_text:
				return

			all_text = self.filterText(all_text)
			if all_text and SequenceMatcher(None, prevString, all_text).ratio() < config.conf[CONFIG_SECTION]["threshold"]:
				ui.message(all_text)
				prevString = all_text
		except Exception as error:
			logHandler.log.error("LION lite OCR error: %s", error, exc_info=True)
