# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""校验 `src/manifest.ini` 与 `src/globalPlugins/lion` 的配置规格。

用 NVDA 自己的字段规则（`addonHandler.AddonManifest.configspec` 的等价定义）与
`configobj.validate.Validator` 检查声明信息；用同一套机制检查插件 `confspec` 里
每个条目能否通过 NVDA 的配置校验（类型合法、默认值合法、越界值被拒）。

运行::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import configparser
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nvda_stubs  # noqa: E402

nvda_stubs.install()

# --------------------------------------------------------------- configobj 来源
# 不往 conda 环境里装包：用 nvda_stubs.loadConfigObj() 统一解析（优先环境里已安装的
# 版本，其次 tests/vendor 里的源码副本）。全部不可用时相关用例标记为 skip。

NVDA_LIBRARY_ZIP = os.environ.get(
	"LION_NVDA_LIBRARY_ZIP",
	r"C:\Program Files\NVDA\library.zip",
)
_configobj = nvda_stubs.loadConfigObj()
CONFIGOBJ_SOURCE = "vendored copy or installed package" if _configobj else "unavailable"
requiresConfigObj = unittest.skipIf(
	_configobj is None,
	"未找到可用的 configobj（tests/vendor/configobj 缺失，环境中也未安装）",
)
if _configobj is not None:
	ConfigObj, Validator, VdtTypeError, VdtValueError = _configobj

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, os.pardir, "src"))
PLUGIN = os.path.join(SRC, "globalPlugins", "lion")
MANIFEST = os.path.join(SRC, "manifest.ini")
VENDOR = os.path.join(HERE, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
	sys.path.append(VENDOR)


def makeSpec(text: str):
	"""构造 configspec。

必须 `list_values=False`：否则 configobj 会把规格里的逗号当成列表分隔符，
`float(min=0.0, max=10.0, default=1.0)` 会被解析成列表并触发 TypeError。
NVDA 自己的 `addonHandler.AddonManifest` 也是同样的写法。
	"""
	return ConfigObj(io.StringIO(text), list_values=False)

#: 取自 NVDA 2026.2 的 addonHandler.AddonManifest.configspec
MANIFEST_SPEC = """
name = string()
summary = string()
description = string(default=None)
author = string()
version = string()
changelog = string(default=None)
minimumNVDAVersion = apiVersion(default="0.0.0")
lastTestedNVDAVersion = apiVersion(default="0.0.0")
url = string(default=None)
docFileName = string(default=None)
"""


def defaultFromSpec(spec: str):
	"""从 configobj 规格字符串里取出 default= 的值（用于测默认值本身是否合法）。"""
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
	try:
		return float(raw)
	except ValueError:
		return raw


def apiVersionString(value):
	"""等价于 addonHandler.validate_apiVersionString：校验并归一化 apiVersion。

	注意：`validate(copy=True)` 之后字段值**已经被转成元组**，所以这里同时接受元组。
	"""
	if isinstance(value, tuple):
		return tuple(int(part) for part in value)
	if not value or value == "None":
		return (0, 0, 0)
	if not isinstance(value, str):
		raise VdtTypeError("expected a string like 2019.1.0")
	parts = value.strip().split(".")
	if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
		raise VdtValueError(f"{value!r} is not a valid apiVersion")
	numbers = [int(part) for part in parts]
	while len(numbers) < 3:
		numbers.append(0)
	if len(str(numbers[0])) != 4:
		raise VdtValueError(f"{value!r}: the first component must be a 4-digit year")
	return tuple(numbers)


def unquote(value):
	"""去掉 configobj 在 `list_values=False` 下没有剥掉的双引号。"""
	if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
		return value[1:-1]
	return value


@requiresConfigObj
class ManifestTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		with open(MANIFEST, "rb") as handle:
			cls.manifest = ConfigObj(
				handle,
				configspec=makeSpec(MANIFEST_SPEC),
				encoding="utf-8",
				default_encoding="utf-8",
			)
		cls.validator = Validator({"apiVersion": apiVersionString})
		cls.result = cls.manifest.validate(cls.validator, copy=True, preserve_errors=True)

	def testValidationPasses(self):
		self.assertIs(self.result, True, msg=f"manifest 校验失败：{self.result}")

	def testRequiredFieldsPresent(self):
		for key in ("name", "summary", "author", "version"):
			self.assertTrue(self.manifest.get(key), key)

	def testAddonId(self):
		# 必须与原版 LION 不同，且是不能与已有 ID 大小写冲突的写法
		self.assertEqual(self.manifest["name"], "LION-modified")
		self.assertNotEqual(self.manifest["name"].lower(), "lion")
		self.assertNotIn(" ", self.manifest["name"])

	def testSummaryAndVersion(self):
		self.assertEqual(unquote(self.manifest["summary"]), "LION 修改版")
		self.assertEqual(self.manifest["version"], "3.3.7")

	def testDescriptionKeepsOriginalFirstAndForkBelow(self):
		"""按用户要求：原版描述原文在上，修改版内容在下。"""
		description = unquote(self.manifest["description"])
		self.assertIn("does live OCR for the whole screen, refreshing at a specified interval.", description)
		self.assertIn("Toggle with NVDA+alt+n.", description)
		self.assertLess(
			description.index("does live OCR"),
			description.index("以下为修改版内容"),
			"原版描述必须排在修改版内容之前",
		)
		self.assertIn("LION 修改版", description)
		self.assertIn("多选区", description)
		self.assertIn("排队朗读", description)

	def testChangelogKeepsOriginalFirstAndForkBelow(self):
		changelog = unquote(self.manifest["changelog"])
		self.assertLess(
			changelog.index("原版 LION 3.3.0"),
			changelog.index("以下为修改版内容"),
			"原版变更日志必须排在修改版内容之前",
		)
		self.assertIn("使用正则过滤识别文本", changelog)
		self.assertIn("版本 3.3.7", changelog)
		self.assertIn("版本 3.3.2", changelog)
		self.assertIn("版本 3.3.1", changelog)
		self.assertIn("xiaoshunxing", changelog)

	def testAuthorship(self):
		author = unquote(self.manifest["author"])
		for original in ("Stefan Moisei", "Cary-rowen", "anmi2004", "hwf1324"):
			self.assertIn(original, author, f"必须保留原作者 {original}")
		self.assertIn("xiaoshunxing", author)

	def testApiVersionRange(self):
		minimum = apiVersionString(self.manifest["minimumNVDAVersion"])
		last = apiVersionString(self.manifest["lastTestedNVDAVersion"])
		self.assertLessEqual(minimum, last, "minimumNVDAVersion 不能大于 lastTestedNVDAVersion")
		self.assertEqual(minimum, (2026, 1, 0))
		self.assertEqual(last, (2026, 2, 0))

	def testDocFileExists(self):
		doc = unquote(self.manifest["docFileName"])
		self.assertTrue(doc)
		for lang in ("zh_CN", "en"):
			path = os.path.join(SRC, "doc", lang, doc)
			self.assertTrue(os.path.isfile(path), path)

	def testLocalisedManifestExists(self):
		path = os.path.join(SRC, "locale", "zh_CN", "manifest.ini")
		self.assertTrue(os.path.isfile(path))
		with open(path, "rb") as handle:
			translated = ConfigObj(handle, encoding="utf-8", default_encoding="utf-8", list_values=False)
		self.assertEqual(unquote(translated["summary"]), "LION 修改版")


@requiresConfigObj
class ConfspecTests(unittest.TestCase):
	"""用 configobj 校验 `confspec` 里每个条目的合法性。"""

	@classmethod
	def setUpClass(cls):
		source = open(os.path.join(PLUGIN, "__init__.py"), encoding="utf-8").read()
		tree = ast.parse(source)
		spec_text = None
		for node in ast.walk(tree):
			if isinstance(node, ast.Assign) and any(
				getattr(target, "id", None) == "confspec" for target in node.targets
			):
				spec_text = ast.literal_eval(node.value)
		if spec_text is None:
			raise AssertionError("在 __init__.py 里找不到 confspec")
		cls.spec = spec_text
		cls.validator = Validator()

	def testSpecParses(self):
		self.assertIn("serviceUrl", self.spec)
		self.assertIn("model", self.spec)
		self.assertIn("interval", self.spec)
		self.assertIn("regions", self.spec)
		self.assertIn("queueSpeech", self.spec)
		self.assertEqual(len(self.spec), 14)

	def testLegacySingleRegionKeysAreGone(self):
		"""单区域时代的裁剪/目标键已被区域列表取代。"""
		for key in ("target", "cropUp", "cropDown", "cropLeft", "cropRight"):
			with self.subTest(key=key):
				self.assertNotIn(key, self.spec)

	def testEveryEntryAcceptsItsDefault(self):
		"""把整份 confspec 交给 configobj，校验所有默认值都能通过类型与范围检查。"""
		specobj = makeSpec("")
		specobj.configspec = makeSpec("\n".join(f"{key} = {value}" for key, value in self.spec.items()) + "\n")
		result = specobj.validate(self.validator, copy=True, preserve_errors=True)
		self.assertIs(result, True, msg=f"默认值校验失败：{result}")

	def _specFromDefaults(self, defaults):
		"""用当前默认值构造一份可校验的配置，方便替换单个字段做边界测试。"""

		def render(value):
			# 字符串按原样写入（否则会被当成带引号的字面量），其余用 repr
			return value if isinstance(value, str) else repr(value)

		specobj = makeSpec("\n".join(f"{k} = {render(v)}" for k, v in defaults.items()) + "\n")
		specobj.configspec = makeSpec("\n".join(f"{k} = {v}" for k, v in self.spec.items()) + "\n")
		return specobj

	def testBoundsRejectOutOfRange(self):
		"""越界值必须被拒：逐个字段替换成范围外的值，其余字段保持默认。"""
		cases = [
			("interval", 10.5),
			("interval", -0.5),
			("threshold", 1.5),
			("threshold", -0.1),
			("scoreThresh", 1.5),
			("imageFormat", 2),
			("imageFormat", -1),
			("requestTimeout", 0.5),
			("requestTimeout", 1000),
			("detLimitSideLen", 9000),
			("detLimitSideLen", -5),
		]
		for key, bad in cases:
			with self.subTest(key=key, value=bad):
				defaults = self.defaults()
				defaults[key] = bad
				try:
					result = self._specFromDefaults(defaults).validate(self.validator, copy=True, preserve_errors=True)
				except Exception as error:  # noqa: BLE001 - 越界值可能直接抛异常
					continue
				self.assertIsNot(result, True, msg=f"{key}={bad} 不应通过校验")

	def testInvalidTypeRejected(self):
		for key, bad in (
			("visualize", "maybe"),
			("interval", "abc"),
			("imageFormat", "1.5"),
			("queueSpeech", "perhaps"),
		):
			with self.subTest(key=key, value=bad):
				defaults = self.defaults()
				defaults[key] = bad
				try:
					result = self._specFromDefaults(defaults).validate(self.validator, copy=True, preserve_errors=True)
				except Exception:  # noqa: BLE001
					continue
				self.assertIsNot(result, True, msg=f"{key}={bad} 不应通过校验")

	def testRegionsAcceptsJsonWithCommas(self):
		"""区域 JSON 里全是逗号，必须整串存下来而不是被 configobj 拆成列表。"""
		payload = '[{"id":1,"name":"字幕","enabled":true,"target":1,"cropUp":70,"cropDown":0,"cropLeft":0,"cropRight":0}]'
		defaults = self.defaults()
		defaults["regions"] = payload
		specobj = self._specFromDefaults(defaults)
		result = specobj.validate(self.validator, copy=True, preserve_errors=True)
		self.assertIs(result, True, msg=f"{result}")
		self.assertIsInstance(specobj["regions"], str)
		self.assertEqual(unquote(specobj["regions"]), payload)

	def testDefaultRegionsJsonIsAParseableEmptyList(self):
		import json as jsonModule

		specobj = makeSpec("")
		specobj.configspec = makeSpec(f"regions = {self.spec['regions']}\n")
		specobj.validate(self.validator, copy=True)
		self.assertEqual(jsonModule.loads(specobj["regions"]), [])

	def testQueueSpeechAcceptsBooleans(self):
		for raw, expected in ((True, True), (False, False), ("1", True), ("0", False)):
			with self.subTest(raw=raw):
				defaults = self.defaults()
				defaults["queueSpeech"] = raw
				specobj = self._specFromDefaults(defaults)
				result = specobj.validate(self.validator, copy=True, preserve_errors=True)
				self.assertIs(result, True, msg=f"{result}")
				self.assertIs(specobj["queueSpeech"], expected)

	def testOriginalsPreserved(self):
		expected = {
			"threshold": 0.5,
			"interval": 1.0,
			"regexFilters": "",
		}
		defaults = self.defaults()
		for key, value in expected.items():
			with self.subTest(key=key):
				self.assertEqual(defaults[key], value)

	def testNewDefaults(self):
		defaults = self.defaults()
		# 3.3.6 起 serviceUrl 是服务基址，具体端点由 model 决定
		self.assertEqual(defaults["serviceUrl"], "http://127.0.0.1:23353")
		self.assertEqual(defaults["model"], "/ocr")
		self.assertEqual(defaults["serviceToken"], "")
		self.assertEqual(defaults["requestTimeout"], 15.0)
		self.assertEqual(defaults["imageFormat"], 1)
		self.assertEqual(defaults["visualize"], False)
		self.assertEqual(defaults["scoreThresh"], 0.0)
		self.assertEqual(defaults["detLimitSideLen"], 0)
		self.assertEqual(defaults["notifyOnError"], True)

	def defaults(self) -> dict:
		result = {}
		specobj = makeSpec("")
		specobj.configspec = makeSpec("\n".join(f"{k} = {v}" for k, v in self.spec.items()) + "\n")
		specobj.validate(self.validator, copy=True)
		for key in self.spec:
			result[key] = specobj[key]
		return result


class SourceHygieneTests(unittest.TestCase):
	"""源码层面的卫生检查：语法、禁止残留、编码。"""

	def _pluginFiles(self):
		paths = []
		for root, _dirs, files in os.walk(SRC):
			for name in files:
				if name.endswith(".py"):
					paths.append(os.path.join(root, name))
		return paths

	def testAllPythonFilesParse(self):
		files = self._pluginFiles()
		self.assertGreaterEqual(len(files), 3)
		for path in files:
			with self.subTest(path=os.path.relpath(path, SRC)):
				with open(path, encoding="utf-8") as handle:
					ast.parse(handle.read(), filename=path)

	def testNoBundledEngineOrPil(self):
		for forbidden in ("PaddleOCR", "PIL", "__pycache__", "PaddleOCR-json.exe"):
			with self.subTest(forbidden=forbidden):
				self.assertFalse(
					os.path.exists(os.path.join(SRC, forbidden)),
					f"{forbidden} 不应出现在插件源里",
				)

	def testNoPyCacheOrPyc(self):
		for root, dirs, files in os.walk(SRC):
			self.assertNotIn("__pycache__", dirs, root)
			for name in files:
				self.assertFalse(name.endswith((".pyc", ".pyo", ".log")), os.path.join(root, name))

	def testPluginDoesNotSpawnLocalEngine(self):
		source = open(os.path.join(PLUGIN, "__init__.py"), encoding="utf-8").read()
		self.assertNotIn("PaddleOCR-json", source)
		self.assertNotIn("GetOcrApi", source)
		self.assertNotIn("subprocess", source)
		self.assertIn("HttpOcrClient", source)

	def testConfigSectionIsIndependent(self):
		source = open(os.path.join(PLUGIN, "__init__.py"), encoding="utf-8").read()
		self.assertIn('CONFIG_SECTION = "LION-modified"', source)
		self.assertNotIn('config.conf.spec["lion"]', source)

	def testNoTopLevelTranslationInjection(self):
		"""`_` 必须来自 addonHandler.initTranslation，不能手工塞进 builtins。

		`dedup.py` 是刻意的例外：纯逻辑模块，不导入任何 NVDA 模块、也没有面向用户的
		字符串，这样才能在普通 Python 下完整测试。
		"""
		for path in self._pluginFiles():
			name = os.path.basename(path)
			with open(path, encoding="utf-8") as handle:
				source = handle.read()
			with self.subTest(path=name):
				self.assertNotIn("builtins", source)
				if name == "dedup.py":
					self.assertNotIn("addonHandler", source)
				else:
					self.assertIn("addonHandler.initTranslation()", source)

	def testDedupModuleHasNoNvdaImports(self):
		"""去重引擎必须能在没有 NVDA 的环境下 import。"""
		with open(os.path.join(PLUGIN, "dedup.py"), encoding="utf-8") as handle:
			tree = ast.parse(handle.read())
		imported = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				imported.update(alias.name.split(".")[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.module:
				imported.add(node.module.split(".")[0])
		for forbidden in ("addonHandler", "config", "gui", "wx", "api", "ui", "speech"):
			self.assertNotIn(forbidden, imported)

	def testUserFacingStringsAreTranslatable(self):
		"""新增的英文界面字符串应全部包在 _() 里，便于本地化。"""
		source = open(os.path.join(PLUGIN, "lionGui.py"), encoding="utf-8").read()
		self.assertIn('title = _("LION modified")', source)
		self.assertIn('_("OCR &service URL:")', source)
		self.assertIn('_("&Model / endpoint:")', source)
		self.assertIn('_("&Test / get models")', source)
		self.assertIn('_("&Queue speech")', source)
		self.assertIn('_("OCR regions:")', source)

	def testRegionModuleIsPresentationFree(self):
		"""区域模块不应依赖 wx / gui / api，才能在普通 Python 下完整测试。"""
		source = open(os.path.join(PLUGIN, "regions.py"), encoding="utf-8").read()
		for forbidden in ("import wx", "import gui", "import api", "import config", "import ui"):
			with self.subTest(forbidden=forbidden):
				self.assertNotIn(forbidden, source)

	def testPoContainsNewStrings(self):
		po = open(os.path.join(SRC, "locale", "zh_CN", "LC_MESSAGES", "nvda.po"), encoding="utf-8").read()
		for text in (
			"OCR &service URL:",
			"&Model / endpoint:",
			"&Test / get models",
			"Found %d models.",
			"Source: OpenAPI endpoints",
			"Connected; model list unavailable: %s",
			"Recognition test skipped (PaddleOCR-VL is slow).",
			"Unauthorized: check the service token.",
			"Token accepted (HTTP %s).",
			"Endpoint not found: %s",
			"LION modified",
			"Invalid service address.",
			"PNG (larger, lossless)",
			"OCR region",
			"&Queue speech",
			"OCR regions:",
			"Area %d",
			"Select a region first.",
			"Invalid crop values",
		):
			with self.subTest(text=text):
				self.assertIn(text, po, "新字符串未进入 .po")

	def testPoKeepsLegacyTargetNames(self):
		"""区域列表会翻译目标名，旧的四个目标译文必须仍在 .po 里。"""
		po = open(os.path.join(SRC, "locale", "zh_CN", "LC_MESSAGES", "nvda.po"), encoding="utf-8").read()
		for text in ("Navigator object", "Whole Screen", "current window", "current control"):
			with self.subTest(text=text):
				self.assertIn(text, po)

	def testPoIsNotFuzzyForNewEntries(self):
		po = open(os.path.join(SRC, "locale", "zh_CN", "LC_MESSAGES", "nvda.po"), encoding="utf-8").read()
		for block in re.split(r"\n\s*\n", po):
			if "OCR &service URL:" in block:
				self.assertNotIn(", fuzzy", block.splitlines()[0] if block.splitlines() else "")


if __name__ == "__main__":
	unittest.main(verbosity=2)
