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
from threading import Event

import addonHandler
import api
import config
import globalPluginHandler
import gui
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
	# 合并所有启用区域的截图：先抓一次并集，再在本地裁剪。
	# 区域较少且彼此靠近时能减少抓屏次数；区域相隔很远时并集接近全屏，
	# 单次抓屏反而更慢，所以默认关闭、由用户按需开启（保证默认行为不变）。
	"mergeCapture": "boolean(default=False)",
	# True：每个有变化的区域各入队一条朗读，互不打断；False：打断式，只读最后一条
	"queueSpeech": "boolean(default=False)",
	# ---- 识别参数 ----
	"threshold": "float(min=0.0, max=1.0, default=0.5)",
	"interval": "float(min=0.0, max=10.0, default=1.0)",
	"regexFilters": "string(default='')",
}
config.conf.spec[CONFIG_SECTION] = confspec


def _baseOnlySections() -> tuple:
	"""NVDA 2026.3+ 的“仅基础配置”节集合；旧版本没有该属性时返回空元组。"""
	return getattr(config.conf, "BASE_ONLY_SECTIONS", ())


def ensureBaseOnlySection() -> None:
	"""把配置节登记为“仅基础配置”，使其不随 NVDA 配置档案（Profile）变化。

	NVDA 2026.3 起才有 `config.configSections`；登记会写入 `customSections.yaml`，
	**下次启动**才生效。正常安装由 `installTasks.py` 完成；这里兜底覆盖侧载、
	手工复制到 addons 目录、scratchpad 等未执行 installTasks 的情况。
	"""
	configSections = getattr(config, "configSections", None)
	if configSections is None:
		logHandler.log.debug("LION: NVDA < 2026.3，配置节仍会随配置档案变化")
		return
	if CONFIG_SECTION in _baseOnlySections():
		logHandler.log.debug("LION: 配置节已固定为基础配置（全局）")
		return
	try:
		configSections.registerSection(CONFIG_SECTION, confspec, isBaseOnly=True)
		logHandler.log.info("LION: 已把配置节登记为全局（base-only），重启 NVDA 后生效")
	except Exception:  # noqa: BLE001 - 登记失败不应影响插件加载
		logHandler.log.debugWarning("LION: 登记 base-only 配置节失败", exc_info=True)


def reportProfileOverrides() -> None:
	"""base-only 生效后，提示活动配置档案里被忽略的本插件设置。"""
	if CONFIG_SECTION not in _baseOnlySections():
		return
	profiles = list(getattr(config.conf, "profiles", []) or [])
	for profile in profiles[1:]:
		try:
			if CONFIG_SECTION in profile:
				name = getattr(profile, "name", "?")
				logHandler.log.info(
					"LION: 配置档案 %s 中的插件设置已被忽略（配置节固定为全局）", name
				)
		except Exception:  # noqa: BLE001 - 检测失败不影响插件加载
			continue


ensureBaseOnlySection()
reportProfileOverrides()


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


def registerSettingsPanel(panelClass) -> None:
	"""把设置面板注册进 NVDA 设置对话框（幂等）。

	插件更新/模块重载时 `__init__` 可能被调用不止一次，而
	`gui.settingsDialogs.NVDASettingsDialog.categoryClasses` 是 NVDA 模块里的
	普通列表、会跨重载存活；先判重可避免设置里出现两个同名面板。
	"""
	panels = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
	if panelClass not in panels:
		panels.append(panelClass)


def unregisterSettingsPanel(panelClass) -> None:
	"""移除设置面板（幂等，允许重复调用）。"""
	panels = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
	while panelClass in panels:
		panels.remove(panelClass)


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
		#: 按设置缓存的 OCR 客户端（设置变化时由 ocrClient() 重建）
		self._client = None
		self._clientSignature = None
		#: 可被“停止识别”立刻唤醒的等待事件（替代不可中断的 time.sleep）
		self._stopEvent = Event()
		#: 每个区域一套去重状态（策略见各区域的 dedup 字段）与"是否已提示过失败"
		self._dedup = dedup.DedupEngine()
		self._errorReported: dict = {}
		registerSettingsPanel(LIONSettingsPanel)

	def terminate(self):
		unregisterSettingsPanel(LIONSettingsPanel)
		global active
		active = False
		# 让还在 wait 的轮询立刻醒来；正在进行的 HTTP 请求会在返回后看到 active=False
		self._ensureStopEvent().set()
		self._resetClient()
		# 清空可能还在排队的朗读，避免停止后继续念
		try:
			self._dedup.reset()
		except Exception:  # noqa: BLE001 - 退出路径不应抛异常
			pass
		try:
			speech.cancelSpeech()
		except Exception:  # noqa: BLE001 - 退出路径不应抛异常
			logHandler.log.debugWarning("LION: cancelSpeech failed", exc_info=True)

	# ------------------------------------------------------------ 运行期辅助

	def _ensureStopEvent(self):
		"""取出（必要时创建）停止事件。

		测试会用 `GlobalPlugin.__new__` 直接构造实例而不调用 `__init__`，
		所以这里做惰性创建，保证这些路径也能正常工作。
		"""
		event = getattr(self, "_stopEvent", None)
		if event is None:
			event = Event()
			self._stopEvent = event
		return event

	def _resetClient(self) -> None:
		"""丢弃缓存的 OCR 客户端，下次识别时按最新设置重建。"""
		self._client = None
		self._clientSignature = None

	def _currentClientSignature(self) -> tuple:
		"""客户端相关设置的快照；设置一变就重建客户端。"""
		conf = config.conf[CONFIG_SECTION]
		return (
			conf["serviceUrl"],
			conf["model"],
			conf["serviceToken"],
			float(conf["requestTimeout"]),
			int(conf["imageFormat"]),
			bool(conf["visualize"]),
			float(conf["scoreThresh"]),
			int(conf["detLimitSideLen"]),
		)

	def ocrClient(self) -> HttpOcrClient:
		"""返回按当前设置缓存的 OCR 客户端，设置变化时自动重建。

		轮询期间不再"每个区域、每一帧"新建客户端；只有在设置（地址/模型/令牌/
		超时/图像格式/可视化/分数/最长边）变化后才重建。这样既减少对象分配，
		也避免同一轮抓屏里出现两套不一致的请求参数。
		"""
		signature = self._currentClientSignature()
		client = getattr(self, "_client", None)
		if client is None or getattr(self, "_clientSignature", None) != signature:
			client = makeOcrClient()
			self._client = client
			self._clientSignature = signature
		return client

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
			self._resetClient()
			self._ensureStopEvent().clear()
			self._dedup.reset()
			self._errorReported = {}
			ui.message(_("lion started"))
			threading.Thread(target=self.ocrLoop, daemon=True).start()
		else:
			active = False
			self._ensureStopEvent().set()
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

	# ---------------------------------------------------------------- 抓屏

	def captureRegionImage(self, rect, sharedImage=None, union=None):
		"""抓取一个区域的截图。

		传了 `sharedImage`（合并截图）与 `union` 时，从共享截图里裁剪对应区域；
		否则单独抓这个区域。裁剪异常由调用方按"该区域失败"处理。
		"""
		if sharedImage is not None and union is not None:
			left = rect["left"] - union["left"]
			top = rect["top"] - union["top"]
			return sharedImage.crop((left, top, left + rect["width"], top + rect["height"]))
		return ImageGrab.grab(
			bbox=(rect["left"], rect["top"], rect["left"] + rect["width"], rect["top"] + rect["height"])
		)

	def grabMergedImage(self, entries) -> tuple:
		"""一次抓取所有启用区域的并集，返回 `(image, unionRect)`。

		没有可抓区域或抓屏失败时返回 `(None, None)`，调用方会退回逐区域抓屏——
		"合并截图"只是可选优化，不能因为它整轮没有信息。
		"""
		union = regionModel.unionRect([rect for _index, _region, rect in entries])
		if union is None:
			return None, None
		try:
			image = ImageGrab.grab(
				bbox=(
					union["left"],
					union["top"],
					union["left"] + union["width"],
					union["top"] + union["height"],
				)
			)
		except Exception:  # noqa: BLE001 - 优化失败不应影响识别主流程
			logHandler.log.debugWarning("LION: 合并区域截图失败，退回逐区域抓屏", exc_info=True)
			return None, None
		return image, union

	# ---------------------------------------------------------------- 主循环

	def ocrLoop(self):
		global active
		stopEvent = self._ensureStopEvent()
		while active:
			try:
				self.OcrScreen()
			except Exception as error:  # noqa: BLE001 - 单轮失败不应终止循环
				logHandler.log.error("LION: OCR loop error: %s", error, exc_info=True)
			if not active:
				break
			# 用 Event.wait 代替 time.sleep：点击“停止识别”后不必再等完整个间隔
			stopEvent.wait(max(0.05, float(config.conf[CONFIG_SECTION]["interval"])))

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
		`[(regionId, regionName, message), ...]`。每个区域的截图/识别失败互相隔离。

		`mergeCapture` 开启且勾选了多于一个区域时，先抓一次所有区域的并集，
		再在本地按区域裁剪（把 N 次抓屏降成 1 次）；合并截图或裁剪失败时，
		该区域会自动退回单独抓屏，保证不会因为优化而丢信息。
		"""
		changed = []
		failures = []
		entries = self.getRegionRects()
		# 区域可能被删除/停用，先清掉它们留下的状态，避免字典无限增长
		configured = loadRegions()
		self._dedup.prune(configured)
		self._errorReported = regionModel.pruneState(self._errorReported, configured)
		conf = config.conf[CONFIG_SECTION]
		globalThreshold = conf.get("threshold", 0.5)
		sharedImage = None
		union = None
		if len(entries) > 1 and bool(conf.get("mergeCapture", False)):
			sharedImage, union = self.grabMergedImage(entries)
		for index, region, rect in entries:
			regionId = region["id"]
			label = regionModel.defaultName(region, index)
			# 每区域可覆盖经典相似度阈值；None/非法时跟随全局默认值
			threshold = regionModel.resolveClassicThreshold(region, globalThreshold)
			# 时间只在插件里取一次，去重状态机本身不读时钟（便于测试）
			now = time.monotonic()
			try:
				try:
					image = self.captureRegionImage(rect, sharedImage, union)
				except Exception:  # noqa: BLE001 - 合并截图裁剪失败：该区域退回单独抓屏
					logHandler.log.debugWarning(
						"LION: 从合并截图裁剪区域 [%s] 失败，退回单独抓屏", label, exc_info=True
					)
					image = self.captureRegionImage(rect)
				client = self.ocrClient()
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
			# 空结果也要喂给去重引擎（对话框清空 = 允许同一句再次朗读）
			events = self._dedup.observe(
				regionId,
				text,
				region["dedup"],
				region["dedupHoldMs"],
				now,
				threshold,
			)
			# 排查“某区域不播报”用：仅 debug 级别，记录有效阈值与判定结果
			logHandler.log.debug(
				"LION: 区域[%s] mode=%s 有效阈值=%.2f 文本=%d 字 事件=%d",
				label,
				region["dedup"],
				threshold,
				len(text),
				len(events),
			)
			for event in events:
				changed.append(event.text)
		return changed, failures

	def OcrScreen(self):
		"""跑一轮：识别所有勾选区域 → 提示失败 → 朗读有变化的文本。"""
		changed, failures = self.scanRegions()
		for regionId, label, message in failures:
			self.reportFailure("%s: %s" % (label, message), regionId=regionId)
		self.speakTexts(changed)
