# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""清亮版（LION 修改版 lite）的离线测试。

lite 版不把 `ocrClient.py` 存进自己的源码树：打包时从完整版复制（单一真源）。
所以这里先搭一个临时 staging（lite 源码 + 完整版客户端），再把它作为 `lionlite`
包加载，最后检查界面文案、配置、客户端一致性，以及产物包（若已构建）。
"""

from __future__ import annotations

import gettext
import os
import re
import shutil
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
LITE_VERSION = "3.3.7"
LITE_BUNDLE = os.path.join(DIST, "%s-%s.nvda-addon" % (LITE_NAME, LITE_VERSION))

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
			"globalPlugins/lionlite/__init__.py",
			"globalPlugins/lionlite/lionGui.py",
			"globalPlugins/lionlite/ocrClient.py",
			"doc/zh_CN/readme.html",
			"doc/en/readme.html",
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
