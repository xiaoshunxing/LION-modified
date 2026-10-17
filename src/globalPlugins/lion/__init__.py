# SPDX-License-Identifier: GPL-2.0-or-later
# LION 修改版 (LION modified) — NVDA global plugin
# 本文件基于 LION（作者 Stefan Moisei / Cary-rowen / anmi2004 / hwf1324）修改，
# 改用外部 PaddleOCR HTTP 服务，遵循 GNU GPL v2 或更高版本发布。
#
# Copyright (C) 2025-2026 LION contributors
# Copyright (C) 2026 xiaoshunxing — 外部服务接入、多选区识别、排队朗读

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
import speech
import tones
import ui
from PIL import ImageGrab

addonHandler.initTranslation()

from . import dedup  # noqa: E402 - 必须在 initTranslation 之后
from . import regions as regionModel  # noqa: E402
from .lionGui import LIONSettingsPanel  # noqa: E402
from .ocrClient import DEFAULT_MODEL, HttpOcrClient, splitServiceUrl  # noqa: E402

#: 兼容旧调用点：3.3.2 的相似度比较函数迁移到了 dedup 模块。
similarityRatio = dedup.similarityRatio

active = False

#: 与 NVDA 自带 PIL 一致，按物理像素抓屏
try:
	import ctypes

	ctypes.windll.user32.SetProcessDPIAware()
	_user32 = ctypes.windll.user32
	resX, resY = _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1)
except Exception:  # noqa: BLE001 - 非 Windows 或调用失败时退化为 PIL 默认尺寸
	logHandler.log.debugWarning("LION: SetProcessDPIAware failed", exc_info=True)
	resX, resY = 0, 0

#: 本插件的配置节。与原版 LION 的 "lion" 节完全独立，两个插件互不覆盖配置。
CONFIG_SECTION = "LION-modified"

confspec = {
	# ---- 外部 OCR 服务 ----
	# 3.3.6 起 serviceUrl 是"服务基址"（可含反向代理路径前缀），具体打哪个端点由 model 决定
	"serviceUrl": "string(default='http://127.0.0.1:23353')",
	# 模型/OCR 端点：端点路径（如 /ocr、/ocr-acc、/layout-parsing）或完整 URL
	"model": "string(default='/ocr')",
	"serviceToken": "string(default='')",
	"requestTimeout": "float(min=1.0, max=120.0, default=15.0)",
	# 0 = PNG，1 = JPEG（JPEG 更快、更省带宽，实测 4K 全屏 364 ms -> 339 ms）
	"imageFormat": "integer(min=0, max=1, default=1)",
	# 服务端返回可视化图；开启会在服务端画框并编码，实测全屏从 0.34 s 涨到 2.0 s
	"visualize": "boolean(default=False)",
	"scoreThresh": "float(min=0.0, max=1.0, default=0.0)",
	# 0 = 不发送 textDetLimitSideLen，用服务端配置
	"detLimitSideLen": "integer(min=0, max=4000, default=0)",
	"notifyOnError": "boolean(default=True)",
	# ---- 多选区识别 ----
	# 区域列表的 JSON，例如
	# [{"id":1,"name":"字幕","enabled":true,"target":1,"cropUp":70,"cropDown":0,"cropLeft":0,"cropRight":0}]
	"regions": "string(default='[]')",
	# True：每个有变化的区域各入队一条朗读，互不打断；False：打断式，只读最后一条
	"queueSpeech": "boolean(default=False)",
	# ---- 识别参数 ----
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


def similarityRatio(previous: str, current: str) -> float:
	"""比较两段识别文本的相似度（0.0–1.0）。

	**必须显式传 `autojunk=False`。** `difflib` 默认会对长度 ≥ 200 的序列启用
	"autojunk"，把出现次数超过 1% 的元素当成噪声忽略；中日韩文本里常用字重复率极高，
	于是"新句 字幕 / 新句 字幕"这类真实字幕会被判定为相似度过高而**永远不再朗读**。
	识别结果通常只有几十个字，关掉这个启发式既准确又几乎不增加开销。
	"""
	try:
		return SequenceMatcher(None, previous, current, autojunk=False).ratio()
	except Exception:  # noqa: BLE001 - 比较失败时视为"变了"，宁可多读一次
		return 0.0


def loadRegions() -> list:
	"""读取当前区域列表（容错，永不抛异常）。"""
	return regionModel.loadFromJson(config.conf[CONFIG_SECTION].get("regions", ""))


def saveRegions(regions) -> None:
	"""写回区域列表。"""
	config.conf[CONFIG_SECTION]["regions"] = regionModel.toJson(regions)


def ensureRegionsMigrated() -> list:
	"""首次运行时把旧版单区域配置迁移成区域列表。

	只有配置里**还没有 `regions` 键**时才迁移——因此用户在界面上把区域全部删除后
	（`regions` 写成了 `[]`）不会被误当成旧配置而自动长回来。
	旧配置里若存在 `target` 或裁剪键，就用它们生成一个区域；否则写入整屏默认区域。
	"""
	section = config.conf[CONFIG_SECTION]
	if "regions" not in section:
		regions = regionModel.migrateFromLegacy(section)
		saveRegions(regions)
		logHandler.log.info("LION: 已把识别区域配置迁移为 %d 个区域", len(regions))
		return regions
	return loadRegions()


def ensureServiceMigrated() -> None:
	"""把 3.3.5 的"完整服务地址"迁移成 3.3.6 的"基址 + 模型/端点"。

	只有配置里**还没有 `model` 键**时才迁移，避免覆盖用户之后的选择。
	迁移保证"拆完再组合"与旧行为等价（旧的完整地址拼回去一字不差），
	所以老用户升级后不需要任何手工操作。
	"""
	section = config.conf[CONFIG_SECTION]
	if "model" in section:
		return
	legacy = section.get("serviceUrl", "")
	base, model = splitServiceUrl(legacy)
	if base:
		section["serviceUrl"] = base
		section["model"] = model
		logHandler.log.info(
			"LION: 已把服务地址迁移为 基址=%s 模型/端点=%s", base, model
		)
	else:
		# 地址非法：保留用户原样输入，模型给默认值，便于在设置里发现笔误
		section["model"] = DEFAULT_MODEL
		logHandler.log.info("LION: 服务地址无法解析，保留原值并设置默认模型 %s", DEFAULT_MODEL)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = _("LION modified")

	def __init__(self):
		super(GlobalPlugin, self).__init__()
		ensureRegionsMigrated()
		ensureServiceMigrated()
		self.ocr = makeOcrClient()
		# 每个区域一套去重状态（策略见各区域的 dedup 字段）与"是否已提示过失败"
		self._dedup = dedup.DedupEngine()
		self._errorReported: dict = {}
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(LIONSettingsPanel)

	def terminate(self):
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(LIONSettingsPanel)
		except ValueError:
			pass
		global active
		active = False
		# 清空可能还在排队的朗读，避免停止后继续念
		try:
			self._dedup.reset()
		except Exception:  # noqa: BLE001 - 退出路径不应抛异常
			pass
		try:
			speech.cancelSpeech()
		except Exception:  # noqa: BLE001 - 退出路径不应抛异常
			logHandler.log.debugWarning("LION: cancelSpeech failed", exc_info=True)

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
			# 每次启动都按最新设置重建客户端，并清空上一轮的去重状态
			self.ocr = makeOcrClient()
			self._dedup.reset()
			self._errorReported = {}
			ui.message(_("lion started"))
			threading.Thread(target=self.ocrLoop, daemon=True).start()
		else:
			active = False
			ui.message(_("lion stopped"))

	# ------------------------------------------------------------ 区域与几何

	def resolveBaseRect(self, target: int):
		"""按目标类型解析基底矩形，返回 `{"left","top","width","height"}` 或 None。

		返回普通字典而不是 `RectLTWH`，这样 `regions` 模块保持与 NVDA 类型无关。
		解析不到时返回 None，由 `regionRect` 退回整屏。
		"""
		screen = {"left": 0, "top": 0, "width": resX, "height": resY}
		try:
			if target == regionModel.NAVIGATOR_OBJECT:
				obj = api.getNavigatorObject()
			elif target == regionModel.WHOLE_SCREEN:
				return screen
			elif target == regionModel.CURRENT_WINDOW:
				obj = api.getForegroundObject()
			else:
				obj = api.getFocusObject()
		except Exception:  # noqa: BLE001 - 取对象失败时退回整屏，不影响其它区域
			logHandler.log.debugWarning("LION: 解析识别目标失败", exc_info=True)
			return screen
		location = getattr(obj, "location", None) if obj is not None else None
		if location is None or location.width <= 0 or location.height <= 0:
			return screen
		return {
			"left": location.left,
			"top": location.top,
			"width": location.width,
			"height": location.height,
		}

	def getRegionRects(self) -> list:
		"""返回 `[(index, region, rect), ...]`，只含被勾选的区域。"""
		result = []
		for index, region in enumerate(loadRegions()):
			if not region["enabled"]:
				continue
			rect = regionModel.regionRect(region, self.resolveBaseRect, screen=(resX, resY))
			result.append((index, region, rect))
		return result

	# ---------------------------------------------------------------- 主循环

	def ocrLoop(self):
		global active
		while active:
			try:
				self.OcrScreen()
			except Exception as error:  # noqa: BLE001 - 单轮失败不应终止循环
				logHandler.log.error("LION: OCR loop error: %s", error, exc_info=True)
			if not active:
				break
			time.sleep(max(0.05, float(config.conf[CONFIG_SECTION]["interval"])))

	def filterText(self, text: str) -> str:
		"""按设置里的正则过滤识别结果，并规整空白。

		两点值得注意（都是原版行为的实际缺陷所在）：

		1. 识别结果是用空格拼成一行的，所以**行锚点 `^`/`$` 匹配不到单行**。
		   想删掉某一行请用 `[^\\s]*` 之类的写法，或用不带锚点的关键词。
		2. 正则往往只删掉一部分（例如只删掉"广告"两个字），删完会留下多余空格；
		   这里把连续空白折叠成单空格并去掉首尾空白，避免把空内容当成"文本变化"
		   而莫名朗读。
		"""
		for pattern in config.conf[CONFIG_SECTION]["regexFilters"].splitlines():
			if pattern.strip():
				try:
					text = re.sub(pattern.strip(), "", text)
				except re.error:
					# 无效正则忽略即可，不要让整轮识别失败
					continue
		return re.sub(r"\s+", " ", text).strip()

	def reportFailure(self, message: str, regionId=None):
		"""失败提示：**每个区域**连续失败只提示第一次。"""
		if not config.conf[CONFIG_SECTION]["notifyOnError"]:
			return
		key = regionId if regionId is not None else 0
		if self._errorReported.get(key):
			return
		self._errorReported[key] = True
		ui.message(_("LION: OCR service unavailable. %s") % message)

	def speakTexts(self, texts: list):
		"""朗读本轮所有"有变化"的文本。

		* `queueSpeech=True`：逐条以 `speech.Spri.NEXT` 入队，互不打断，按区域顺序朗读。
		* `queueSpeech=False`：打断式——只有最后一条用 `speech.Spri.NOW` 立刻朗读，
		  其余丢弃（这样在多区域时不会互相打断；单区域时与旧版行为完全一致）。
		"""
		if not texts:
			return
		if config.conf[CONFIG_SECTION]["queueSpeech"]:
			for text in texts:
				ui.message(text, speechPriority=speech.Spri.NEXT)
		else:
			ui.message(texts[-1], speechPriority=speech.Spri.NOW)

	def scanRegions(self) -> tuple:
		"""识别所有勾选区域，返回 `(changedTexts, failures)`。

		`changedTexts` 是按区域顺序的"文本有变化"列表；`failures` 是
		`[(regionId, regionName, message), ...]`。每个区域用**独立客户端**，
		这样失败信息不会被其它区域覆盖。
		"""
		changed = []
		failures = []
		entries = self.getRegionRects()
		# 区域可能被删除/停用，先清掉它们留下的状态，避免字典无限增长
		configured = loadRegions()
		self._dedup.prune(configured)
		self._errorReported = regionModel.pruneState(self._errorReported, configured)
		threshold = config.conf[CONFIG_SECTION]["threshold"]
		for index, region, rect in entries:
			regionId = region["id"]
			label = regionModel.defaultName(region, index)
			# 时间只在插件里取一次，去重状态机本身不读时钟（便于测试）
			now = time.monotonic()
			try:
				image = ImageGrab.grab(bbox=(rect["left"], rect["top"], rect["left"] + rect["width"], rect["top"] + rect["height"]))
				client = makeOcrClient()
				result = client.runBytes(client.encodeImage(image))
			except Exception as error:  # noqa: BLE001 - 单区域失败不影响其它区域
				message = str(error)
				logHandler.log.error("LION: 区域 [%s] 截图/识别异常：%s", label, message, exc_info=True)
				failures.append((regionId, label, message))
				continue
			code = result.get("code")
			if code != 100:
				message = str(result.get("data", "")) or client.lastErrorBrief
				logHandler.log.error("LION: 区域 [%s] 识别失败，code=%s：%s", label, code, message)
				failures.append((regionId, label, message))
				continue
			self._errorReported[regionId] = False
			text = self.filterText(" ".join([line["text"] for line in result["data"]]))
			if not text:
				# 空结果也要喂给去重引擎（对话框清空 = 允许同一句再次朗读）
				self._dedup.observe(regionId, text, region["dedup"], region["dedupHoldMs"], now, threshold)
				continue
			for event in self._dedup.observe(
				regionId,
				text,
				region["dedup"],
				region["dedupHoldMs"],
				now,
				threshold,
			):
				changed.append(event.text)
		return changed, failures

	def OcrScreen(self):
		"""跑一轮：识别所有勾选区域 → 提示失败 → 朗读有变化的文本。"""
		changed, failures = self.scanRegions()
		for regionId, label, message in failures:
			self.reportFailure("%s: %s" % (label, message), regionId=regionId)
		self.speakTexts(changed)
