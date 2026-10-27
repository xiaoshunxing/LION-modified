# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""`lion.regions` 的单元测试（纯逻辑，不需要 NVDA 环境）。

覆盖：JSON 往返、容错解析与归一化、四向裁剪几何、旧配置迁移、状态清理与列表说明文本。

运行::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nvda_stubs  # noqa: E402

nvda_stubs.install()
nvda_stubs.package("lion")

from lion import regions  # noqa: E402


def baseRect(left=0, top=0, width=1000, height=1000):
	return {"left": left, "top": top, "width": width, "height": height}


def resolveWholeScreen(screen=(1920, 1080)):
	def resolve(target):
		return {"left": 0, "top": 0, "width": screen[0], "height": screen[1]}

	return resolve


class SerializationTests(unittest.TestCase):
	def testRoundTripKeepsValues(self):
		original = [
			{"id": 1, "name": "字幕", "enabled": True, "target": 1, "cropUp": 70, "cropDown": 0, "cropLeft": 0, "cropRight": 0},
			{"id": 2, "name": "Chat", "enabled": False, "target": 3, "cropUp": 5, "cropDown": 5, "cropLeft": 10, "cropRight": 10},
		]
		loaded = regions.loadFromJson(regions.toJson(original))
		self.assertEqual(loaded, regions.normalizeRegions(original))

	def testJsonIsCompactAndReadable(self):
		text = regions.toJson([{"id": 1, "name": "字幕"}])
		self.assertIn("字幕", text)
		self.assertNotIn(", ", text)

	def testEmptyListMeansNoRegions(self):
		self.assertEqual(regions.loadFromJson("[]"), [])

	def testBlankTextFallsBackToDefaultRegion(self):
		self.assertEqual(regions.loadFromJson(""), [regions.normalizeRegion(regions.DEFAULT_REGION)])
		self.assertEqual(regions.loadFromJson("   "), [regions.normalizeRegion(regions.DEFAULT_REGION)])

	def testBrokenJsonFallsBackToDefaultRegion(self):
		for bad in ("{oops", "null", "42", "\"text\"", "[1,2,3]", "{\"a\":1}"):
			with self.subTest(bad=bad):
				loaded = regions.loadFromJson(bad)
				self.assertEqual(len(loaded), 1, bad)
				self.assertEqual(loaded[0]["target"], regions.WHOLE_SCREEN)

	def testDefaultRegionIsWholeScreenWithoutCrop(self):
		region = regions.normalizeRegion(regions.DEFAULT_REGION)
		self.assertEqual(region["target"], regions.WHOLE_SCREEN)
		self.assertTrue(region["enabled"])
		for key in regions.LEGACY_CROP_KEYS:
			self.assertEqual(region[key], 0)


class NormalizeTests(unittest.TestCase):
	def testCropValuesClamped(self):
		region = regions.normalizeRegion({"cropUp": 500, "cropDown": -20, "cropLeft": "30", "cropRight": "abc"})
		self.assertEqual(region["cropUp"], 100)
		self.assertEqual(region["cropDown"], 0)
		self.assertEqual(region["cropLeft"], 30)
		self.assertEqual(region["cropRight"], 0)

	def testOppositeCropsCannotExceed100(self):
		region = regions.normalizeRegion({"cropLeft": 80, "cropRight": 50})
		self.assertEqual(region["cropLeft"], 80)
		self.assertEqual(region["cropRight"], 20)
		region = regions.normalizeRegion({"cropUp": 90, "cropDown": 40})
		self.assertEqual(region["cropDown"], 10)

	def testTargetOutOfRangeFallsBack(self):
		self.assertEqual(regions.normalizeRegion({"target": 9})["target"], regions.WHOLE_SCREEN)
		self.assertEqual(regions.normalizeRegion({"target": -1})["target"], regions.WHOLE_SCREEN)
		self.assertEqual(regions.normalizeRegion({"target": "2"})["target"], regions.CURRENT_WINDOW)

	def testNameIsTrimmedAndTruncated(self):
		self.assertEqual(regions.normalizeRegion({"name": "  字幕  "})["name"], "字幕")
		long_name = "x" * (regions.NAME_LIMIT + 20)
		self.assertEqual(len(regions.normalizeRegion({"name": long_name})["name"]), regions.NAME_LIMIT)

	def testEnabledCoercion(self):
		self.assertTrue(regions.normalizeRegion({"enabled": 1})["enabled"])
		self.assertFalse(regions.normalizeRegion({"enabled": 0})["enabled"])
		self.assertFalse(regions.normalizeRegion({"enabled": None})["enabled"])

	def testIdsAreUniqueAfterNormalization(self):
		loaded = regions.normalizeRegions([{"id": 1}, {"id": 1}, {"id": 1}])
		self.assertEqual(len({region["id"] for region in loaded}), 3)

	def testNonDictEntriesBecomeDefaults(self):
		loaded = regions.normalizeRegions([None, "x", 7, {"name": "ok"}])
		self.assertEqual(len(loaded), 4)
		self.assertEqual(loaded[3]["name"], "ok")

	def testDefaultNameUsesIndex(self):
		self.assertEqual(regions.defaultName({"name": ""}, 0), "Area 1")
		self.assertEqual(regions.defaultName({"name": "   "}, 2), "Area 3")
		self.assertEqual(regions.defaultName({"name": "字幕"}, 0), "字幕")


class DedupFieldTests(unittest.TestCase):
	"""区域级去重策略字段（3.3.3 新增）。"""

	def testDefaultIsClassic(self):
		region = regions.normalizeRegion({"id": 1})
		self.assertEqual(region["dedup"], "classic", "默认必须是经典，老区域行为不变")
		self.assertEqual(region["dedupHoldMs"], regions.DEDUP_HOLD_DEFAULT)

	def testKnownModesPreserved(self):
		for mode in regions.DEDUP_MODES:
			with self.subTest(mode=mode):
				self.assertEqual(regions.normalizeRegion({"dedup": mode})["dedup"], mode)

	def testCaseInsensitive(self):
		self.assertEqual(regions.normalizeRegion({"dedup": " COMMIT "})["dedup"], "commit")

	def testUnknownFallsBackToClassic(self):
		for bad in ("nonsense", None, 42, ""):
			with self.subTest(bad=bad):
				self.assertEqual(regions.normalizeRegion({"dedup": bad})["dedup"], "classic")

	def testHoldClamped(self):
		self.assertEqual(regions.normalizeRegion({"dedupHoldMs": 2000})["dedupHoldMs"], 2000)
		self.assertEqual(regions.normalizeRegion({"dedupHoldMs": 1})["dedupHoldMs"], regions.DEDUP_HOLD_MIN)
		self.assertEqual(regions.normalizeRegion({"dedupHoldMs": 99999})["dedupHoldMs"], regions.DEDUP_HOLD_MAX)
		self.assertEqual(regions.normalizeRegion({"dedupHoldMs": "abc"})["dedupHoldMs"], regions.DEDUP_HOLD_DEFAULT)

	def testRoundTripKeepsDedupFields(self):
		original = [{"id": 1, "name": "对话", "dedup": "commit", "dedupHoldMs": 3000}]
		loaded = regions.loadFromJson(regions.toJson(original))
		self.assertEqual(loaded[0]["dedup"], "commit")
		self.assertEqual(loaded[0]["dedupHoldMs"], 3000)

	def testLegacyJsonWithoutFieldsGetsClassic(self):
		"""3.3.2 写出的区域 JSON 没有这两个字段，加载后必须等价于经典去重。"""
		legacy = '[{"id":1,"name":"字幕","enabled":true,"target":1,"cropUp":70,"cropDown":0,"cropLeft":0,"cropRight":0}]'
		region = regions.loadFromJson(legacy)[0]
		self.assertEqual(region["dedup"], "classic")
		self.assertEqual(region["dedupHoldMs"], regions.DEDUP_HOLD_DEFAULT)

	def testMigrationProducesClassicRegion(self):
		region = regions.migrateFromLegacy({"target": 2, "cropUp": 70})[0]
		self.assertEqual(region["dedup"], "classic")


class ClassicThresholdTests(unittest.TestCase):
	"""每区域经典相似度阈值（3.3.8 新增）。"""

	def testDefaultIsInherit(self):
		self.assertIsNone(regions.DEFAULT_REGION["classicThreshold"])
		self.assertIsNone(regions.normalizeRegion({"id": 1})["classicThreshold"])

	def testOverrideKeptAndClamped(self):
		self.assertEqual(regions.normalizeRegion({"classicThreshold": 0.85})["classicThreshold"], 0.85)
		self.assertEqual(regions.normalizeRegion({"classicThreshold": 1.5})["classicThreshold"], 1.0)
		self.assertEqual(regions.normalizeRegion({"classicThreshold": -0.2})["classicThreshold"], 0.0)
		self.assertEqual(regions.normalizeRegion({"classicThreshold": "0.4"})["classicThreshold"], 0.4)

	def testInvalidValuesInherit(self):
		for bad in (None, True, False, "abc", {}, []):
			with self.subTest(bad=bad):
				self.assertIsNone(regions.normalizeRegion({"classicThreshold": bad})["classicThreshold"])

	def testLegacyJsonWithoutFieldInherits(self):
		legacy = '[{"id":1,"name":"字幕","enabled":true,"target":1,"cropUp":70,"cropDown":0,"cropLeft":0,"cropRight":0}]'
		self.assertIsNone(regions.loadFromJson(legacy)[0]["classicThreshold"])

	def testResolveUsesOverrideOrGlobal(self):
		self.assertEqual(regions.resolveClassicThreshold({"classicThreshold": 0.9}, 0.3), 0.9)
		self.assertEqual(regions.resolveClassicThreshold({"classicThreshold": None}, 0.3), 0.3)
		self.assertEqual(regions.resolveClassicThreshold({}, 0.3), 0.3)
		# 全局值非法时退回 confspec 默认 0.5
		self.assertEqual(regions.resolveClassicThreshold({}, "bad"), 0.5)

	def testRoundTripKeepsThreshold(self):
		original = [{"id": 1, "name": "对话", "classicThreshold": 0.75}]
		loaded = regions.loadFromJson(regions.toJson(original))
		self.assertEqual(loaded[0]["classicThreshold"], 0.75)


class UnionRectTests(unittest.TestCase):
	"""`unionRect`：合并截图用的最小外接矩形。"""

	def testEmptyInputReturnsNone(self):
		self.assertIsNone(regions.unionRect([]))
		self.assertIsNone(regions.unionRect(None))

	def testSingleRectIsReturnedAsIs(self):
		rect = {"left": 100, "top": 200, "width": 300, "height": 400}
		self.assertEqual(regions.unionRect([rect]), rect)

	def testDisjointRectsProduceBoundingBox(self):
		union = regions.unionRect([
			{"left": 100, "top": 50, "width": 200, "height": 100},
			{"left": 600, "top": 400, "width": 300, "height": 200},
		])
		self.assertEqual(
			(union["left"], union["top"], union["width"], union["height"]),
			(100, 50, 800, 550),
		)

	def testOverlappingRectsProduceBoundingBox(self):
		union = regions.unionRect([
			{"left": 0, "top": 0, "width": 100, "height": 100},
			{"left": 50, "top": 50, "width": 100, "height": 100},
		])
		self.assertEqual((union["width"], union["height"]), (150, 150))

	def testInvalidRectsAreSkipped(self):
		union = regions.unionRect([
			{"left": 10, "top": 10, "width": 0, "height": 50},
			{"left": 20, "top": 20, "width": 30, "height": 40},
			{"width": 10},
			None,
		])
		self.assertEqual(union, {"left": 20, "top": 20, "width": 30, "height": 40})


class ClippedRectTests(unittest.TestCase):
	def testNoCropReturnsBase(self):
		rect = regions.clippedRect(baseRect(), regions.normalizeRegion({}), screen=(1920, 1080))
		self.assertEqual((rect["left"], rect["top"], rect["width"], rect["height"]), (0, 0, 1000, 1000))

	def testCropFromTopKeepsLowerBand(self):
		crop = regions.normalizeRegion({"cropUp": 70})
		rect = regions.clippedRect(baseRect(), crop, screen=(1920, 1080))
		self.assertEqual((rect["left"], rect["top"]), (0, 700))
		self.assertEqual((rect["width"], rect["height"]), (1000, 300))

	def testCropOnAllSides(self):
		crop = regions.normalizeRegion({"cropUp": 10, "cropDown": 20, "cropLeft": 10, "cropRight": 10})
		rect = regions.clippedRect(baseRect(), crop, screen=(1920, 1080))
		self.assertEqual((rect["left"], rect["top"]), (100, 100))
		self.assertEqual((rect["width"], rect["height"]), (800, 700))

	def testOffsetBaseIsRespected(self):
		crop = regions.normalizeRegion({"cropLeft": 50})
		rect = regions.clippedRect(baseRect(left=200, top=100, width=400, height=400), crop, screen=(1920, 1080))
		self.assertEqual((rect["left"], rect["top"]), (400, 100))
		self.assertEqual((rect["width"], rect["height"]), (200, 400))

	def testDegenerateRectIsRaisedToMinimumSide(self):
		crop = regions.normalizeRegion({"cropUp": 100, "cropDown": 100, "cropLeft": 100, "cropRight": 100})
		rect = regions.clippedRect(baseRect(), crop, screen=(1920, 1080))
		self.assertEqual(rect["width"], regions.MIN_SIDE)
		self.assertEqual(rect["height"], regions.MIN_SIDE)

	def testRectIsClampedToScreen(self):
		# 基底比屏幕还大时，宽度不能越过屏幕边界
		rect = regions.clippedRect(baseRect(width=3000, height=2000), regions.normalizeRegion({}), screen=(1920, 1080))
		self.assertLessEqual(rect["width"], 1920)
		self.assertLessEqual(rect["height"], 1080)


class RegionRectTests(unittest.TestCase):
	def testUsesInjectedResolver(self):
		seen = []

		def resolve(target):
			seen.append(target)
			return baseRect(left=100, top=200, width=800, height=600)

		region = regions.normalizeRegion({"target": regions.CURRENT_WINDOW, "cropUp": 50})
		rect = regions.regionRect(region, resolve, screen=(1920, 1080))
		self.assertEqual(seen, [regions.CURRENT_WINDOW])
		self.assertEqual((rect["left"], rect["top"]), (100, 500))
		self.assertEqual((rect["width"], rect["height"]), (800, 300))

	def testResolverFailureFallsBackToWholeScreen(self):
		def resolve(target):
			raise RuntimeError("boom")

		region = regions.normalizeRegion({"cropUp": 70})
		rect = regions.regionRect(region, resolve, screen=(1920, 1080))
		self.assertEqual((rect["left"], rect["top"]), (0, 756))
		self.assertEqual(rect["width"], 1920)

	def testNoneResolverFallsBackToWholeScreen(self):
		region = regions.normalizeRegion({})
		rect = regions.regionRect(region, lambda target: None, screen=(800, 600))
		self.assertEqual((rect["width"], rect["height"]), (800, 600))

	def testInvalidBaseFallsBackToWholeScreen(self):
		region = regions.normalizeRegion({})
		rect = regions.regionRect(region, lambda target: baseRect(width=0, height=0), screen=(800, 600))
		self.assertEqual((rect["width"], rect["height"]), (800, 600))

	def testEveryTargetIsResolvable(self):
		calls = []
		rect = regions.regionRect(
			regions.normalizeRegion({"target": regions.NAVIGATOR_OBJECT}),
			lambda target: calls.append(target) or baseRect(),
			screen=(1920, 1080),
		)
		self.assertEqual(calls, [regions.NAVIGATOR_OBJECT])
		self.assertEqual(rect["width"], 1000)


class MigrationTests(unittest.TestCase):
	def testLegacyKeysBecomeOneRegion(self):
		conf = {"target": 2, "cropUp": 70, "cropDown": 0, "cropLeft": 0, "cropRight": 10}
		loaded = regions.migrateFromLegacy(conf)
		self.assertEqual(len(loaded), 1)
		region = loaded[0]
		self.assertEqual(region["target"], regions.CURRENT_WINDOW)
		self.assertEqual(region["cropUp"], 70)
		self.assertEqual(region["cropRight"], 10)
		self.assertTrue(region["enabled"])
		self.assertEqual(region["name"], "Area 1")

	def testMissingLegacyKeysUseDefaults(self):
		loaded = regions.migrateFromLegacy({})
		self.assertEqual(len(loaded), 1)
		self.assertEqual(loaded[0]["target"], regions.WHOLE_SCREEN)

	def testPartialLegacyConfig(self):
		loaded = regions.migrateFromLegacy({"cropUp": 70})
		self.assertEqual(loaded[0]["cropUp"], 70)
		self.assertEqual(loaded[0]["cropDown"], 0)

	def testLegacyTargetIsClamped(self):
		loaded = regions.migrateFromLegacy({"target": 77})
		self.assertEqual(loaded[0]["target"], regions.WHOLE_SCREEN)

	def testConfObjectWithoutMembershipStillWorks(self):
		class Boom:
			def __contains__(self, key):
				raise TypeError("no membership")

			def get(self, key, default=None):
				return default

		self.assertEqual(len(regions.migrateFromLegacy(Boom())), 1)


class StateTests(unittest.TestCase):
	def testPruneKeepsOnlyExistingRegions(self):
		state = {1: "a", 2: "b", 3: "c"}
		kept = regions.pruneState(state, [{"id": 1}, {"id": 3}])
		self.assertEqual(kept, {1: "a", 3: "c"})

	def testPruneWithEmptyList(self):
		self.assertEqual(regions.pruneState({1: "a"}, []), {})


class DescribeTests(unittest.TestCase):
	def testDescribeIncludesTargetAndCrop(self):
		text = regions.describe({"name": "字幕", "target": regions.WHOLE_SCREEN, "cropUp": 70}, 0)
		self.assertIn("字幕", text)
		self.assertIn("Whole Screen", text)
		self.assertIn("70", text)

	def testDescribeFallsBackToIndexedName(self):
		self.assertIn("Area 2", regions.describe({"name": ""}, 1))

	def testDescribeToleratesBrokenRegion(self):
		self.assertIn("Area 1", regions.describe(None, 0))


if __name__ == "__main__":
	unittest.main(verbosity=2)
