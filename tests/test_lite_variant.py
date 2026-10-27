# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""清亮版（LION 修改版 lite）的离线测试。

lite 版不把 `ocrClient.py` 存进自己的源码树：打包时从完整版复制（单一真源）。
所以这里先搭一个临时 staging（lite 源码 + 完整版客户端），再把它作为 `lionlite`
包加载，最后检查界面文案、配置、客户端一致性，以及产物包（若已构建）。
"""

from __future__ import annotations

import ast
import gettext
import hashlib
import os
import re
import shutil
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
LITE_SRC = os.path.join(ROOT, "variants", "lite", "src")
FULL_CLIENT = os.path.join(ROOT, "src", "globalPlugins", "lion", "ocrClient.py")
DIST = os.path.join(ROOT, "dist")
LITE_NAME = "LION-modified-lite"
LITE_VERSION = "3.3.8"
LITE_BUNDLE = os.path.join(DIST, "%s-%s.nvda-addon" % (LITE_NAME, LITE_VERSION))
LITE_INSTALL_TASKS = os.path.join(LITE_SRC, "installTasks.py")

#: 官方 dsh 徽章：插件手册内嵌本地 PNG 副本（与完整版 src/doc/dsh-badge.png 逐字节一致）。
DSH_PROJECT_URL = "https://github.com/deepseek-ai/deepseek-harness"
DSH_BADGE_LOCAL = "[![powered by dsh](../dsh-badge.png)](%s)" % DSH_PROJECT_URL
DSH_BADGE_PNG = os.path.join(LITE_SRC, "doc", "dsh-badge.png")
#: 官方 PNG 的 sha256（来源 dsh-badge 技能 assets，726×120 / 12339 字节）。
DSH_BADGE_SHA256 = "f2c4f5ec9cbe847c0c763545c4d839efa8485bc74203733d0a0e8259f233c653"

#: 3.3.8 起弃用的旧 AI 声明字面量，lite 文档与 manifest 中不得再出现。
LEGACY_DISCLOSURE_LITERALS = (
	"AI 创作声明",
	"代码声明",
	"模型版本",
	"AI disclosure",
	"Code disclosure",
	"Model version",
	"DeepSeek-V4",
)

_ABSENT = object()

sys.path.insert(0, HERE)
import nvda_stubs  # noqa: E402

_configobj = nvda_stubs.loadConfigObj()
requiresConfigObj = unittest.skipIf(_configobj is None, "未找到可用的 configobj")
if _configobj is not None:
	ConfigObj, Validator, VdtTypeError, VdtValueError = _configobj

_MOCKED_MODULES = []


def _installMocks():
	"""装 NVDA / wx 模块桩，让 lite 的 __init__ 与 lionGui 能在普通 Python 下加载。"""
	nvda_stubs.install()
	guiModule = types.ModuleType("gui")
	guiModule.settingsDialogs = types.SimpleNamespace(
		NVDASettingsDialog=types.SimpleNamespace(categoryClasses=[]),
		SettingsPanel=type("SettingsPanel", (), {}),
		SettingsDialog=type("SettingsDialog", (), {}),
	)
	guiModule.guiHelper = types.ModuleType("gui.guiHelper")
	guiModule.nvdaControls = types.ModuleType("gui.nvdaControls")
	modules = {
		"gui": guiModule,
		"gui.guiHelper": guiModule.guiHelper,
		"gui.nvdaControls": guiModule.nvdaControls,
		"wx": mock.MagicMock(name="wx"),
		"api": types.ModuleType("api"),
		"tones": types.ModuleType("tones"),
		"ui": types.ModuleType("ui"),
		"scriptHandler": types.ModuleType("scriptHandler"),
		"speech": types.ModuleType("speech"),
		"globalPluginHandler": types.ModuleType("globalPluginHandler"),
		"screenCurtain": types.ModuleType("screenCurtain"),
		"locationHelper": types.ModuleType("locationHelper"),
		"PIL": types.ModuleType("PIL"),
	}
	modules["api"].getNavigatorObject = lambda: None
	modules["api"].getForegroundObject = lambda: None
	modules["api"].getFocusObject = lambda: None
	modules["tones"].beep = lambda *args: None
	modules["ui"].message = lambda *args, **kwargs: None
	modules["speech"].cancelSpeech = lambda: None
	modules["screenCurtain"].screenCurtain = types.SimpleNamespace(enabled=False)

	def script(*args, **kwargs):
		def decorator(func):
			func.gestures = kwargs.get("gestures", [])
			return func

		return decorator

	modules["scriptHandler"].script = script

	class FakeGlobalPlugin:
		def __init__(self):
			pass

	modules["globalPluginHandler"].GlobalPlugin = FakeGlobalPlugin

	class RectLTWH:
		def __init__(self, left, top, width, height):
			self.left, self.top = left, top
			self.width, self.height = width, height
			self.right, self.bottom = left + width, top + height

		@property
		def location(self):
			return self

	modules["locationHelper"].RectLTWH = RectLTWH
	modules["PIL"].ImageGrab = types.SimpleNamespace(grab=lambda bbox=None: None)

	for name, module in modules.items():
		sys.modules[name] = module
		_MOCKED_MODULES.append(name)
	return modules


def _stage():
	"""把 lite 源码树 + 完整版 ocrClient.py 复制到临时目录，返回 (临时根, 包目录)。"""
	tempRoot = tempfile.mkdtemp(prefix="lionlite-test-")
	src = os.path.join(tempRoot, "src")
	shutil.copytree(LITE_SRC, src)
	shutil.copyfile(FULL_CLIENT, os.path.join(src, "globalPlugins", "lionlite", "ocrClient.py"))
	return tempRoot, src


def _moduleLevelDict(path, name):
	"""取出模块顶层 `name = {...}` 的 AST 节点（只解析不执行，不需要 NVDA 环境）。"""
	with open(path, encoding="utf-8") as handle:
		tree = ast.parse(handle.read(), filename=path)
	for node in tree.body:
		if not isinstance(node, ast.Assign):
			continue
		for target in node.targets:
			if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Dict):
				return node.value
	raise AssertionError("%s 里没有顶层字典赋值 %s" % (path, name))


def _loadInstallTasks():
	"""从 lite 源码树直接加载 installTasks.py（NVDA 只会把 it 当模块执行，不 import 插件包）。"""
	import importlib.util

	spec = importlib.util.spec_from_file_location("lionlite_installTasks", LITE_INSTALL_TASKS)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def _loadPackage(src):
	"""把 staging 里的 lionlite 包加载成模块。"""
	import importlib.util

	packageDir = os.path.join(src, "globalPlugins", "lionlite")
	spec = importlib.util.spec_from_file_location(
		"lionlite",
		os.path.join(packageDir, "__init__.py"),
		submodule_search_locations=[packageDir],
	)
	module = importlib.util.module_from_spec(spec)
	for name in ("lionlite", "lionlite.lionGui", "lionlite.ocrClient"):
		sys.modules.pop(name, None)
	sys.modules["lionlite"] = module
	spec.loader.exec_module(module)
	return module


class LiteHarness(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.modules = _installMocks()
		cls.tempRoot, cls.src = _stage()
		cls.package = _loadPackage(cls.src)

	@classmethod
	def tearDownClass(cls):
		for name in ("lionlite", "lionlite.lionGui", "lionlite.ocrClient"):
			sys.modules.pop(name, None)
		shutil.rmtree(cls.tempRoot, ignore_errors=True)
		for name in reversed(_MOCKED_MODULES):
			sys.modules.pop(name, None)
		_MOCKED_MODULES.clear()

	def setUp(self):
		# 每个用例前把配置写成 lite 的默认值
		conf = self.package.config.conf[LITE_CONFIG_SECTION]
		conf.clear()
		conf.update({
			"serviceUrl": "http://127.0.0.1:23353",
			"model": "/ocr",
			"serviceToken": "",
			"requestTimeout": 15.0,
			"imageFormat": 1,
			"visualize": False,
			"scoreThresh": 0.0,
			"detLimitSideLen": 0,
			"notifyOnError": True,
			"cropUp": 0, "cropLeft": 0, "cropRight": 0, "cropDown": 0,
			"target": 1, "threshold": 0.5, "interval": 1.0, "regexFilters": "",
		})


LITE_CONFIG_SECTION = "LION-modified-lite"


class ManifestTests(LiteHarness):
	@requiresConfigObj
	def testManifestIdentity(self):
		manifest = ConfigObj(os.path.join(self.src, "manifest.ini"), encoding="utf-8",
							 list_values=False, interpolation=False)
		manifest.configspec = ConfigObj(
			["name = string()", "summary = string()", "version = string()",
			 "author = string()", "description = string()", "changelog = string()",
			 "minimumNVDAVersion = string()", "lastTestedNVDAVersion = string()"],
			list_values=False,
		)
		validator = Validator()
		self.assertIs(manifest.validate(validator, copy=True), True, "lite manifest 校验失败")
		self.assertEqual(manifest["name"], LITE_NAME)
		self.assertEqual(str(manifest["summary"]).strip('"'), "LION 修改版 lite")
		self.assertEqual(manifest["version"], LITE_VERSION)
		self.assertEqual(manifest["docFileName"], "readme.html")
		self.assertIn("xiaoshunxing", manifest["author"])
		self.assertIn("Stefan Moisei", manifest["author"], "上游作者必须保留")
		self.assertEqual(manifest["minimumNVDAVersion"], "2026.1", "最低支持版本保持 2026.1")
		self.assertEqual(manifest["lastTestedNVDAVersion"], "2026.3")
		self.assertIn("3.3.8", manifest["description"])
		self.assertIn("版本 3.3.8", manifest["changelog"])

	def testNoLegacyAiDeclaration(self):
		"""3.3.8 起旧 AI 声明行改为 dsh 署名；lite 的 manifest 里不得残留旧字面量。"""
		for path in (
			os.path.join(LITE_SRC, "manifest.ini"),
			os.path.join(LITE_SRC, "locale", "zh_CN", "manifest.ini"),
		):
			with open(path, encoding="utf-8") as handle:
				text = handle.read()
			with self.subTest(path=path):
				for literal in LEGACY_DISCLOSURE_LITERALS:
					self.assertNotIn(literal, text)
				self.assertIn("Powered by dsh", text)

	@requiresConfigObj
	def testLocalisedManifestParses(self):
		"""中文翻译清单必须能被 ConfigObj 解析；NVDA 安装时会读取它。"""
		path = os.path.join(LITE_SRC, "locale", "zh_CN", "manifest.ini")
		with open(path, "rb") as handle:
			translated = ConfigObj(handle, encoding="utf-8", default_encoding="utf-8")
		self.assertEqual(str(translated["summary"]).strip('"'), "LION 修改版 lite")
		self.assertIn("3.3.8", translated["description"])
		self.assertIn("版本 3.3.8", translated["changelog"])

		if os.path.isfile(LITE_BUNDLE):
			import zipfile

			with zipfile.ZipFile(LITE_BUNDLE) as archive:
				raw = archive.read("locale/zh_CN/manifest.ini")
			translated = ConfigObj(
				raw.decode("utf-8").splitlines(), encoding="utf-8", default_encoding="utf-8"
			)
			self.assertEqual(str(translated["summary"]).strip('"'), "LION 修改版 lite")

	def testConfigSectionIsIndependent(self):
		self.assertEqual(self.package.CONFIG_SECTION, LITE_CONFIG_SECTION)
		# 与完整版的配置节不能相同
		self.assertNotEqual(self.package.CONFIG_SECTION, "LION-modified")

	def testConfigKeys(self):
		spec = self.package.confspec
		self.assertEqual(len(spec), 17)
		self.assertIn("model", spec)
		self.assertIn("/ocr", spec["model"])
		self.assertIn("http://127.0.0.1:23353", spec["serviceUrl"])
		for key in ("regions", "queueSpeech", "dedup"):
			self.assertNotIn(key, spec, "lite 版不应有完整版的多区域/排队/去重键")
		for key in ("target", "cropUp", "cropDown", "cropLeft", "cropRight",
					"interval", "threshold", "regexFilters"):
			self.assertIn(key, spec)


class InstallTasksTests(LiteHarness):
	"""installTasks.py：安装时把配置节登记为基础配置，旧版 NVDA 跳过，且绝不抛异常。"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.installTasks = _loadInstallTasks()
		cls.configModule = sys.modules["config"]

	def setUp(self):
		super().setUp()
		self.calls = []
		self._savedConfigSections = getattr(self.configModule, "configSections", _ABSENT)
		if hasattr(self.configModule, "configSections"):
			del self.configModule.configSections

	def tearDown(self):
		if self._savedConfigSections is _ABSENT:
			if hasattr(self.configModule, "configSections"):
				del self.configModule.configSections
		else:
			self.configModule.configSections = self._savedConfigSections

	def _fakeConfigSections(self, error=None):
		calls = self.calls

		class FakeConfigSections:
			def registerSection(self, sectionName, sectionSpec, isBaseOnly=False):
				calls.append((sectionName, sectionSpec, isBaseOnly))
				if error is not None:
					raise error

		return FakeConfigSections()

	def testSpecMatchesPluginConfspec(self):
		"""installTasks 内联的 SPEC 必须与插件 confspec 逐项一致（17 项）。

		用 AST 比较而不是 import：不需要 NVDA 环境，也能发现漏改哪一处。
		"""
		plugin = _moduleLevelDict(
			os.path.join(LITE_SRC, "globalPlugins", "lionlite", "__init__.py"), "confspec"
		)
		install = _moduleLevelDict(LITE_INSTALL_TASKS, "SPEC")
		self.assertEqual(ast.dump(plugin), ast.dump(install), "installTasks.SPEC 与插件 confspec 不一致")
		self.assertEqual(len(ast.literal_eval(install)), 17)

	def testSectionMatchesPlugin(self):
		self.assertEqual(self.installTasks.SECTION, LITE_CONFIG_SECTION)
		self.assertEqual(self.installTasks.SECTION, self.package.CONFIG_SECTION)

	def testOnInstallWithoutApiDoesNothing(self):
		"""NVDA 2026.1/2026.2 没有 configSections API：静默跳过，不抛异常。"""
		self.assertFalse(hasattr(self.configModule, "configSections"))
		self.installTasks.onInstall()
		self.assertEqual(self.calls, [])

	def testOnInstallRegistersBaseOnlySection(self):
		"""NVDA 2026.3+：以 isBaseOnly=True 登记配置节。"""
		self.configModule.configSections = self._fakeConfigSections()
		self.installTasks.onInstall()
		self.assertEqual(len(self.calls), 1)
		section, spec, isBaseOnly = self.calls[0]
		self.assertEqual(section, LITE_CONFIG_SECTION)
		self.assertIs(isBaseOnly, True, "配置节必须固定为基础配置")
		self.assertEqual(spec, self.package.confspec)

	def testOnInstallSwallowsExceptions(self):
		"""registerSection 抛异常时必须被吞掉：安装任务异常会让 NVDA 回滚整包安装。"""
		self.configModule.configSections = self._fakeConfigSections(error=RuntimeError("boom"))
		self.installTasks.onInstall()
		self.assertEqual(len(self.calls), 1)

	def testNoUnsafeHooksOrImports(self):
		with open(LITE_INSTALL_TASKS, encoding="utf-8") as handle:
			source = handle.read()
		self.assertNotIn("globalPlugins", source, "installTasks 不应 import 插件包（旧模块残留风险）")
		self.assertNotIn("unregisterSection", source, "升级首启时卸载清理会误删新版刚登记的配置节")
		self.assertNotIn("onUninstall", source, "配置节不做卸载清理，保留给新版本继续使用")


class BaseOnlySectionTests(LiteHarness):
	"""ensureBaseOnlySection / logActiveProfileSections：NVDA 2026.3+ 配置节登记。"""

	def setUp(self):
		super().setUp()
		self.configModule = sys.modules["config"]
		self.conf = self.configModule.conf
		self.calls = []
		self.logs = []
		self._savedConfigSections = getattr(self.configModule, "configSections", _ABSENT)
		if hasattr(self.configModule, "configSections"):
			del self.configModule.configSections
		self._savedBaseOnly = getattr(self.conf, "BASE_ONLY_SECTIONS", _ABSENT)
		if hasattr(self.conf, "BASE_ONLY_SECTIONS"):
			del self.conf.BASE_ONLY_SECTIONS
		self._savedProfiles = getattr(self.conf, "profiles", _ABSENT)
		if hasattr(self.conf, "profiles"):
			del self.conf.profiles
		self._savedLogHandler = self.package.logHandler
		self.package.logHandler = types.SimpleNamespace(
			log=types.SimpleNamespace(
				info=lambda message, *args: self.logs.append(message % args if args else message),
				debugWarning=lambda message, *args, **kwargs: self.logs.append(message % args if args else message),
			)
		)

	def tearDown(self):
		self.package.logHandler = self._savedLogHandler
		for obj, name, saved in (
			(self.configModule, "configSections", self._savedConfigSections),
			(self.conf, "BASE_ONLY_SECTIONS", self._savedBaseOnly),
			(self.conf, "profiles", self._savedProfiles),
		):
			if saved is _ABSENT:
				if hasattr(obj, name):
					delattr(obj, name)
			else:
				setattr(obj, name, saved)

	def _fakeConfigSections(self):
		calls = self.calls

		class FakeConfigSections:
			def registerSection(self, sectionName, sectionSpec, isBaseOnly=False):
				calls.append((sectionName, sectionSpec, isBaseOnly))

		return FakeConfigSections()

	def testRegistersWhenApiPresentAndNotRegistered(self):
		self.configModule.configSections = self._fakeConfigSections()
		self.conf.BASE_ONLY_SECTIONS = set()
		self.package.ensureBaseOnlySection()
		self.assertEqual(self.calls, [(LITE_CONFIG_SECTION, self.package.confspec, True)])
		self.assertTrue(any("重启" in message for message in self.logs), self.logs)

	def testSkipsWhenAlreadyBaseOnly(self):
		self.configModule.configSections = self._fakeConfigSections()
		self.conf.BASE_ONLY_SECTIONS = {LITE_CONFIG_SECTION}
		self.package.ensureBaseOnlySection()
		self.assertEqual(self.calls, [], "已登记为基础配置时不应重复写 customSections.yaml")
		self.assertTrue(any("已登记" in message for message in self.logs), self.logs)

	def testSkipsWithoutApi(self):
		"""NVDA 2026.1/2026.2：没有 API 时直接跳过，插件照常加载。"""
		self.assertFalse(hasattr(self.configModule, "configSections"))
		self.package.ensureBaseOnlySection()
		self.assertEqual(self.calls, [])
		self.assertTrue(any("配置档案" in message for message in self.logs), self.logs)

	def testLogsProfileContainingSection(self):
		"""活动配置档案里残留本插件节时，日志说明这些设置当前是否被忽略。"""
		self.configModule.configSections = self._fakeConfigSections()
		self.conf.BASE_ONLY_SECTIONS = {LITE_CONFIG_SECTION}
		self.conf.profiles = [{}, {LITE_CONFIG_SECTION: {"interval": "2.0"}}]
		self.package.logActiveProfileSections()
		self.assertTrue(any("已忽略" in message for message in self.logs), self.logs)

	def testNoProfileLogWithoutRelevantProfile(self):
		self.configModule.configSections = self._fakeConfigSections()
		self.conf.BASE_ONLY_SECTIONS = {LITE_CONFIG_SECTION}
		self.conf.profiles = [{}, {"other": {}}]
		self.package.logActiveProfileSections()
		self.assertEqual(self.logs, [])


class ClientSyncTests(LiteHarness):
	def testSourceTreeHasNoClientCopy(self):
		"""lite 源码树不存 ocrClient.py，避免和完整版不同步。"""
		self.assertFalse(
			os.path.exists(os.path.join(LITE_SRC, "globalPlugins", "lionlite", "ocrClient.py"))
		)

	def testStagedClientIsIdenticalToFull(self):
		staged = os.path.join(self.src, "globalPlugins", "lionlite", "ocrClient.py")
		with open(staged, "rb") as handle:
			stagedBytes = handle.read()
		with open(FULL_CLIENT, "rb") as handle:
			fullBytes = handle.read()
		self.assertEqual(stagedBytes, fullBytes, "lite 的客户端必须与完整版逐字节一致")


class ClientCompositionTests(LiteHarness):
	def testMakeOcrClientUsesBaseAndModel(self):
		conf = self.package.config.conf[LITE_CONFIG_SECTION]
		conf["serviceUrl"] = "http://127.0.0.1:23353"
		conf["model"] = "/layout-parsing"
		client = self.package.makeOcrClient()
		self.assertEqual(client.base_url, "http://127.0.0.1:23353/layout-parsing")
		self.assertEqual(client.service_base, "http://127.0.0.1:23353")

	def testFullUrlModelWins(self):
		conf = self.package.config.conf[LITE_CONFIG_SECTION]
		conf["serviceUrl"] = ""
		conf["model"] = "http://other:9000/ocr"
		client = self.package.makeOcrClient()
		self.assertEqual(client.base_url, "http://other:9000/ocr")


def _ocrResult(text=""):
	"""构造一次 OCR 成功结果；空字符串表示服务返回空结果。"""
	return {"code": 100, "data": [{"text": text}] if text else []}


class FakeOcrClient:
	"""按脚本返回 OCR 结果，避免在测试里发真实网络请求。"""

	def __init__(self):
		self.results: list = []
		self.lastErrorBrief = "stub error"

	def feed(self, *results):
		self.results.extend(results)

	def encodeImage(self, image):
		return b""

	def runBytes(self, data):
		return self.results.pop(0)


class PluginBehaviourTests(LiteHarness):
	"""直接驱动 GlobalPlugin.OcrScreen：OCR 结果与时钟都由测试控制。"""

	def setUp(self):
		super().setUp()
		self.panelClasses = self.modules["gui"].settingsDialogs.NVDASettingsDialog.categoryClasses
		self.panelClasses.clear()
		# prevString 是模块级状态，用例之间必须复位
		self.package.prevString = ""
		self.messages: list = []
		self.modules["ui"].message = lambda text, **kwargs: self.messages.append(text)
		self.clock = [1000.0]
		self.ocr = FakeOcrClient()
		self.plugin = self.package.GlobalPlugin()
		self.plugin.ocr = self.ocr
		# 虚拟时钟：空帧宽限不依赖真实墙钟
		self.plugin._now = lambda: self.clock[0]

	def tearDown(self):
		self.panelClasses.clear()

	def tick(self, seconds=1.0):
		self.clock[0] += seconds
		return self.clock[0]

	def speakFrame(self, text):
		self.ocr.feed(_ocrResult(text))
		self.plugin.OcrScreen()

	def emptyFrame(self, seconds=1.0):
		self.tick(seconds)
		self.ocr.feed(_ocrResult(""))
		self.plugin.OcrScreen()

	def testLongRepetitiveTextSimilarityStillComparable(self):
		"""≥200 字、重复字多的长文本必须仍算得出相似度。

		difflib 默认 autojunk 会把重复字当噪声：同一句只是多识别出一段前缀，也会被
		算成"完全不同"（相似度 0），于是每帧重读整段字幕。这里锁定 autojunk=False。
		"""
		line = "欢迎收看本期节目感谢大家观看" * 18
		self.assertGreaterEqual(len(line), 200)
		self.speakFrame(line)
		# 同一句，本帧多识别出一小段前缀：相似度应仍然很高，不重复朗读
		self.tick(1.0)
		self.ocr.feed(_ocrResult("本帧：" + line))
		self.plugin.OcrScreen()
		self.assertEqual(self.messages, [line])
		# 真正换成另一句长字幕时必须能朗读，不能被相似度卡住
		newLine = "今天的节目到这里就全部结束了感谢收看" * 12
		self.tick(1.0)
		self.ocr.feed(_ocrResult(newLine))
		self.plugin.OcrScreen()
		self.assertEqual(self.messages, [line, newLine])

	def testSingleEmptyFrameKeepsPreviousString(self):
		"""单帧空结果（OCR 抖动）不清 prevString，同一句回来不重复朗读。"""
		line = "第一句很长很长的字幕内容"
		self.speakFrame(line)
		self.emptyFrame(1.0)
		self.ocr.feed(_ocrResult(line))
		self.plugin.OcrScreen()
		self.assertEqual(self.messages, [line])
		self.assertEqual(self.package.prevString, line)

	def testSustainedEmptyClearsPreviousStringAfterGrace(self):
		"""连续空结果满 EMPTY_GRACE_SECONDS 才清空 prevString，之后同句重新朗读。"""
		line = "第二句同样很长的字幕内容"
		self.speakFrame(line)
		self.emptyFrame(1.0)  # 记下空帧起点
		self.emptyFrame(1.0)  # 才 1 秒，仍不清
		self.assertEqual(self.package.prevString, line)
		self.emptyFrame(1.0)  # 满 2 秒，清空
		self.assertEqual(self.package.prevString, "")
		self.ocr.feed(_ocrResult(line))
		self.plugin.OcrScreen()
		self.assertEqual(self.messages, [line, line])

	def testFilteredEmptyCountsAsEmptyFrame(self):
		"""正则过滤后为空的文本也按空帧处理，同样有 2 秒宽限。"""
		conf = self.package.config.conf[LITE_CONFIG_SECTION]
		conf["regexFilters"] = "广告"
		line = "这是正文内容"
		self.speakFrame(line)
		for _ in range(3):
			self.tick(1.0)
			self.ocr.feed(_ocrResult("广告"))  # 过滤后为空
			self.plugin.OcrScreen()
		self.assertEqual(self.package.prevString, "")
		self.ocr.feed(_ocrResult(line))
		self.plugin.OcrScreen()
		self.assertEqual(self.messages, [line, line])


class PluginLifecycleTests(LiteHarness):
	"""设置面板注册/注销与 terminate 的语音清理。"""

	def setUp(self):
		super().setUp()
		self.panelClasses = self.modules["gui"].settingsDialogs.NVDASettingsDialog.categoryClasses
		self.panelClasses.clear()
		self.cancelCalls: list = []
		self.modules["speech"].cancelSpeech = lambda: self.cancelCalls.append(True)

	def tearDown(self):
		self.panelClasses.clear()

	def testPanelRegistrationIsIdempotent(self):
		"""重复构造（模拟插件重载后旧模块残留）只保留一个设置面板条目。"""
		self.package.GlobalPlugin()
		self.package.GlobalPlugin()
		self.assertEqual(self.panelClasses.count(self.package.LIONSettingsPanel), 1)

	def testTerminateCancelsSpeech(self):
		plugin = self.package.GlobalPlugin()
		plugin.terminate()
		self.assertEqual(len(self.cancelCalls), 1)
		self.assertNotIn(self.package.LIONSettingsPanel, self.panelClasses)

	def testTerminateRemovesDuplicatePanelEntries(self):
		plugin = self.package.GlobalPlugin()
		self.panelClasses.append(self.package.LIONSettingsPanel)  # 模拟旧模块残留
		plugin.terminate()
		self.assertNotIn(self.package.LIONSettingsPanel, self.panelClasses)


class WordingTests(unittest.TestCase):
	def _source(self, name):
		with open(os.path.join(LITE_SRC, "globalPlugins", "lionlite", name), encoding="utf-8") as handle:
			return handle.read()

	def testPanelTitleAndShortLabels(self):
		source = self._source("lionGui.py")
		self.assertIn('title = _("LION modified lite")', source)
		for short in (
			'&Model / endpoint:',
			'&Test / get models',
			'Service &token:',
			'Crop &above (%):',
			'Detection longest side (0 = default):',
			'Return &visualisation image (slow)',
			'Announce &errors once',
		):
			self.assertIn(short, source, short)

	def testNoVerboseLabels(self):
		source = self._source("lionGui.py")
		for verbose in (
			"(leave empty if the service requires no authentication)",
			"Ask the service to return a",
			"when the OCR service is unavailable",
			"Text detection longest side (0 = service default)",
		):
			self.assertNotIn(verbose, source, verbose)

	def testSingleAreaBehaviourKept(self):
		"""lite 版保持 3.3.1 的单区域行为：没有区域列表、排队朗读、分区去重。"""
		source = self._source("__init__.py")
		self.assertNotIn("regions", source)
		self.assertNotIn("queueSpeech", source)
		self.assertIn("prevString", source)
		self.assertIn("getDynamicTargetRect", source)


class TranslationTests(LiteHarness):
	def testMoTranslatesKeyStrings(self):
		moPath = os.path.join(self.src, "locale", "zh_CN", "LC_MESSAGES", "nvda.mo")
		with open(moPath, "rb") as handle:
			catalog = gettext.GNUTranslations(handle)
		self.assertEqual(catalog.gettext("LION modified lite"), "LION 修改版 lite")
		self.assertEqual(catalog.gettext("&Test / get models"), "测试 / 获取模型(&T)")

	def testEverySourceStringHasEntry(self):
		poPath = os.path.join(self.src, "locale", "zh_CN", "LC_MESSAGES", "nvda.po")
		with open(poPath, encoding="utf-8") as handle:
			po = handle.read()
		for name in ("__init__.py", "lionGui.py"):
			with open(os.path.join(self.src, "globalPlugins", "lionlite", name),
					  encoding="utf-8") as handle:
				source = handle.read()
			for literal in re.findall(r'(?<![.\w])_\("([^"]+)"\)', source):
				with self.subTest(name=name, literal=literal):
					self.assertIn('msgid "%s"' % literal, po, "缺少译文条目")


class DocTests(unittest.TestCase):
	"""lite 手册：版本引用、配置档案小节、文末 dsh 徽章、旧 AI 声明已移除。"""

	def _text(self, relativePath):
		with open(os.path.join(LITE_SRC, relativePath), encoding="utf-8") as handle:
			return handle.read()

	def testBadgeAtEndOfReadmes(self):
		"""文末徽章用本地 PNG 副本，且链接指向官方项目 URL。"""
		for lang in ("zh_CN", "en"):
			with self.subTest(lang=lang):
				text = self._text(os.path.join("doc", lang, "readme.md")).rstrip()
				self.assertTrue(text.endswith(DSH_BADGE_LOCAL), "dsh 徽章必须在手册文末")
				self.assertIn("../dsh-badge.png", text, "手册徽章必须引用本地 PNG 副本")
				self.assertIn(DSH_PROJECT_URL, text, "徽章链接必须指向官方项目")
				self.assertEqual(text.count("dsh-badge.png"), 1)

	def testLocalBadgePngIsOfficialCopy(self):
		"""内置 PNG 必须是官方原图：哈希匹配、726×120，且与完整版副本逐字节一致。"""
		self.assertTrue(os.path.isfile(DSH_BADGE_PNG), "缺少 doc/dsh-badge.png")
		with open(DSH_BADGE_PNG, "rb") as handle:
			data = handle.read()
		self.assertEqual(hashlib.sha256(data).hexdigest(), DSH_BADGE_SHA256)
		width, height = struct.unpack(">II", data[16:24])  # PNG IHDR 宽高
		self.assertEqual((width, height), (726, 120))
		fullPng = os.path.join(ROOT, "src", "doc", "dsh-badge.png")
		if os.path.isfile(fullPng):
			with open(fullPng, "rb") as handle:
				self.assertEqual(data, handle.read(), "lite 与完整版徽章 PNG 必须逐字节一致")

	def testNoLegacyAiDisclosure(self):
		for lang in ("zh_CN", "en"):
			text = self._text(os.path.join("doc", lang, "readme.md"))
			for literal in LEGACY_DISCLOSURE_LITERALS:
				with self.subTest(lang=lang, literal=literal):
					self.assertNotIn(literal, text)

	def testVersionReferences(self):
		for lang in ("zh_CN", "en"):
			text = self._text(os.path.join("doc", lang, "readme.md"))
			with self.subTest(lang=lang):
				self.assertIn("3.3.8", text)
				self.assertNotIn("3.3.7", text, "手册里的版本引用应全部改为 3.3.8")

	def testProfileSectionPresent(self):
		self.assertIn("## 配置档案", self._text(os.path.join("doc", "zh_CN", "readme.md")))
		self.assertIn("## Configuration profiles", self._text(os.path.join("doc", "en", "readme.md")))


@unittest.skipUnless(os.path.isfile(LITE_BUNDLE), "lite 包尚未构建，先运行 build_addon.ps1 -Variant lite")
class LiteBundleTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		import zipfile

		cls.zipFile = zipfile.ZipFile(LITE_BUNDLE)
		cls.entries = set(cls.zipFile.namelist())
		cls.manifest = ConfigObj(
			cls.zipFile.read("manifest.ini").decode("utf-8").splitlines(),
			encoding="utf-8", list_values=False, interpolation=False,
		)

	@classmethod
	def tearDownClass(cls):
		cls.zipFile.close()

	def testIdentity(self):
		self.assertEqual(self.manifest["name"], LITE_NAME)
		self.assertEqual(self.manifest["version"], LITE_VERSION)

	def testRequiredEntries(self):
		for entry in (
			"manifest.ini",
			"installTasks.py",
			"globalPlugins/lionlite/__init__.py",
			"globalPlugins/lionlite/lionGui.py",
			"globalPlugins/lionlite/ocrClient.py",
			"doc/zh_CN/readme.html",
			"doc/en/readme.html",
			"doc/dsh-badge.png",
			"locale/zh_CN/LC_MESSAGES/nvda.mo",
		):
			self.assertIn(entry, self.entries, entry)

	def testBundledClientMatchesFull(self):
		with open(FULL_CLIENT, "rb") as handle:
			self.assertEqual(self.zipFile.read("globalPlugins/lionlite/ocrClient.py"), handle.read())

	def testNoFullVersionOnlyFiles(self):
		for forbidden in ("globalPlugins/lion/regions.py", "globalPlugins/lion/dedup.py",
						  "globalPlugins/lion/__init__.py"):
			self.assertNotIn(forbidden, self.entries)


if __name__ == "__main__":
	unittest.main(verbosity=2)
