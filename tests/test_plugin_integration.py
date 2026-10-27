# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""对插件 `__init__.py` 的集成测试（不启动 NVDA）。

搭一套最小 NVDA 环境：`gui`/`gui.settingsDialogs`/`api`/`locationHelper`/
`scriptHandler`/`ui`/`tones`/`speech`/`screenCurtain`/`globalPluginHandler`/`wx`/`PIL`
用 mock 顶替，再用 `importlib.util.spec_from_file_location` 把插件的 `__init__.py`
直接加载成 `lion` 包（`from .lionGui import ...` 因而能解析，而不会真的导入 `wx`）。

覆盖改造后最容易出错的部分：多选区抓屏与裁剪、每区域独立去重与失败处理、
排队朗读的优先级与顺序、配置迁移、脚本开关、配置节独立性。
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import sys
import types
import unittest
from enum import IntEnum
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nvda_stubs  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, os.pardir, "src"))
PLUGIN_DIR = os.path.join(SRC, "globalPlugins", "lion")

SCREEN = (1920, 1080)

#: 真实的 PIL（在把 `PIL` 换成桩之前抓住），供在线用例做真实抓屏与真实编码。
#: 必须把编码插件也抓住：Pillow 的 save() 会在需要时 `import PIL.JpegImagePlugin`，
#: 而那时 `PIL` 已经变成桩模块，导入会失败（表现为 KeyError: 'JPEG'）。
try:
	import PIL as RealPILPackage  # type: ignore[import-not-found]
	from PIL import BmpImagePlugin as RealPILBmpPlugin  # type: ignore[import-not-found]
	from PIL import Image as RealPILImage  # type: ignore[import-not-found]
	from PIL import ImageFile as RealPILImageFile  # type: ignore[import-not-found]
	from PIL import ImageGrab as RealImageGrab  # type: ignore[import-not-found]
	from PIL import JpegImagePlugin as RealPILJpegPlugin  # type: ignore[import-not-found]
	from PIL import PngImagePlugin as RealPILPngPlugin  # type: ignore[import-not-found]

	REAL_PIL = True
	REAL_PIL_MODULES = {
		"PIL.BmpImagePlugin": RealPILBmpPlugin,
		"PIL.Image": RealPILImage,
		"PIL.ImageFile": RealPILImageFile,
		"PIL.JpegImagePlugin": RealPILJpegPlugin,
		"PIL.PngImagePlugin": RealPILPngPlugin,
	}
except Exception:  # noqa: BLE001 - 没有 Pillow 时在线用例自动跳过
	RealPILPackage = RealPILImage = RealPILImageFile = RealImageGrab = None
	REAL_PIL = False
	REAL_PIL_MODULES = {}


# ----------------------------------------------------------------- 测试辅助

def genericDefault(spec: str):
	"""从 configobj 规格字符串解析默认值。"""
	match = re.search(r"default\s*=\s*('([^']*)'|\"([^\"]*)\"|[^,\)\s]+)", spec)
	if not match:
		return None
	raw = match.group(1)
	if raw in ("True", "False"):
		return raw == "True"
	if match.group(2) is not None:
		return match.group(2)
	if match.group(3) is not None:
		return match.group(3)
	if re.match(r"^-?\d+$", raw):
		return int(raw)
	try:
		return float(raw)
	except ValueError:
		return raw


def pluginConfspec() -> dict:
	"""从源码 AST 读出插件自己的 confspec（不执行模块）。"""
	source = open(os.path.join(PLUGIN_DIR, "__init__.py"), encoding="utf-8").read()
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "confspec" for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError("confspec 未找到")


CONFSPEC = pluginConfspec()


def regionJson(regions) -> str:
	"""把简写的区域元组变成配置 JSON。

	元组格式：`(name, target, up, down, left, right [, enabled [, dedup [, holdMs]]])`
	"""
	items = []
	for index, raw in enumerate(regions):
		name, target, up, down, left, right = (list(raw) + [None] * 6)[:6]
		enabled = raw[6] if len(raw) > 6 else True
		dedup = raw[7] if len(raw) > 7 else "classic"
		holdMs = raw[8] if len(raw) > 8 else 1500
		items.append({
			"id": index + 1,
			"name": name,
			"enabled": enabled,
			"target": target,
			"cropUp": up,
			"cropDown": down,
			"cropLeft": left,
			"cropRight": right,
			"dedup": dedup,
			"dedupHoldMs": holdMs,
		})
	return json.dumps(items, ensure_ascii=False)


class FakeImage:
	def __init__(self, mode: str = "RGB", sink: list | None = None, crops: list | None = None, size=(0, 0)):
		self.mode = mode
		self.sink = sink if sink is not None else []
		self.crops = crops if crops is not None else []
		self.size = size

	def save(self, buffered, format="PNG", **kwargs):  # noqa: A002 - 对齐 PIL 接口
		self.sink.append({"format": format, **kwargs})
		buffered.write(b"encoded-image")

	def convert(self, mode: str):
		return FakeImage(mode, self.sink, self.crops, self.size)

	def crop(self, box):
		"""模拟 PIL 的裁剪：记录裁剪框，返回对应尺寸的假图。"""
		self.crops.append(box)
		left, top, right, bottom = box
		return FakeImage(self.mode, self.sink, self.crops, (right - left, bottom - top))


class FakeClient:
	"""脚本化的 OCR 客户端：每次调用返回下一个预设结果。"""

	def __init__(self, results, sink, lastError="last-error-brief"):
		self.results = results
		self.sink = sink
		self.lastErrorBrief = lastError
		self.lastError = lastError

	def runBytes(self, payload):
		self.sink.append(payload)
		if not self.results:
			return {"code": 100, "data": []}
		item = self.results.pop(0)
		if isinstance(item, Exception):
			raise item
		return item

	def encodeImage(self, image):
		image.save(io.BytesIO(), format="JPEG", quality=85)
		return b"encoded"


class SpeechPriority(IntEnum):
	NORMAL = 0
	NEXT = 1
	NOW = 2


def installModules():
	"""装上 mock 的 NVDA 模块，返回字典以便用例改行为。"""
	nvda_stubs.install()

	class FakeGlobalPlugin:
		def __init__(self):
			pass

	guiModule = types.ModuleType("gui")
	guiModule.settingsDialogs = types.SimpleNamespace(
		NVDASettingsDialog=types.SimpleNamespace(categoryClasses=[]),
		SettingsPanel=type("SettingsPanel", (), {}),
		SettingsDialog=type("SettingsDialog", (), {}),
	)
	modules = {
		"gui": guiModule,
		"gui.guiHelper": types.ModuleType("gui.guiHelper"),
		"gui.nvdaControls": types.ModuleType("gui.nvdaControls"),
		"wx": mock.MagicMock(name="wx"),
		"api": types.ModuleType("api"),
		"tones": types.ModuleType("tones"),
		"ui": types.ModuleType("ui"),
		"scriptHandler": types.ModuleType("scriptHandler"),
		"globalPluginHandler": types.ModuleType("globalPluginHandler"),
		"screenCurtain": types.ModuleType("screenCurtain"),
		"locationHelper": types.ModuleType("locationHelper"),
		"speech": types.ModuleType("speech"),
		"PIL": types.ModuleType("PIL"),
	}
	for name, module in modules.items():
		sys.modules[name] = module

	modules["globalPluginHandler"].GlobalPlugin = FakeGlobalPlugin
	modules["api"].getNavigatorObject = lambda: None
	modules["api"].getForegroundObject = lambda: None
	modules["api"].getFocusObject = lambda: None
	modules["tones"].beep = lambda *args: None
	modules["ui"].message = lambda *args, **kwargs: None
	modules["speech"].Spri = SpeechPriority
	modules["speech"].cancelSpeech = lambda: None
	modules["screenCurtain"].screenCurtain = types.SimpleNamespace(enabled=False)

	def script(*args, **kwargs):
		def decorator(func):
			func.gestures = kwargs.get("gestures", [])
			return func

		return decorator

	modules["scriptHandler"].script = script

	class RectLTWH:
		def __init__(self, left, top, width, height):
			self.left, self.top = left, top
			self.width, self.height = width, height
			self.right, self.bottom = left + width, top + height

		@property
		def location(self):
			return self

	modules["locationHelper"].RectLTWH = RectLTWH
	modules["PIL"].ImageGrab = types.SimpleNamespace(grab=lambda bbox=None: FakeImage())
	return modules


def loadPlugin():
	"""把插件 `__init__.py` 作为 `lion` 包加载。"""
	spec = importlib.util.spec_from_file_location(
		"lion",
		os.path.join(PLUGIN_DIR, "__init__.py"),
		submodule_search_locations=[PLUGIN_DIR],
	)
	module = importlib.util.module_from_spec(spec)
	sys.modules["lion"] = module
	spec.loader.exec_module(module)
	return module


class Harness(unittest.TestCase):
	"""公共基类：装 mock、加载插件、配置成"一个整屏区域"。"""

	@classmethod
	def setUpClass(cls):
		cls.modules = installModules()

	def setUp(self):
		for name in ("lion", "lion.lionGui", "lion.ocrClient", "lion.regions", "lion.dedup"):
			sys.modules.pop(name, None)
		self.plugin = loadPlugin()
		self.plugin.resX, self.plugin.resY = SCREEN
		self.messages: list = []
		self.modules["ui"].message = self.recordMessage
		self.grabbed: list = []
		self.saveCalls: list = []
		self.crops: list = []
		self.modules["PIL"].ImageGrab.grab = self.grab
		self.plugin.config.conf._sections.clear()
		self.section = self.plugin.config.conf["LION-modified"]
		for key, spec in CONFSPEC.items():
			self.section[key] = genericDefault(spec)
		self.plugin.active = False
		# 受控时钟：去重状态机按 now 判断"稳定"，测试不能依赖真实墙钟
		self.clock = [1000.0]
		self.plugin.time = types.SimpleNamespace(sleep=lambda seconds: None, monotonic=lambda: self.clock[0])

	def tick(self, seconds=1.0):
		"""推进虚拟时钟（模拟一个 OCR 间隔）。"""
		self.clock[0] += seconds
		return self.clock[0]

	def recordMessage(self, text, **kwargs):
		self.messages.append({"text": text, "priority": kwargs.get("speechPriority")})

	def grab(self, bbox=None):
		self.grabbed.append(bbox)
		return self.captureImage(bbox)

	def captureImage(self, bbox=None):
		"""返回"屏幕内容"。默认是可控的假图像；在线用例会换成真实截图。"""
		width = (bbox[2] - bbox[0]) if bbox else SCREEN[0]
		height = (bbox[3] - bbox[1]) if bbox else SCREEN[1]
		return FakeImage(sink=self.saveCalls, crops=self.crops, size=(width, height))

	def setRegions(self, regions):
		self.section["regions"] = regionJson(regions)

	def makePlugin(self, results, lastError="last-error-brief"):
		"""构造插件实例，并让每次 `makeOcrClient()` 返回一个脚本化客户端。"""
		instance = self.plugin.GlobalPlugin.__new__(self.plugin.GlobalPlugin)
		self.lastText = {}
		self.ocrCalls: list = []
		self.pending = list(results)

		def factory():
			self.ocrCalls.append(len(self.pending))
			return FakeClient(self.pending, self.saveCalls, lastError=lastError)

		self.plugin.makeOcrClient = factory
		instance._dedup = self.plugin.dedup.DedupEngine()
		instance._errorReported = {}
		instance.ocr = factory()
		return instance

	def texts(self):
		return [message["text"] for message in self.messages]


# --------------------------------------------------------------------- 用例

class ConfigSectionTests(Harness):
	def testRegistersIndependentSection(self):
		self.assertIn("LION-modified", self.plugin.config.conf.spec.sections)
		self.assertNotIn("lion", self.plugin.config.conf.spec.sections)
		self.assertEqual(self.plugin.CONFIG_SECTION, "LION-modified")

	def testSpecHasEveryKey(self):
		spec = self.plugin.config.conf.spec.sections["LION-modified"]
		self.assertEqual(len(spec), 15)
		for key in ("serviceUrl", "model", "serviceToken", "requestTimeout", "imageFormat", "visualize",
					"scoreThresh", "detLimitSideLen", "notifyOnError", "regions", "mergeCapture",
					"queueSpeech", "interval", "threshold", "regexFilters"):
			self.assertIn(key, spec)

	def testLegacyKeysAreGone(self):
		spec = self.plugin.config.conf.spec.sections["LION-modified"]
		for key in ("target", "cropUp", "cropDown", "cropLeft", "cropRight"):
			self.assertNotIn(key, spec)

	def testDefaultServiceUrl(self):
		# 3.3.6 起地址是服务基址，端点由 model 决定
		self.assertIn("http://127.0.0.1:23353", CONFSPEC["serviceUrl"])
		self.assertNotIn("/ocr", CONFSPEC["serviceUrl"])
		self.assertIn("default='/ocr'", CONFSPEC["model"])

	def testDefaults(self):
		self.assertEqual(genericDefault(CONFSPEC["regions"]), "[]")
		self.assertIs(genericDefault(CONFSPEC["queueSpeech"]), False)

	def testMakeOcrClientReadsSettings(self):
		self.section.update({
			"serviceUrl": "http://127.0.0.1:9999",
			"model": "/ocr-acc",
			"serviceToken": "tok",
			"requestTimeout": 7.5,
			"visualize": True,
			"scoreThresh": 0.4,
			"detLimitSideLen": 960,
			"imageFormat": 0,
		})
		client = self.plugin.makeOcrClient()
		self.assertEqual(client.base_url, "http://127.0.0.1:9999/ocr-acc")
		self.assertEqual(client.token, "tok")
		self.assertEqual(client.timeout, 7.5)
		self.assertIs(client.visualize, True)
		self.assertEqual(client.score_thresh, 0.4)
		self.assertEqual(client.det_limit_side_len, 960)
		self.assertEqual(client.image_format, "PNG")

	def testMakeOcrClientDefaultsToJpeg(self):
		client = self.plugin.makeOcrClient()
		self.assertEqual(client.image_format, "JPEG")
		self.assertEqual(client.base_url, "http://127.0.0.1:23353/ocr")

	def testGesturesKeepOriginalShortcut(self):
		self.assertEqual(self.plugin.GlobalPlugin.script_ReadLiveOcr.gestures, ["kb:NVDA+Alt+N"])

	def testPanelClassIsRegisteredType(self):
		self.assertTrue(issubclass(self.plugin.LIONSettingsPanel, object))
		self.assertEqual(self.plugin.LIONSettingsPanel.title, "LION modified")


class PanelRegistrationTests(Harness):
	"""设置面板注册必须幂等（插件更新/模块重载时 __init__ 可能被多次调用）。"""

	def panels(self):
		return self.modules["gui"].settingsDialogs.NVDASettingsDialog.categoryClasses

	def testRegistrationIsIdempotent(self):
		panelClass = self.plugin.LIONSettingsPanel
		self.panels().clear()
		self.addCleanup(self.panels().clear)
		self.plugin.registerSettingsPanel(panelClass)
		self.plugin.registerSettingsPanel(panelClass)
		self.assertEqual(self.panels(), [panelClass])
		self.plugin.unregisterSettingsPanel(panelClass)
		self.plugin.unregisterSettingsPanel(panelClass)
		self.assertEqual(self.panels(), [])


class BaseOnlySectionTests(Harness):
	"""配置节固定为全局：运行期兜底登记（NVDA 2026.3+）与旧版本跳过。"""

	def setUp(self):
		super().setUp()
		self.conf = self.plugin.config.conf
		self.addCleanup(self.plugin.config.__dict__.pop, "configSections", None)
		self.addCleanup(lambda: self.conf.__dict__.pop("BASE_ONLY_SECTIONS", None))
		self.addCleanup(lambda: self.conf.__dict__.pop("profiles", None))

	def installFakeApi(self):
		calls = []

		def registerSection(*args, **kwargs):
			calls.append((args, kwargs))

		self.plugin.config.configSections = types.SimpleNamespace(registerSection=registerSection)
		return calls

	def testRegistersWhenSectionIsNotBaseOnly(self):
		calls = self.installFakeApi()
		self.conf.BASE_ONLY_SECTIONS = set()
		self.plugin.ensureBaseOnlySection()
		self.assertEqual(len(calls), 1)
		args, kwargs = calls[0]
		self.assertEqual(args[0], "LION-modified")
		self.assertIs(kwargs["isBaseOnly"], True)

	def testSkipsWhenAlreadyBaseOnly(self):
		calls = self.installFakeApi()
		self.conf.BASE_ONLY_SECTIONS = {"LION-modified"}
		self.plugin.ensureBaseOnlySection()
		self.assertEqual(calls, [])

	def testSkipsWithoutApi(self):
		self.plugin.config.__dict__.pop("configSections", None)
		self.plugin.ensureBaseOnlySection()  # 不抛异常即可

	def testProfileOverrideDetectionDoesNotRaise(self):
		self.conf.BASE_ONLY_SECTIONS = {"LION-modified"}
		self.conf.profiles = [object(), {"LION-modified": {"threshold": 0.3}}]
		self.plugin.reportProfileOverrides()  # 只记录日志，不抛异常


class MigrationTests(Harness):
	def testEmptyRegionsMigrateToWholeScreen(self):
		self.section["regions"] = ""
		self.makePlugin([])
		loaded = self.plugin.loadRegions()
		self.assertEqual(len(loaded), 1)
		self.assertEqual(loaded[0]["target"], self.plugin.regionModel.WHOLE_SCREEN)
		self.assertEqual(loaded[0]["dedup"], "classic", "迁移出来的区域默认经典去重")

	def testMigratedRegionKeepsClassicDedup(self):
		"""旧配置迁移后必须是经典去重，行为与 3.3.2 一致。"""
		del self.section["regions"]
		self.section["target"] = 1
		self.plugin.ensureRegionsMigrated()
		self.assertEqual(self.plugin.loadRegions()[0]["dedup"], "classic")

	def testLegacyTargetAndCropMigrate(self):
		del self.section["regions"]
		self.section["target"] = 2
		self.section["cropUp"] = 70
		self.plugin.ensureRegionsMigrated()
		loaded = self.plugin.loadRegions()
		self.assertEqual(len(loaded), 1)
		self.assertEqual(loaded[0]["target"], self.plugin.regionModel.CURRENT_WINDOW)
		self.assertEqual(loaded[0]["cropUp"], 70)
		self.assertIn("regions", self.section)

	def testExistingRegionsAreNotOverwritten(self):
		self.setRegions([("字幕", 1, 70, 0, 0, 0)])
		before = self.section["regions"]
		self.plugin.ensureRegionsMigrated()
		self.assertEqual(self.section["regions"], before)

	def testMissingRegionsKeyMigrates(self):
		del self.section["regions"]
		self.section["target"] = 3
		self.plugin.ensureRegionsMigrated()
		self.assertEqual(self.plugin.loadRegions()[0]["target"], self.plugin.regionModel.CURRENT_CONTROL)
		self.assertIn("regions", self.section)

	def testBrokenJsonIsRepaired(self):
		self.section["regions"] = "{broken"
		loaded = self.plugin.loadRegions()
		self.assertEqual(len(loaded), 1)
		self.assertEqual(loaded[0]["target"], self.plugin.regionModel.WHOLE_SCREEN)


class ServiceMigrationTests(Harness):
	"""3.3.6：旧配置的完整服务地址迁移成"基址 + 模型/端点"。"""

	def testFullEndpointUrlSplitsIntoBaseAndModel(self):
		del self.section["model"]
		self.section["serviceUrl"] = "http://127.0.0.1:23353/ocr"
		self.plugin.ensureServiceMigrated()
		self.assertEqual(self.section["serviceUrl"], "http://127.0.0.1:23353")
		self.assertEqual(self.section["model"], "/ocr")

	def testBareHostGetsDefaultModel(self):
		del self.section["model"]
		self.section["serviceUrl"] = "http://h:23353"
		self.plugin.ensureServiceMigrated()
		self.assertEqual(self.section["serviceUrl"], "http://h:23353")
		self.assertEqual(self.section["model"], "/ocr")

	def testProxyPrefixPreserved(self):
		del self.section["model"]
		self.section["serviceUrl"] = "http://h/paddle/ocr"
		self.plugin.ensureServiceMigrated()
		self.assertEqual(self.section["serviceUrl"], "http://h/paddle")
		self.assertEqual(self.section["model"], "/ocr")
		self.assertEqual(
			self.plugin.HttpOcrClient(
				self.section["serviceUrl"], model=self.section["model"]
			).base_url,
			"http://h/paddle/ocr",
		)

	def testExistingModelIsNotOverwritten(self):
		self.section["serviceUrl"] = "http://h:23353"
		self.section["model"] = "/layout-parsing"
		self.plugin.ensureServiceMigrated()
		self.assertEqual(self.section["model"], "/layout-parsing")

	def testInvalidUrlKeepsRawAndGetsDefaultModel(self):
		del self.section["model"]
		self.section["serviceUrl"] = "ftp://host/x"
		self.plugin.ensureServiceMigrated()
		self.assertEqual(self.section["serviceUrl"], "ftp://host/x")
		self.assertEqual(self.section["model"], "/ocr")

	def testMakeOcrClientUsesComposedUrl(self):
		self.section["serviceUrl"] = "http://h:23353"
		self.section["model"] = "/ocr-acc"
		self.assertEqual(self.plugin.makeOcrClient().base_url, "http://h:23353/ocr-acc")

	def testMakeOcrClientLegacyFullUrlWithEmptyModel(self):
		"""老式"完整 URL + 空 model"仍按原样请求（向后兼容）。"""
		self.section["serviceUrl"] = "http://h:23353/layout-parsing"
		self.section["model"] = ""
		self.assertEqual(self.plugin.makeOcrClient().base_url, "http://h:23353/layout-parsing")


class RegionSelectionTests(Harness):
	def testOnlyCheckedRegionsAreCaptured(self):
		self.setRegions([
			("字幕", 1, 70, 0, 0, 0, True),
			("停用的", 1, 0, 0, 0, 0, False),
		])
		plugin = self.makePlugin([{"code": 100, "data": []}, {"code": 100, "data": []}])
		rects = plugin.getRegionRects()
		self.assertEqual(len(rects), 1)
		plugin.OcrScreen()
		self.assertEqual(len(self.grabbed), 1, "停用的区域不应被抓屏")

	def testDisabledOnlyMeansNoWork(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0, False), ("乙", 1, 0, 0, 0, 0, False)])
		plugin = self.makePlugin([])
		plugin.OcrScreen()
		self.assertEqual(self.grabbed, [])
		self.assertEqual(self.messages, [])

	def testEmptyRegionListIsSilent(self):
		self.section["regions"] = "[]"
		plugin = self.makePlugin([])
		plugin.OcrScreen()
		self.assertEqual(self.grabbed, [])
		self.assertEqual(self.messages, [])

	def testEachRegionUsesItsOwnCrop(self):
		self.setRegions([
			("上条", 1, 70, 0, 0, 0),
			("窄条", 1, 80, 10, 0, 0),
		])
		plugin = self.makePlugin([{"code": 100, "data": []}, {"code": 100, "data": []}])
		plugin.OcrScreen()
		self.assertEqual(len(self.grabbed), 2)
		first, second = self.grabbed
		self.assertEqual((first[1], first[3]), (756, 1080))
		self.assertEqual((second[1], second[3]), (864, 972))

	def testRegionTargetIsResolvedSeparately(self):
		self.setRegions([("窗口", 2, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 100, "data": []}])
		instance = self.modules["api"].getForegroundObject
		self.modules["api"].getForegroundObject = lambda: types.SimpleNamespace(
			location=self.modules["locationHelper"].RectLTWH(100, 200, 400, 300)
		)
		self.addCleanup(setattr, self.modules["api"], "getForegroundObject", instance)
		plugin.OcrScreen()
		self.assertEqual(tuple(self.grabbed[0]), (100, 200, 500, 500))

	def testUnresolvableTargetFallsBackToWholeScreen(self):
		self.setRegions([("导航对象", 0, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 100, "data": []}])
		plugin.OcrScreen()
		self.assertEqual(tuple(self.grabbed[0]), (0, 0, SCREEN[0], SCREEN[1]))


class MergedCaptureTests(Harness):
	"""合并区域截图（mergeCapture）：默认逐区域抓屏，开启后一次抓并集再裁剪。"""

	def twoFarApartRegions(self):
		# 上方一条（裁剪掉上方 70%）+ 下方一条（裁剪掉下方 80%），两条几乎不重叠
		self.setRegions([
			("上方", 1, 70, 0, 0, 0),
			("下方", 1, 0, 80, 0, 0),
		])

	def testDisabledByDefault(self):
		self.twoFarApartRegions()
		self.assertIs(self.section["mergeCapture"], False)
		plugin = self.makePlugin([{"code": 100, "data": []}, {"code": 100, "data": []}])
		plugin.OcrScreen()
		self.assertEqual(len(self.grabbed), 2, "关闭时应逐区域抓屏")
		self.assertEqual(self.crops, [])

	def testEnabledGrabsUnionOnceAndCropsPerRegion(self):
		self.twoFarApartRegions()
		self.section["mergeCapture"] = True
		plugin = self.makePlugin([{"code": 100, "data": []}, {"code": 100, "data": []}])
		plugin.OcrScreen()
		self.assertEqual(len(self.grabbed), 1, "开启后只应抓一次并集")
		self.assertEqual(tuple(self.grabbed[0]), (0, 0, SCREEN[0], SCREEN[1]))
		self.assertEqual(len(self.crops), 2, "每个区域各裁剪一次")
		self.assertEqual(self.crops[0], (0, 756, 1920, 1080))
		self.assertEqual(self.crops[1], (0, 0, 1920, 216))

	def testEnabledStillSpeaksChangedRegions(self):
		self.twoFarApartRegions()
		self.section["mergeCapture"] = True
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "上方的文字", "score": 0.9}]},
			{"code": 100, "data": [{"text": "下方的文字", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["上方的文字", "下方的文字"])

	def testSingleRegionIgnoresMergeOption(self):
		self.setRegions([("唯一", 1, 70, 0, 0, 0)])
		self.section["mergeCapture"] = True
		plugin = self.makePlugin([{"code": 100, "data": []}])
		plugin.OcrScreen()
		self.assertEqual(len(self.grabbed), 1)
		self.assertEqual(tuple(self.grabbed[0]), (0, 756, 1920, 1080))
		self.assertEqual(self.crops, [], "单区域无需合并截图")

	def testMergeFailureFallsBackToPerRegionCapture(self):
		self.twoFarApartRegions()
		self.section["mergeCapture"] = True
		plugin = self.makePlugin([{"code": 100, "data": []}, {"code": 100, "data": []}])
		realGrab = self.grab
		calls = []

		def flakyGrab(bbox=None):
			calls.append(bbox)
			if len(calls) == 1:
				raise OSError("merge capture failed")
			return realGrab(bbox)

		self.modules["PIL"].ImageGrab.grab = flakyGrab
		plugin.OcrScreen()
		self.assertEqual(len(calls), 3, "一次合并失败 + 两次逐区域抓屏")
		self.assertEqual(len(self.grabbed), 2)
		self.assertEqual(self.crops, [])


class PerRegionThresholdTests(Harness):
	"""每区域经典阈值：只影响该区域，且只对 classic 生效。"""

	def setRegionsWithThreshold(self, specs):
		"""specs: [(name, dedup, override)]；其余字段取默认。"""
		items = json.loads(
			regionJson([(name, 1, 0, 0, 0, 0, True, dedup) for name, dedup, _override in specs])
		)
		for item, (_name, _dedup, override) in zip(items, specs):
			item["classicThreshold"] = override
		self.section["regions"] = json.dumps(items, ensure_ascii=False)

	def testOverrideOnlyAffectsItsRegion(self):
		self.section["queueSpeech"] = True
		self.section["threshold"] = 0.5
		self.setRegionsWithThreshold([("A", "classic", 0.9), ("B", "classic", None)])
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "第1回合，攻击力 100", "score": 0.9}]},
			{"code": 100, "data": [{"text": "第1回合，攻击力 100", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.pending.extend([
			{"code": 100, "data": [{"text": "第2回合，攻击力 90", "score": 0.9}]},
			{"code": 100, "data": [{"text": "第2回合，攻击力 90", "score": 0.9}]},
		])
		plugin.OcrScreen()
		# A 覆盖 0.9：相似度约 0.783 < 0.9 → 朗读；B 继承 0.5：不朗读
		self.assertEqual(
			self.texts(),
			["第1回合，攻击力 100", "第1回合，攻击力 100", "第2回合，攻击力 90"],
		)

	def testInheritedRegionFollowsGlobal(self):
		self.section["queueSpeech"] = True
		self.section["threshold"] = 0.9
		self.setRegionsWithThreshold([("继承", "classic", None)])
		plugin = self.makePlugin([{"code": 100, "data": [{"text": "第1回合，攻击力 100", "score": 0.9}]}])
		plugin.OcrScreen()
		self.pending.extend([{"code": 100, "data": [{"text": "第2回合，攻击力 90", "score": 0.9}]}])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["第1回合，攻击力 100", "第2回合，攻击力 90"])

	def testOtherModesIgnoreOverride(self):
		"""commit 不读 threshold：覆盖成 0.01 或 1.0，行为应完全一致。"""
		results = []
		for override in (0.01, 1.0):
			with self.subTest(override=override):
				self.setUp()
				self.section["queueSpeech"] = True
				self.setRegionsWithThreshold([("稳定", "commit", override)])
				plugin = self.makePlugin([
					{"code": 100, "data": [{"text": "逐字出现的第一句", "score": 0.9}]},
				])
				plugin.OcrScreen()
				# 第二帧仍是同一句（commit 需要“文本不变”才释放）
				self.pending.append({"code": 100, "data": [{"text": "逐字出现的第一句", "score": 0.9}]})
				self.tick(2.0)  # 超过 dedupHoldMs，commit 应念一次
				plugin.OcrScreen()
				results.append(self.texts())
		self.assertEqual(results[0], results[1])
		self.assertIn("逐字出现的第一句", results[0])


class MultiRegionSpeechTests(Harness):
	def twoRegions(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0), ("乙", 1, 0, 0, 0, 0)])

	def testQueueModeSpeaksEveryChangedRegionInOrder(self):
		self.twoRegions()
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "甲文本", "score": 0.9}]},
			{"code": 100, "data": [{"text": "乙文本", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["甲文本", "乙文本"])
		self.assertTrue(all(message["priority"] == SpeechPriority.NEXT for message in self.messages))

	def testNonQueueModeSpeaksOnlyLastChangedRegion(self):
		self.twoRegions()
		self.section["queueSpeech"] = False
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "甲文本", "score": 0.9}]},
			{"code": 100, "data": [{"text": "乙文本", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["乙文本"])
		self.assertEqual(self.messages[0]["priority"], SpeechPriority.NOW)

	def testSingleRegionIsIdenticalInBothModes(self):
		for queue in (False, True):
			with self.subTest(queueSpeech=queue):
				self.setUp()
				self.setRegions([("唯一", 1, 0, 0, 0, 0)])
				self.section["queueSpeech"] = queue
				plugin = self.makePlugin([{"code": 100, "data": [{"text": "同一句", "score": 0.9}]}])
				plugin.OcrScreen()
				self.assertEqual(self.texts(), ["同一句"])

	def testEachRegionDeduplicatesIndependently(self):
		"""逐区域去重：两轮文本完全不同，四个区域的文本都应被朗读。"""
		self.twoRegions()
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "今天天气很好", "score": 0.9}]},
			{"code": 100, "data": [{"text": "明天要下雨了", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.pending.extend([
			{"code": 100, "data": [{"text": "外面开始下雪", "score": 0.9}]},
			{"code": 100, "data": [{"text": "我们出去散步", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(
			self.texts(),
			["今天天气很好", "明天要下雨了", "外面开始下雪", "我们出去散步"],
		)

	def testUnchangedRegionsStaySilent(self):
		self.twoRegions()
		self.section["queueSpeech"] = True
		# 两个区域各自的文本不同，且两轮之间都不变 → 第二轮一句都不该读
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "甲一样", "score": 0.9}]},
			{"code": 100, "data": [{"text": "乙一样", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.pending.extend([
			{"code": 100, "data": [{"text": "甲一样", "score": 0.9}]},
			{"code": 100, "data": [{"text": "乙一样", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["甲一样", "乙一样"])

	def testEmptyTextDoesNotConsumeSpeech(self):
		self.twoRegions()
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([
			{"code": 100, "data": []},
			{"code": 100, "data": [{"text": "只读这个", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["只读这个"])


class RegionFailureTests(Harness):
	def testOneFailingRegionDoesNotBlockOthers(self):
		self.setRegions([("坏的", 1, 0, 0, 0, 0), ("好的", 1, 0, 0, 0, 0)])
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([
			{"code": 902, "data": "连接失败：拒绝"},
			{"code": 100, "data": [{"text": "好消息", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertIn("好消息", self.texts())

	def testFailureAnnouncedOncePerRegion(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0), ("乙", 1, 0, 0, 0, 0)])
		plugin = self.makePlugin([
			{"code": 902, "data": "连接失败"},
			{"code": 902, "data": "连接失败"},
			{"code": 902, "data": "连接失败"},
			{"code": 902, "data": "连接失败"},
		])
		plugin.OcrScreen()
		plugin.OcrScreen()
		self.assertEqual(len(self.messages), 2, "两个区域各提示一次")
		for message in self.messages:
			self.assertIn("连接失败", message["text"])

	def testRecoveryResetsOnlyThatRegion(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0), ("乙", 1, 0, 0, 0, 0)])
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([
			{"code": 902, "data": "甲失败"},
			{"code": 100, "data": [{"text": "乙正常", "score": 0.9}]},
			# 第二轮：甲恢复且文本有变化，乙再次失败
			{"code": 100, "data": [{"text": "甲恢复", "score": 0.9}]},
			{"code": 902, "data": "乙失败"},
		])
		plugin.OcrScreen()
		plugin.OcrScreen()
		self.assertIn("乙正常", self.texts())
		self.assertIn("甲恢复", self.texts())
		self.assertTrue(any("乙失败" in text for text in self.texts()))

	def testExceptionInOneRegionIsCaught(self):
		self.setRegions([("爆炸", 1, 0, 0, 0, 0), ("正常", 1, 0, 0, 0, 0)])
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([
			OSError("grab failed"),
			{"code": 100, "data": [{"text": "仍然工作", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertIn("仍然工作", self.texts())

	def testFailureNotificationCanBeDisabled(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0)])
		self.section["notifyOnError"] = False
		plugin = self.makePlugin([{"code": 902, "data": "连接失败"}])
		plugin.OcrScreen()
		self.assertEqual(self.messages, [])

	def testErrorStateIsPrunedWhenRegionRemoved(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0), ("乙", 1, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 902, "data": "失败"}])
		plugin._errorReported = {1: True, 2: True}
		self.setRegions([("甲", 1, 0, 0, 0, 0)])
		plugin.OcrScreen()
		self.assertEqual(set(plugin._errorReported), {1})


class RegionEditingTests(Harness):
	def testRemovingRegionClearsItsDedupState(self):
		"""删掉的区域留下的去重状态要被清理；保留下来的继续跟踪。"""
		self.setRegions([("甲", 1, 0, 0, 0, 0), ("乙", 1, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 100, "data": []}])
		plugin._dedup.observe(1, "甲区域的旧文本", "classic", 1500, 0.0, 0.5)
		plugin._dedup.observe(2, "乙区域的旧文本", "classic", 1500, 0.0, 0.5)
		# 删掉"乙"（id=2），保留"甲"（id=1）
		self.setRegions([("甲", 1, 0, 0, 0, 0)])
		plugin.OcrScreen()
		self.assertIsNone(plugin._dedup.stateFor(2), "删除的区域状态应被清理")
		self.assertIsNotNone(plugin._dedup.stateFor(1), "保留的区域状态应继续存在")

	def testEmptyRegionListIsNotRemigrated(self):
		"""用户在界面上删光所有区域后，不应被当成旧配置而自动长回来。"""
		self.section["regions"] = "[]"
		self.plugin.ensureRegionsMigrated()
		self.assertEqual(self.plugin.loadRegions(), [])

	def testDisabledRegionKeepsItsState(self):
		"""停用区域只是不参与识别，状态保留（恢复勾选后仍记得上次读过什么）。"""
		self.setRegions([("甲", 1, 0, 0, 0, 0, False)])
		plugin = self.makePlugin([{"code": 100, "data": []}])
		plugin._dedup.observe(1, "旧文本", "classic", 1500, 0.0, 0.5)
		plugin.OcrScreen()
		self.assertIsNotNone(plugin._dedup.stateFor(1))

	def testStateSurvivesWhileRegionStaysEnabled(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0, True)])
		plugin = self.makePlugin([{"code": 100, "data": [{"text": "保留", "score": 0.9}]}])
		plugin.OcrScreen()
		self.assertIsNotNone(plugin._dedup.stateFor(1))
		self.assertEqual(plugin._dedup.stateFor(1).announcedFull, "保留")


class PerRegionStrategyTests(Harness):
	"""分区选择去重策略：这是 3.3.3 的核心验收。"""

	def typewriter(self, line, step=1):
		return [line[:index] for index in range(step, len(line) + 1, step)]

	def testGameRegionReadsWholeLineOnce(self):
		"""游戏区域选 commit：逐字出现的整句只在停顿后念一次完整句子。"""
		line = "你好冒险者"
		self.setRegions([("对话", 1, 0, 0, 0, 0, True, "commit", 1500)])
		frames = self.typewriter(line)
		results = [{"code": 100, "data": [{"text": text, "score": 0.9}]} for text in frames]
		results.extend({"code": 100, "data": [{"text": line, "score": 0.9}]} for _ in range(3))
		plugin = self.makePlugin(results)
		for _ in range(len(frames)):
			plugin.OcrScreen()
			self.tick()
		self.assertEqual(self.texts(), [], "打字过程中不应朗读")
		for _ in range(3):
			plugin.OcrScreen()
			self.tick()
		self.assertEqual(self.texts(), [line])

	def testSubtitleRegionKeepsClassicBehaviour(self):
		"""字幕区域选 classic：整行出现 + 相同内容不重复。"""
		self.setRegions([("字幕", 1, 0, 0, 0, 0, True, "classic")])
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "同样的字幕内容", "score": 0.9}]},
			{"code": 100, "data": [{"text": "同样的字幕内容", "score": 0.9}]},
		])
		plugin.OcrScreen()
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["同样的字幕内容"])

	def testTwoRegionsUseDifferentStrategies(self):
		"""同一个插件里，一个区域用 classic、一个用 commit，互不干扰。"""
		self.setRegions([
			("字幕", 1, 0, 0, 0, 0, True, "classic"),
			("对话", 1, 70, 0, 0, 0, True, "commit", 1500),
		])
		self.section["queueSpeech"] = True
		plugin = self.makePlugin([])
		# 字幕区域：整句立刻念（classic）
		self.pending.extend([
			{"code": 100, "data": [{"text": "字幕整句内容", "score": 0.9}]},
			{"code": 100, "data": [{"text": "你", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["字幕整句内容"])
		# 对话区域继续逐字增长，暂时不念
		self.pending.extend([
			{"code": 100, "data": [{"text": "字幕整句内容", "score": 0.9}]},
			{"code": 100, "data": [{"text": "你好", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["字幕整句内容"])

	def testIncrementalRegionReleasesAtPunctuation(self):
		self.setRegions([("长对话", 1, 0, 0, 0, 0, True, "incremental")])
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "你好，", "score": 0.9}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["你好，"])

	def testBrokenStrategyFallsBackToClassic(self):
		self.setRegions([("坏配置", 1, 0, 0, 0, 0, True, "nonsense")])
		loaded = self.plugin.loadRegions()[0]
		self.assertEqual(loaded["dedup"], "classic")

	def testDefaultHoldForNewRegion(self):
		region = self.plugin.regionModel.normalizeRegion({"id": 9})
		self.assertEqual(region["dedup"], "classic")
		self.assertEqual(region["dedupHoldMs"], 1500)

	def fakeClock(self):
		return None

	def advancePending(self):
		return None


class DeduplicationTests(Harness):
	"""相似度去重：这是"不重复朗读"的核心，且对中日韩短句极易踩坑。"""

	def testIdenticalCjkSubtitleIsSuppressed(self):
		line = "新句 字幕内容"
		self.setRegions([("字幕", 1, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 100, "data": [{"text": line, "score": 0.9}]}])
		plugin.OcrScreen()
		self.pending.extend([{"code": 100, "data": [{"text": line, "score": 0.9}]}])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), [line])

	def testShortIdenticalTextIsSuppressed(self):
		self.setRegions([("字幕", 1, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 100, "data": [{"text": "你好", "score": 0.9}]}])
		plugin.OcrScreen()
		self.pending.extend([{"code": 100, "data": [{"text": "你好", "score": 0.9}]}])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["你好"])

	def testDifferentTextIsSpoken(self):
		self.setRegions([("字幕", 1, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 100, "data": [{"text": "第一句话", "score": 0.9}]}])
		plugin.OcrScreen()
		self.pending.extend([{"code": 100, "data": [{"text": "另一句毫不相干的话", "score": 0.9}]}])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["第一句话", "另一句毫不相干的话"])

	def testSimilarityRatioFunction(self):
		ratio = self.plugin.similarityRatio
		self.assertEqual(ratio("同样的字", "同样的字"), 1.0)
		self.assertEqual(ratio("", "任何内容"), 0.0)
		self.assertLess(ratio("甲区域上一轮的旧文本内容", "乙区域今天的第一句话"), 0.5)
		# 关闭 autojunk：长 CJK 文本的相似度必须仍然算得出来
		longLine = "这是一个比较长的字幕句子用来验证相似度计算" * 8
		self.assertEqual(ratio(longLine, longLine), 1.0)
		self.assertLess(ratio(longLine, "完全不同的另一段文字" * 8), 0.5)

	def testThresholdZeroNeverSpeaksTwice(self):
		self.setRegions([("字幕", 1, 0, 0, 0, 0)])
		self.section["threshold"] = 0.0
		plugin = self.makePlugin([{"code": 100, "data": [{"text": "第一句话", "score": 0.9}]}])
		plugin.OcrScreen()
		self.pending.extend([{"code": 100, "data": [{"text": "完全不同的一句话", "score": 0.9}]}])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), [])

	def testThresholdOneAlwaysSpeaks(self):
		self.setRegions([("字幕", 1, 0, 0, 0, 0)])
		self.section["threshold"] = 1.0
		plugin = self.makePlugin([{"code": 100, "data": [{"text": "第一句话", "score": 0.9}]}])
		plugin.OcrScreen()
		self.pending.extend([{"code": 100, "data": [{"text": "完全不同的一句话", "score": 0.9}]}])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["第一句话", "完全不同的一句话"])


class CropMathTests(Harness):
	def testCropRectFromRegionsModule(self):
		model = self.plugin.regionModel
		region = model.normalizeRegion({"cropUp": 70})
		rect = model.regionRect(region, lambda target: None, screen=(1000, 1000))
		self.assertEqual((rect["left"], rect["top"], rect["width"], rect["height"]), (0, 700, 1000, 300))

	def testCropOnAllSides(self):
		model = self.plugin.regionModel
		region = model.normalizeRegion({"cropUp": 10, "cropDown": 20, "cropLeft": 10, "cropRight": 10})
		rect = model.regionRect(region, lambda target: None, screen=(1000, 1000))
		self.assertEqual((rect["left"], rect["top"]), (100, 100))
		self.assertEqual((rect["width"], rect["height"]), (800, 700))

	def testRegexFilterAppliedPerRegion(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0), ("乙", 1, 0, 0, 0, 0)])
		self.section["queueSpeech"] = True
		self.section["regexFilters"] = "广告"
		plugin = self.makePlugin([
			{"code": 100, "data": [{"text": "广告"}, {"text": "甲正文"}]},
			{"code": 100, "data": [{"text": "乙正文"}]},
		])
		plugin.OcrScreen()
		self.assertEqual(self.texts(), ["甲正文", "乙正文"])

	def testRegexThatMatchesEverythingGoesSilent(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0)])
		self.section["regexFilters"] = ".*"
		plugin = self.makePlugin([{"code": 100, "data": [{"text": "会被全部过滤"}]}])
		plugin.OcrScreen()
		self.assertEqual(self.messages, [])

	def testRegexFilterCollapsesWhitespace(self):
		self.section["regexFilters"] = "广告"
		plugin = self.makePlugin([])
		self.assertEqual(plugin.filterText("广告 正文"), "正文")

	def testInvalidRegexIsIgnored(self):
		self.section["regexFilters"] = "([unclosed"
		plugin = self.makePlugin([])
		self.assertEqual(plugin.filterText("正文"), "正文")

	def testJpegEncodingUsesConfiguredFormat(self):
		self.setRegions([("甲", 1, 0, 0, 0, 0)])
		plugin = self.makePlugin([{"code": 100, "data": []}])
		plugin.OcrScreen()
		self.assertEqual(self.saveCalls[0]["format"], "JPEG")


class _FakeThread:
	def __init__(self, target=None, daemon=None, **_kwargs):
		self.target = target

	def start(self):
		return None


class ScriptToggleTests(Harness):
	def setUp(self):
		super().setUp()
		self.started: list = []

		def Thread(target=None, daemon=None, **kwargs):  # noqa: N802 - 顶替 threading.Thread
			self.started.append(target)
			return _FakeThread()

		self.plugin.threading = types.SimpleNamespace(Thread=Thread)
		self.addCleanup(setattr, self.plugin, "active", False)

	def makeInstance(self):
		instance = self.plugin.GlobalPlugin.__new__(self.plugin.GlobalPlugin)
		instance.ocr = FakeClient([], self.saveCalls)
		instance._dedup = self.plugin.dedup.DedupEngine()
		instance._errorReported = {}
		return instance

	def testToggleStartsThenStops(self):
		plugin = self.makeInstance()
		plugin.script_ReadLiveOcr(None)
		self.assertTrue(self.plugin.active)
		self.assertEqual(len(self.started), 1)
		self.assertIn("lion started", self.texts()[-1])
		plugin.script_ReadLiveOcr(None)
		self.assertFalse(self.plugin.active)
		self.assertIn("lion stopped", self.texts()[-1])

	def testStartResetsDedupState(self):
		plugin = self.makeInstance()
		plugin._dedup.observe(1, "上一轮的旧文本", "classic", 1500, 0.0, 0.5)
		self.assertIsNotNone(plugin._dedup.stateFor(1))
		plugin.script_ReadLiveOcr(None)
		self.assertIsNone(plugin._dedup.stateFor(1), "开始识别时应清空上一轮状态")

	def testStartMigratesEmptyRegions(self):
		self.section["regions"] = ""
		plugin = self.makeInstance()
		plugin.script_ReadLiveOcr(None)
		self.assertEqual(len(self.plugin.loadRegions()), 1)

	def testRefusesWhenScreenCurtainEnabled(self):
		self.modules["screenCurtain"].screenCurtain.enabled = True
		self.addCleanup(setattr, self.modules["screenCurtain"].screenCurtain, "enabled", False)
		plugin = self.makeInstance()
		plugin.script_ReadLiveOcr(None)
		self.assertFalse(self.plugin.active)
		self.assertEqual(self.started, [])
		self.assertIn("screen curtain", " ".join(self.texts()).lower())

	def testTerminateStopsAndCancelsSpeech(self):
		cancelled = []
		self.modules["speech"].cancelSpeech = lambda: cancelled.append(True)
		self.addCleanup(setattr, self.modules["speech"], "cancelSpeech", lambda: None)
		self.section["regions"] = regionJson([("甲", 1, 0, 0, 0, 0)])
		plugin = self.makeInstance()
		plugin.terminate()
		self.assertFalse(self.plugin.active)
		self.assertEqual(cancelled, [True])


class ThreadedLoopTests(Harness):
	def testLoopExitsWhenNotActive(self):
		self.plugin.active = True
		plugin = self.makePlugin([{"code": 100, "data": []}])
		calls = []

		def ocrScreen():
			calls.append(1)
			self.plugin.active = False

		plugin.OcrScreen = ocrScreen
		plugin.ocrLoop()
		self.assertEqual(len(calls), 1)
		self.assertFalse(self.plugin.active)

	def testWaitUsesConfiguredInterval(self):
		"""轮询间隔用可被“停止”立刻唤醒的 Event.wait，而不是不可中断的 time.sleep。"""
		self.plugin.active = True
		plugin = self.makePlugin([])
		waited = []

		def wait(seconds):
			waited.append(seconds)
			self.plugin.active = False

		plugin._stopEvent = types.SimpleNamespace(clear=lambda: None, set=lambda: None, wait=wait)

		def ocrScreen():
			return None

		plugin.OcrScreen = ocrScreen
		self.section["interval"] = 2.5
		plugin.ocrLoop()
		self.assertEqual(waited, [2.5])


class ServiceProbeTests(Harness):
	"""可选的在线用例：真实服务可用时才跑。"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if os.environ.get("LION_SKIP_SERVICE_PROBE"):
			cls.skip = True
		else:
			cls.skip = False

	def setUp(self):
		super().setUp()
		if getattr(self, "skip", False):
			self.skipTest("LION_SKIP_SERVICE_PROBE 已设置")

	def testScanOneRegionAgainstLiveService(self):
		"""走完整链路：真实抓屏 → 真实编码 → 真实 HTTP 请求 → 解析。"""
		import pathlib

		if not REAL_PIL:
			self.skipTest("未安装 Pillow，无法做真实抓屏")
		tokenPath = os.environ.get("LION_TEST_TOKEN_FILE", "")
		if not tokenPath:
			self.skipTest("未设置 LION_TEST_TOKEN_FILE")
		tokenFile = pathlib.Path(tokenPath)
		if not tokenFile.is_file():
			self.skipTest("未找到令牌文件")
		self.section["serviceToken"] = tokenFile.read_text(encoding="utf-8").strip()
		self.setRegions([("字幕条", 1, 70, 0, 0, 0)])
		# 这个用例要用真实图像（其余用例用假图像，无法被服务端解码）。
		# Pillow 内部会做 isinstance(img, PIL.Image.Image) 检查，所以必须把真实的
		# PIL.Image / PIL.ImageFile 装回桩模块，否则 save() 会报 "cannot import name"。
		real = RealImageGrab.grab()
		self.captureImage = lambda bbox=None: real.crop(bbox)
		self.modules["PIL"].Image = RealPILImage
		self.modules["PIL"].ImageFile = RealPILImageFile
		# 让 Pillow 的编码插件可以被 import 到（见模块顶部注释）
		for name, module in REAL_PIL_MODULES.items():
			sys.modules.setdefault(name, module)
		sys.modules["PIL"] = self.modules["PIL"]
		instance = self.plugin.GlobalPlugin.__new__(self.plugin.GlobalPlugin)
		instance._dedup = self.plugin.dedup.DedupEngine()
		instance._errorReported = {}
		instance.ocr = self.plugin.makeOcrClient()
		changed, failures = instance.scanRegions()
		self.assertEqual(failures, [], f"真实服务不应失败：{failures}")
		self.assertEqual(len(self.grabbed), 1)
		for text in changed:
			self.assertTrue(text.strip())


if __name__ == "__main__":
	unittest.main(verbosity=2)
