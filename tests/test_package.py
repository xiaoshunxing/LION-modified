# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""对 `dist/*.nvda-addon` 产物本身做结构性验证。

模拟 NVDA 安装插件时做的事：读 `manifest.ini`（含 `addonHandler` 的字段规格）、
找 `doc/<lang>/readme.html`、加载 `locale/<lang>/LC_MESSAGES/nvda.mo`、确认
`globalPlugins/` 下有入口模块；同时断言"不该存在的东西"确实不在包里。

产物不存在时跳过（先跑 scripts/build_addon.ps1）。
"""

from __future__ import annotations

import configparser
import gettext
import io
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nvda_stubs  # noqa: E402

nvda_stubs.install()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
DIST = os.path.join(ROOT, "dist")
SRC = os.path.join(ROOT, "src")

_configobj = nvda_stubs.loadConfigObj()
requiresConfigObj = unittest.skipIf(_configobj is None, "未找到可用的 configobj")
if _configobj is not None:
	ConfigObj, Validator, VdtTypeError, VdtValueError = _configobj

EXPECTED_NAME = "LION-modified"
EXPECTED_VERSION = "3.3.7"

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


def apiVersionString(value):
	if not value or value == "None":
		return (0, 0, 0)
	if not isinstance(value, str):
		raise VdtTypeError("expected a string")
	parts = value.strip().split(".")
	if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
		raise VdtValueError(f"{value!r} is not a valid apiVersion")
	numbers = [int(part) for part in parts]
	while len(numbers) < 3:
		numbers.append(0)
	if len(str(numbers[0])) != 4:
		raise VdtValueError(f"{value!r}: first component must be a 4-digit year")
	return tuple(numbers)


def findBundle() -> str | None:
	if not os.path.isdir(DIST):
		return None
	# 注意：不能只用 "LION-modified" in name，否则会把 lite 版也当成完整版
	expected = f"{EXPECTED_NAME}-{EXPECTED_VERSION}.nvda-addon"
	path = os.path.join(DIST, expected)
	return path if os.path.isfile(path) else None


BUNDLE = findBundle()


class _Extracted:
	"""把产物解压一次，供多个用例复用。"""

	_instances: dict[str, "_Extracted"] = {}

	def __init__(self, bundle: str):
		self.bundle = bundle
		self._temp = tempfile.TemporaryDirectory(prefix="lion-pkg-")
		with zipfile.ZipFile(bundle) as archive:
			archive.extractall(self._temp.name)
			self.entries = archive.namelist()
		self.root = self._temp.name

	def read(self, relative: str, binary: bool = False):
		path = os.path.join(self.root, relative.replace("/", os.sep))
		return open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"}))

	@classmethod
	def get(cls, bundle: str) -> "_Extracted":
		if bundle not in cls._instances:
			cls._instances[bundle] = cls(bundle)
		return cls._instances[bundle]


@unittest.skipIf(BUNDLE is None, "未找到 dist/*.nvda-addon，请先运行 scripts/build_addon.ps1")
class BundleStructureTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.extracted = _Extracted.get(BUNDLE)
		cls.entries = set(cls.extracted.entries)

	def testBundleNameFollowsNvdaConvention(self):
		expected = f"{EXPECTED_NAME}-{EXPECTED_VERSION}.nvda-addon"
		self.assertEqual(os.path.basename(BUNDLE), expected)

	def testNoForbiddenEntries(self):
		for entry in self.entries:
			with self.subTest(entry=entry):
				self.assertFalse(entry.startswith("PaddleOCR/"), "不得打包内置引擎")
				self.assertFalse(entry.startswith("PIL/"), "不得打包 PIL")
				self.assertNotIn("__pycache__", entry)
				self.assertFalse(entry.endswith((".pyc", ".pyo", ".log")))

	def testRequiredEntriesPresent(self):
		for required in (
			"manifest.ini",
			"globalPlugins/lion/__init__.py",
			"globalPlugins/lion/ocrClient.py",
			"globalPlugins/lion/lionGui.py",
			"globalPlugins/lion/regions.py",
			"doc/zh_CN/readme.md",
			"doc/zh_CN/readme.html",
			"doc/en/readme.md",
			"doc/en/readme.html",
			"doc/style.css",
			"locale/zh_CN/LC_MESSAGES/nvda.po",
			"locale/zh_CN/LC_MESSAGES/nvda.mo",
			"locale/zh_CN/manifest.ini",
		):
			with self.subTest(entry=required):
				self.assertIn(required, self.entries)

	def testReadmeKeepsOriginalContentAboveForkSection(self):
		"""用户要求：原版 readme 原文保留在上，修改版内容在下面。"""
		for lang, originalMarker, forkMarker in (
			("zh_CN", "# 这个插件可以做什么", "# 修改版改动（LION modified"),
			("en", "# What can this add-on do?", "# Changes in this fork"),
		):
			with self.subTest(lang=lang):
				with self.extracted.read(f"doc/{lang}/readme.md") as handle:
					text = handle.read()
				self.assertIn(originalMarker, text)
				self.assertIn(forkMarker, text)
				self.assertLess(
					text.index(originalMarker),
					text.index(forkMarker),
					"原版内容必须在修改版章节之前",
				)

	def testManifestKeepsOriginalBlockFirst(self):
		"""描述与变更日志各自都要"原版在上、修改版在下"（分别按字段检查）。"""
		with self.extracted.read("manifest.ini") as handle:
			manifest = ConfigObj(
				handle,
				configspec=ConfigObj(io.StringIO(MANIFEST_SPEC), list_values=False),
				encoding="utf-8",
				default_encoding="utf-8",
			)

		def strip(value):
			if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
				return value[1:-1]
			return value

		description = strip(manifest["description"])
		self.assertLess(
			description.index("does live OCR for the whole screen"),
			description.index("以下为修改版内容"),
			"描述里原版内容必须在前",
		)
		changelog = strip(manifest["changelog"])
		self.assertLess(
			changelog.index("原版 LION 3.3.0"),
			changelog.index("以下为修改版内容"),
			"变更日志里原版内容必须在前",
		)
		self.assertIn("使用正则过滤识别文本", changelog)
		self.assertIn("版本 3.3.7", changelog)
		self.assertIn("版本 3.3.2", changelog)

	def testNoOriginalAddonFiles(self):
		"""不得覆盖原版 LION：包内不能出现原版特有的内置引擎或原样 manifest。"""
		self.assertNotIn("globalPlugins/lion/PPOCR_api.py", self.entries)
		with self.extracted.read("manifest.ini") as handle:			self.assertNotIn("name = LION\n", handle.read())

	def testSizeBudget(self):
		size = os.path.getsize(BUNDLE)
		self.assertLess(size, 2 * 1024 * 1024, "产物应小于 2 MB")


@unittest.skipIf(BUNDLE is None, "未找到 dist/*.nvda-addon，请先运行 scripts/build_addon.ps1")
@requiresConfigObj
class BundleManifestTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.extracted = _Extracted.get(BUNDLE)
		with cls.extracted.read("manifest.ini") as handle:
			cls.manifest = ConfigObj(
				handle,
				configspec=ConfigObj(io.StringIO(MANIFEST_SPEC), list_values=False),
				encoding="utf-8",
				default_encoding="utf-8",
			)
		cls.result = cls.manifest.validate(Validator({"apiVersion": apiVersionString}), copy=True, preserve_errors=True)

	def testManifestValidates(self):
		self.assertIs(self.result, True, msg=f"{self.result}")

	def testIdentity(self):
		self.assertEqual(self.manifest["name"], EXPECTED_NAME)
		self.assertEqual(self.manifest["version"], EXPECTED_VERSION)
		self.assertEqual(self.manifest["summary"], "LION 修改版")

	def testAuthorshipPreserved(self):
		author = self.manifest["author"]
		for original in ("Stefan Moisei", "Cary-rowen", "anmi2004", "hwf1324"):
			self.assertIn(original, author)
		self.assertIn("xiaoshunxing", author)

	def testDocFileReferencedExistsInBundle(self):
		docName = self.manifest["docFileName"]
		self.assertTrue(docName)
		for lang in ("zh_CN", "en"):
			self.assertIn(f"doc/{lang}/{docName}", _Extracted.get(BUNDLE).entries)

	def testTranslationManifestMatchesId(self):
		with self.extracted.read("locale/zh_CN/manifest.ini") as handle:
			translated = ConfigObj(handle, encoding="utf-8", default_encoding="utf-8", list_values=False)
		summary = translated["summary"]
		if isinstance(summary, str) and len(summary) >= 2 and summary[0] == summary[-1] and summary[0] in "\"'":
			summary = summary[1:-1]
		self.assertEqual(summary, "LION 修改版")


@unittest.skipIf(BUNDLE is None, "未找到 dist/*.nvda-addon，请先运行 scripts/build_addon.ps1")
class BundleTranslationTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.extracted = _Extracted.get(BUNDLE)
		cls.moPath = os.path.join(cls.extracted.root, "locale", "zh_CN", "LC_MESSAGES", "nvda.mo")
		with open(cls.moPath, "rb") as handle:
			cls.catalog = gettext.GNUTranslations(handle)

	def testKeyStringsTranslated(self):
		expected = {
			"LION modified": "LION 修改版",
			"OCR &service URL:": "OCR 服务地址(&S)：",
			"&Test / get models": "测试 / 获取模型(&T)",
			"&Model / endpoint:": "模型 / 端点(&M)：",
			"Whole Screen": "全屏幕",
		}
		for source, translated in expected.items():
			with self.subTest(source=source):
				self.assertEqual(self.catalog.gettext(source), translated)

	def testEveryPluginStringHasAnEntry(self):
		"""源码里的可翻译字符串必须都在 .mo 里（英文条目回退为原文也算有）。"""
		import re

		missing = []
		for relative in ("globalPlugins/lion/__init__.py", "globalPlugins/lion/lionGui.py", "globalPlugins/lion/ocrClient.py"):
			with self.extracted.read(relative) as handle:
				source = handle.read()
			for literal in re.findall(r'(?<![.\w])_\("([^"]+)"\)', source):
				if self.catalog.gettext(literal) != literal:
					continue
				if literal not in self.catalog._catalog:
					missing.append(f"{relative}: {literal}")
		self.assertEqual(missing, [], "以下字符串没有进入 .mo")

	def testCompiledFromPoInBundle(self):
		with self.extracted.read("locale/zh_CN/LC_MESSAGES/nvda.po") as handle:
			po = handle.read()
		self.assertIn("LION-modified", po)
		self.assertIn('msgstr "测试 / 获取模型(&T)"', po)


@unittest.skipIf(BUNDLE is None, "未找到 dist/*.nvda-addon，请先运行 scripts/build_addon.ps1")
class DistHygieneTests(unittest.TestCase):
	"""产物目录的约定：只允许发布包和清单，不留下构建临时文件。"""

	def testDistHasOnlyAddonsAndListings(self):
		"""dist 里除了 .nvda-addon / .files.txt，不应出现临时文件。"""
		if not os.path.isdir(DIST):
			self.skipTest("dist 不存在")
		for name in os.listdir(DIST):
			if name == BUNDLE or name.endswith((".nvda-addon", ".files.txt")):
				continue
			with self.subTest(name=name):
				# 允许用户自己放进去的文件（截图、笔记等），只拦明显是构建残留的
				self.assertFalse(
					name.endswith((".tmp", ".bak", ".part")),
					f"dist 里不应有构建临时文件：{name}",
				)


@unittest.skipIf(BUNDLE is None, "未找到当前版本的产物包，请先运行 scripts/build_addon.ps1")
class BundleSourceConsistencyTests(unittest.TestCase):
	def testBundleMatchesSrcTree(self):
		"""产物内容必须与 src/ 完全一致（防止打包了旧代码）。"""
		extracted = _Extracted.get(BUNDLE)
		onDisk = set()
		for root, _dirs, files in os.walk(SRC):
			for name in files:
				relative = os.path.relpath(os.path.join(root, name), SRC).replace(os.sep, "/")
				onDisk.add(relative)
		self.assertEqual(set(extracted.entries), onDisk)


if __name__ == "__main__":
	unittest.main(verbosity=2)
