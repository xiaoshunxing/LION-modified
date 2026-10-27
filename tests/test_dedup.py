# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""`lion.dedup` 的单元测试（纯逻辑，离线、虚拟时间）。

重点是把"打字机效果"的真实时间线跑一遍：
RPG 对话每帧多出几个字，验证四种策略分别念什么、念几次。

运行::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nvda_stubs  # noqa: E402

nvda_stubs.install()
nvda_stubs.package("lion")

from lion import dedup  # noqa: E402

HOLD = 1500
FRAME = 1.0  # 模拟 interval=1000ms


class Timeline:
	"""按固定帧间隔喂文本，收集全部朗读事件。

	时间推进在**观察之前**（与插件一致：启动后先等满一个 interval 才做第一次扫描）。
	"""

	def __init__(self, engine, regionId=1, mode="commit", holdMs=HOLD, frame=FRAME, threshold=0.5):
		self.engine = engine
		self.regionId = regionId
		self.mode = mode
		self.holdMs = holdMs
		self.frame = frame
		self.threshold = threshold
		self.now = 0.0
		self.events = []
		self.frames = []

	def feed(self, text):
		self.now += self.frame
		self.frames.append((self.now, text))
		result = self.engine.observe(self.regionId, text, self.mode, self.holdMs, self.now, self.threshold)
		self.events.extend(result)
		return result

	def hold(self, text, count=None):
		"""重复喂同一段文本，直到"稳定"条件成立（含一点余量）。"""
		if count is None:
			count = int(self.holdMs / (self.frame * 1000.0)) + 2
		result = []
		for _ in range(max(1, count)):
			result.extend(self.feed(text))
		return result

	def feedMany(self, texts):
		for text in texts:
			self.feed(text)
		return self.events

	def texts(self):
		return [event.text for event in self.events]

	def advance(self, seconds):
		self.now += seconds


def typewriter(line, step=1):
	"""把一行文字拆成"每次多 step 个字"的帧序列。"""
	return [line[:index] for index in range(step, len(line) + 1, step)]


class ModeNormalizationTests(unittest.TestCase):
	def testKnownModesPreserved(self):
		for mode in dedup.MODES:
			self.assertEqual(dedup.clampMode(mode), mode)

	def testUnknownFallsBackToClassic(self):
		for bad in (None, "", "nonsense", 42):
			with self.subTest(bad=bad):
				self.assertEqual(dedup.clampMode(bad), "classic")

	def testCaseInsensitive(self):
		self.assertEqual(dedup.clampMode(" COMMIT "), "commit")
		self.assertEqual(dedup.clampMode("Incremental"), "incremental")

	def testHoldClamping(self):
		self.assertEqual(dedup.clampHold(1500), 1500)
		self.assertEqual(dedup.clampHold("2000"), 2000)
		self.assertEqual(dedup.clampHold(1), dedup.HOLD_MIN_MS)
		self.assertEqual(dedup.clampHold(99999), dedup.HOLD_MAX_MS)
		self.assertEqual(dedup.clampHold("abc"), dedup.DEFAULT_HOLD_MS)
		self.assertEqual(dedup.clampHold(None), dedup.DEFAULT_HOLD_MS)


class SimilarityTests(unittest.TestCase):
	def testIdentical(self):
		self.assertEqual(dedup.similarityRatio("同样的字", "同样的字"), 1.0)

	def testEmptyPrevious(self):
		self.assertEqual(dedup.similarityRatio("", "任何内容"), 0.0)

	def testDifferentText(self):
		self.assertLess(dedup.similarityRatio("甲区域上一轮的旧文本内容", "乙区域今天的第一句话"), 0.5)

	def testLongCjkTextStillComparable(self):
		"""autojunk 关闭后，长 CJK 文本的相似度必须仍然算得出来。"""
		line = "这是一个比较长的字幕句子用来验证相似度计算" * 8
		self.assertEqual(dedup.similarityRatio(line, line), 1.0)
		self.assertLess(dedup.similarityRatio(line, "完全不同的另一段文字" * 8), 0.5)

	def testLongRepetitiveTextSimilarityIsNotUnderestimated(self):
		"""≥200 字的高重复 CJK 文本：位置偏移时相似度不能被大幅低估。

		difflib 的 autojunk 会把常用字当噪声；实测 250 字重复字幕只偏移 30 字，
		相似度从 0.94 掉到 0.11。classic 策略是"相似度低于阈值才朗读"，所以低估会让
		同一句被每帧反复朗读（不是"永远不念"）。这里同时锁定正确值与原始行为。
		"""
		line = "这是一个比较长的字幕句子用来验证相似度计算反复出现" * 10  # 250 字
		self.assertEqual(dedup.similarityRatio(line, line), 1.0)
		shifted = line[:30] + line
		self.assertGreaterEqual(dedup.similarityRatio(line, shifted), 0.8)
		# 对照：默认 autojunk=True 会把同一句的相似度压到阈值以下（旧 bug 的来源）
		self.assertLess(SequenceMatcher(None, line, shifted).ratio(), 0.5)

	def testPrefixGrowthIsSimilar(self):
		"""打字机增长之所以会被 classic 判为"没变"，正是因为它相似度越来越高。"""
		line = "你好冒险者欢迎来到新手村"
		ratios = [dedup.similarityRatio(line[:index], line[:index + 1]) for index in range(1, 6)]
		self.assertTrue(all(ratio >= 0.5 for ratio in ratios), ratios)


class BoundaryTests(unittest.TestCase):
	def testNoBoundary(self):
		self.assertEqual(dedup.findBoundary("你好世界", 0), -1)

	def testCjkComma(self):
		self.assertEqual(dedup.findBoundary("你好，世界", 0), 3)

	def testLastBoundaryWins(self):
		self.assertEqual(dedup.findBoundary("你好，世界。之后", 0), 6)

	def testStartSkipsConsumedPart(self):
		# 前 6 个字符已念过，剩下 "之后" 没有边界
		self.assertEqual(dedup.findBoundary("你好，世界。之后", 6), -1)

	def testTrailingBlankIsNotBoundary(self):
		"""空白不是边界：否则英文会切出半个词。"""
		self.assertEqual(dedup.findBoundary("Hello adv", 0), -1)
		self.assertEqual(dedup.findBoundary("Hello adv ", 0), -1)
		self.assertEqual(dedup.findBoundary("Hello, adv", 0), 6)

	def testAsciiPunctuation(self):
		# "Hi," 里逗号在前面，返回逗号之后的位置
		self.assertEqual(dedup.findBoundary("Hi, there", 0), 3)

	def testStartOutOfRange(self):
		self.assertEqual(dedup.findBoundary("abc", 5), -1)
		self.assertEqual(dedup.findBoundary("abc,", -3), 4)


class ClassicModeTests(unittest.TestCase):
	"""classic 必须与 3.3.2 完全一致。"""

	def setUp(self):
		self.engine = dedup.DedupEngine()

	def testNewTextIsSpoken(self):
		timeline = Timeline(self.engine, mode="classic")
		timeline.feed("第一句")
		self.assertEqual(timeline.texts(), ["第一句"])

	def testIdenticalTextIsSuppressed(self):
		timeline = Timeline(self.engine, mode="classic")
		timeline.feedMany(["同样的字幕", "同样的字幕", "同样的字幕"])
		self.assertEqual(timeline.texts(), ["同样的字幕"])

	def testTypewriterOnlySpeaksTwoFragments(self):
		"""这就是要解决的问题：没有专门策略时只会念出几段碎片，而不是整句。"""
		line = "你好冒险者欢迎来到新手村"
		timeline = Timeline(self.engine, mode="classic")
		timeline.feedMany(typewriter(line))
		texts = timeline.texts()
		self.assertLess(len(texts), 3, texts)
		self.assertNotIn(line, texts, "经典策略在打字机下念不出整句")

	def testThresholdOneAlwaysSpeaks(self):
		timeline = Timeline(self.engine, mode="classic", threshold=1.0)
		timeline.feedMany(["第一句话", "完全不同的一句话"])
		self.assertEqual(len(timeline.texts()), 2)

	def testThresholdZeroNeverSpeaksTwice(self):
		"""阈值 0：任何文本都被视为"没变过"，与 3.3.2 语义一致。"""
		timeline = Timeline(self.engine, mode="classic", threshold=0.0)
		timeline.feedMany(["第一句话", "完全不同的一句话", "又一个新句子"])
		self.assertEqual(timeline.texts(), [])

	def testEventReason(self):
		timeline = Timeline(self.engine, mode="classic")
		events = timeline.feed("一句话")
		self.assertEqual(events[0].reason, "classic")


class CommitModeTests(unittest.TestCase):
	"""游戏打字机的核心策略：稳定后整句念一次。"""

	def setUp(self):
		self.engine = dedup.DedupEngine()

	def testTypewriterSpeaksWholeLineOnce(self):
		"""3 秒打完的整句：只在打完时念一次完整句子。"""
		line = "你好冒险者"
		timeline = Timeline(self.engine, mode="commit")
		timeline.feedMany(typewriter(line))
		self.assertEqual(timeline.texts(), [], "打字过程中不应朗读")
		timeline.hold(line)  # 打完后再稳定到阈值
		self.assertEqual(timeline.texts(), [line])
		self.assertEqual(timeline.events[-1].reason, "commit")

	def testVerySlowTypewriterSpeaksCompleteLine(self):
		"""一秒一个字、超过 10 秒的长句：只在打完后念一次完整句子。

		（3.3.4 修正：旧实现会在第 10 秒念出没打完的半句。）
		"""
		line = "你好冒险者欢迎来到新手村"
		timeline = Timeline(self.engine, mode="commit")
		timeline.feedMany(typewriter(line))
		self.assertEqual(timeline.texts(), [], "打字过程中不应朗读")
		timeline.hold(line)
		self.assertEqual(timeline.texts(), [line])

	def testNothingSpokenWhileGrowing(self):
		timeline = Timeline(self.engine, mode="commit")
		timeline.feedMany(["你", "你好", "你好冒", "你好冒险", "你好冒险者"])
		self.assertEqual(timeline.texts(), [])

	def testStableTextIsNotRepeated(self):
		timeline = Timeline(self.engine, mode="commit")
		timeline.hold("一整句对话")
		self.assertEqual(len(timeline.texts()), 1)
		for _ in range(5):
			timeline.feed("一整句对话")
		self.assertEqual(len(timeline.texts()), 1)

	def testClearedBoxAllowsSameLineAgain(self):
		"""游戏里循环对话：对话框真的清空（连续空帧超过宽限）后再出现同一句，要能再念。"""
		timeline = Timeline(self.engine, mode="commit")
		timeline.hold("欢迎光临")
		timeline.feedMany([""] * 4)  # 真的清空了（超过 2 秒宽限）
		timeline.hold("欢迎光临")
		self.assertEqual(timeline.texts(), ["欢迎光临", "欢迎光临"])

	def testLineByLineAppendOnlySpeaksNewPart(self):
		"""逐行追加：第 1 行打完、第 2 行出现，只念第 2 行。"""
		timeline = Timeline(self.engine, mode="commit")
		timeline.hold("第一行对话")
		timeline.hold("第一行对话 第二行对话")
		self.assertEqual(timeline.texts(), ["第一行对话", "第二行对话"])

	def testReplacementSpeaksWholeNewText(self):
		timeline = Timeline(self.engine, mode="commit")
		timeline.hold("旧的一段话")
		timeline.hold("完全不同的新对话")
		self.assertEqual(timeline.texts(), ["旧的一段话", "完全不同的新对话"])

	def testTruncatedTailIsSilent(self):
		"""已念过的句子尾部被 OCR 裁掉：没有新内容，不应重念。

		（3.3.4 修正：旧行为会把截断后的残句再念一遍，正是用户听到的重复之一。）
		"""
		timeline = Timeline(self.engine, mode="commit")
		timeline.hold("你好冒险者欢迎")
		timeline.hold("你好冒")
		self.assertEqual(timeline.texts(), ["你好冒险者欢迎"])

	def testStabilityWindowIsRespected(self):
		line = "一句话"
		timeline = Timeline(dedup.DedupEngine(), mode="commit", holdMs=3000)
		timeline.feed(line)  # t=1.0（刚出现）
		timeline.feed(line)  # t=2.0（>=1 秒但 <3 秒）
		self.assertEqual(timeline.texts(), [], "未达到 3000ms 不应朗读")
		timeline.feed(line)  # t=3.0
		timeline.feed(line)  # t=4.0（>=3 秒）
		self.assertEqual(timeline.texts(), [line])

	def testGrowthNeverTriggersForcedRelease(self):
		"""正常长句逐字打字：内容一直在增长，10 秒保护**不应**打断它。

		（3.3.4 修正：旧实现会在第 10 秒把一个没打完的句子念出来。）
		"""
		line = "你好冒险者欢迎来到新手村这是很长的一句台词"
		timeline = Timeline(dedup.DedupEngine(), mode="commit", holdMs=1500)
		for index in range(1, len(line) + 1):
			timeline.feed(line[:index])
		self.assertEqual(timeline.texts(), [], "打字过程中不应播报")
		timeline.hold(line)
		self.assertEqual(timeline.texts(), [line])
		self.assertEqual(timeline.events[-1].reason, "commit")

	def testForcedReleaseGuardDoesNotFireWhenTextKeepsGrowing(self):
		"""兜底保护（MAX_PENDING_SECONDS）不得打断正常增长的文字。"""
		timeline = Timeline(dedup.DedupEngine(), mode="commit", holdMs=8000, frame=1.0)
		for index in range(1, 20):
			timeline.feed("ABCDEFGHIJKLMN"[: index])
		self.assertEqual(timeline.texts(), [], "8 秒 hold 未到，不应有兜底播报")
		self.assertFalse([e for e in timeline.events if e.reason == "forced"])

	def testWhitespaceOnlyTextIsSilent(self):
		timeline = Timeline(self.engine, mode="commit")
		timeline.feedMany(["   ", "  ", " "])
		self.assertEqual(timeline.texts(), [])


class IncrementalModeTests(unittest.TestCase):
	"""长对话跟读：到标点就念一段。"""

	def setUp(self):
		self.engine = dedup.DedupEngine()

	def testReleasesAtCommaThenRest(self):
		timeline = Timeline(self.engine, mode="incremental", frame=0.25)
		timeline.feed("你好，")
		self.assertEqual(timeline.texts(), ["你好，"])
		for index in range(5, len("你好，冒险者。") + 1):
			timeline.feed("你好，冒险者。"[:index])
		self.assertEqual(timeline.texts(), ["你好，", "冒险者。"])
		self.assertEqual({event.reason for event in timeline.events}, {"incremental"})

	def testNothingReleasedWithoutBoundary(self):
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feedMany(["你好", "你好冒", "你好冒险"])
		self.assertEqual(timeline.texts(), [])

	def testLongUnpunctuatedLineFallsBackToStability(self):
		line = "这一句完全没有标点符号所以只能等它稳定"
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feed(line)
		self.assertEqual(timeline.texts(), [])
		timeline.hold(line)
		self.assertEqual(timeline.texts(), [line])

	def testDoesNotSplitEnglishWords(self):
		"""没有标点时一个词都不释放，等稳定后整句念。"""
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feedMany(["Hello a", "Hello adv", "Hello adve", "Hello adve "])
		self.assertEqual(timeline.texts(), [])

	def testEnglishPunctuationTriggersRelease(self):
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feedMany(["Hi", "Hi,", "Hi, there"])
		self.assertEqual(timeline.texts(), ["Hi,"])

	def testReplacementResetsWatermark(self):
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feed("第一句，")
		self.assertEqual(timeline.texts(), ["第一句，"])
		timeline.feed("完全不同的新对话。")
		self.assertEqual(timeline.texts(), ["第一句，", "完全不同的新对话。"])

	def testSentenceBySentenceForLongDialogue(self):
		line = "第一句。第二句！第三句？"
		timeline = Timeline(self.engine, mode="incremental", frame=0.25)
		for index in range(1, len(line) + 1):
			timeline.feed(line[:index])
		self.assertEqual(timeline.texts(), ["第一句。", "第二句！", "第三句？"])

	def testAlreadyConsumedTextIsNotRepeated(self):
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feed("你好，")
		self.assertEqual(timeline.texts(), ["你好，"])
		for _ in range(3):
			timeline.feed("你好，")
		self.assertEqual(timeline.texts(), ["你好，"])


class OffModeTests(unittest.TestCase):
	def testSpeaksEveryChange(self):
		timeline = Timeline(dedup.DedupEngine(), mode="off")
		timeline.feedMany(["一", "二", "三"])
		self.assertEqual(timeline.texts(), ["一", "二", "三"])
		self.assertEqual({event.reason for event in timeline.events}, {"off"})

	def testIdenticalFramesAreSilent(self):
		timeline = Timeline(dedup.DedupEngine(), mode="off")
		timeline.feedMany(["同一句", "同一句"])
		self.assertEqual(timeline.texts(), ["同一句"])


class OcrJitterRegressionTests(unittest.TestCase):
	"""3.3.4 回归：真实 OCR 抖动不应造成重复播报或整段重读。

	这些用例对应真机报告的缺陷 —— 3.3.3 的用例全都喂"逐字节相同的重复帧"，
	所以一个都没抓住。
	"""

	LINE = "你好冒险者欢迎来到新手村"

	def setUp(self):
		self.engine = dedup.DedupEngine()

	def testSingleCharMisreadDoesNotRepeat(self):
		"""念完之后某帧认错一个字，随后恢复正常：只能有一次播报。"""
		timeline = Timeline(self.engine, mode="commit", frame=1.0)
		timeline.hold(self.LINE)
		timeline.feed("你好冒险者欢迎来到新手材")  # 错一个字
		timeline.hold(self.LINE)
		self.assertEqual(timeline.texts(), [self.LINE])

	def testTrailingPunctuationFlickerDoesNotRepeat(self):
		timeline = Timeline(self.engine, mode="commit", frame=1.0)
		timeline.hold(self.LINE)
		timeline.feed(self.LINE + "。")
		timeline.feed(self.LINE)
		timeline.hold(self.LINE)
		self.assertEqual(timeline.texts(), [self.LINE])

	def testMissingSpaceDoesNotRepeat(self):
		"""行拼接产生的空格差异不算新内容。"""
		text = "第一行对话 第二行对话"
		timeline = Timeline(self.engine, mode="commit", frame=1.0)
		timeline.hold(text)
		timeline.feed("第一行对话第二行对话")  # 空格消失
		timeline.hold("第一行对话第二行对话")
		self.assertEqual(timeline.texts(), [text])

	def testSingleEmptyFrameDoesNotRepeat(self):
		"""一帧 OCR 闪空（插件会主动喂空文本）不应导致整句重念。"""
		timeline = Timeline(self.engine, mode="commit", frame=1.0)
		timeline.hold(self.LINE)
		timeline.feed("")  # 闪空一帧
		timeline.hold(self.LINE)
		self.assertEqual(timeline.texts(), [self.LINE])

	def testAlternatingVariantsDoNotLoop(self):
		"""两个抖动的变体交替出现 60 帧：只能播报一次，不能每 10 秒重复一次。"""
		timeline = Timeline(self.engine, mode="commit", frame=1.0)
		variantA = self.LINE
		variantB = "你好冒险者欢迎来到新手材"
		timeline.feed(variantA)
		for index in range(60):
			timeline.feed(variantB if index % 2 else variantA)
		self.assertEqual(len(timeline.texts()), 1, timeline.texts())

	def testJitterFrameIsNeverSpoken(self):
		"""抖动帧的内容本身绝不能被念出来。"""
		timeline = Timeline(self.engine, mode="commit", frame=1.0)
		timeline.feed("你好冒险者欢迎")
		timeline.feed("你好冒险者欢材")  # 抖动（同长、错一个字）
		timeline.hold("你好冒险者欢迎")
		self.assertNotIn("材", "".join(timeline.texts()))
		self.assertEqual(timeline.texts(), ["你好冒险者欢迎"])

	def testIncrementalDoesNotRereadAfterJitter(self):
		"""跟读：已念过"你好，冒险者。"后中间抖一下，后续只念新片段。"""
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feed("你好，冒险者。")
		timeline.feed("你好，冒俭者。")  # 抖动
		timeline.feed("你好，冒险者。欢迎来到新手村！")
		self.assertEqual(timeline.texts(), ["你好，冒险者。", "欢迎来到新手村！"])

	def testIncrementalDoesNotRereadAcrossSentences(self):
		"""跟读：三句逐句出现，每句只念一次，不重念前面的句子。"""
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feed("第一句。")
		timeline.feed("第一句。第二句。")
		timeline.feed("第一句。第二句。第三句。")
		self.assertEqual(timeline.texts(), ["第一句。", "第二句。", "第三句。"])

	def testIncrementalNeverRepeatsAPrefix(self):
		"""任何一次跟读播报都不能是上一次播报的超串（否则就是重读）。"""
		timeline = Timeline(self.engine, mode="incremental", frame=0.4)
		line = "你好，冒险者。欢迎来到新手村！这里还有第四句。"
		for index in range(1, len(line) + 1):
			timeline.feed(line[:index])
		texts = timeline.texts()
		for earlier, later in zip(texts, texts[1:]):
			self.assertNotIn(earlier, later, f"后一次播报包含了前一次：{texts}")

	def testIncrementalNoDuplicateAfterBlink(self):
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feed("第一句。")
		timeline.feed("")
		timeline.feed("第一句。第二句。")
		self.assertEqual(timeline.texts(), ["第一句。", "第二句。"])


class IncrementalJitterRegressionTests(unittest.TestCase):
	"""3.3.5 回归：跟读在句子稳定后，OCR 抖动不得导致整段重念。

	3.3.4 修好了 commit，但 incremental 缺少对等的"已播报"保护，且抖动判定用
	"公共前缀占比"，长台词里靠前的错字会被判成"换了一句" → 从头重念。
	"""

	LINE = "你好，冒险者。欢迎来到新手村，这里有很多任务等着你。"
	PHRASES = ("你好，", "你好，冒险者。", "你好，冒险者。欢迎来到新手村，")

	def setUp(self):
		self.engine = dedup.DedupEngine()

	def spokenBox(self):
		"""模拟逐段跟读并把整句念完、随后稳定若干帧。"""
		timeline = Timeline(self.engine, mode="incremental")
		for phrase in self.PHRASES:
			timeline.feedMany([phrase] * 3)
		timeline.feedMany([self.LINE] * 4)
		return timeline

	def testLongTextEarlyJitterDoesNotReread(self):
		"""用户报告的缺陷：长句稳定后，靠前位置抖一个字。"""
		timeline = self.spokenBox()
		before = len(timeline.texts())
		self.assertEqual(before, 4, timeline.texts())
		jitter = self.LINE[:3] + "X" + self.LINE[4:]
		timeline.feedMany([jitter] + [self.LINE] * 4)
		self.assertEqual(timeline.texts(), [
			"你好，",
			"冒险者。",
			"欢迎来到新手村，",
			"这里有很多任务等着你。",
		])

	def testLongTextMidJitterDoesNotReread(self):
		timeline = self.spokenBox()
		jitter = self.LINE[:20] + "X" + self.LINE[21:]
		timeline.feedMany([jitter] + [self.LINE] * 3)
		self.assertEqual(len(timeline.texts()), 4, timeline.texts())

	def testLongTextTwoCharJitterDoesNotReread(self):
		timeline = self.spokenBox()
		jitter = self.LINE[:5] + "XY" + self.LINE[7:]
		timeline.feedMany([jitter] + [self.LINE] * 3)
		self.assertEqual(len(timeline.texts()), 4, timeline.texts())

	def testAlternatingVariantsDoNotReread(self):
		timeline = self.spokenBox()
		jitter = self.LINE[:3] + "X" + self.LINE[4:]
		timeline.feedMany([jitter if index % 2 else self.LINE for index in range(30)])
		self.assertEqual(len(timeline.texts()), 4, timeline.texts())

	def testTruncatedTailThenRestoredDoesNotReread(self):
		timeline = self.spokenBox()
		timeline.feedMany([self.LINE[:18]] + [self.LINE] * 3)
		self.assertEqual(len(timeline.texts()), 4, timeline.texts())

	def testMultiSentenceBoxEarlyJitterDoesNotReread(self):
		"""两句都念完后，第一句内抖动：不能重念整个文本框。"""
		first = "你好，冒险者。"
		box = first + "欢迎来到新手村，这里有很多任务。"
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feedMany([first] * 3 + [box] * 4)
		spoken = list(timeline.texts())
		jitter = box[:3] + "X" + box[4:]
		timeline.feedMany([jitter] + [box] * 3)
		self.assertEqual(timeline.texts(), spoken)

	def testNewSentenceAfterJitterIsSpoken(self):
		"""抖动抑制不能把真正的新台词也吞掉。"""
		timeline = self.spokenBox()
		jitter = self.LINE[:3] + "X" + self.LINE[4:]
		timeline.feedMany([jitter] + [self.LINE] * 2)
		newLine = "老板你好，有什么可以帮你的吗？"
		timeline.feedMany([newLine] * 4)
		self.assertIn(newLine, timeline.texts())

	def testSharedOpeningNewSentenceIsSpoken(self):
		"""新句与旧句共享开头（相似度约 0.5）时必须完整念出。"""
		timeline = self.spokenBox()
		newLine = "你好，冒险者。我是村长，有事请找我。"
		timeline.feedMany([newLine] * 4)
		self.assertIn(newLine, timeline.texts())

	def testDigitChangeIsTreatedAsNewContent(self):
		"""数字/编号变化代表游戏状态更新，不能被抖动逻辑吞掉。"""
		timeline = Timeline(self.engine, mode="incremental", frame=1.0)
		timeline.feedMany(["HP: 100"] * 3 + ["HP: 90"] * 4)
		self.assertIn("90", "".join(timeline.texts()), timeline.texts())

	def testPunctuationOnlyChunkIsNotSpoken(self):
		"""末尾多一个感叹号：不应单独念出"！"。"""
		timeline = Timeline(self.engine, mode="incremental")
		timeline.feedMany([self.LINE] * 3 + [self.LINE + "！"] * 4)
		punctuationChunks = [t for t in timeline.texts() if not dedup.hasSpeakableContent(t)]
		self.assertEqual(punctuationChunks, [])
		self.assertNotIn("！", timeline.texts())

	def testNoChunkRepeatsEarlierContent(self):
		"""属性断言：任何一次跟读播报都不应包含已播报过的内容。"""
		timeline = self.spokenBox()
		jitter = self.LINE[:3] + "X" + self.LINE[4:]
		timeline.feedMany([jitter] + [self.LINE] * 3 + [self.LINE + "村长在等你。"] * 3)
		texts = [text for text in timeline.texts() if dedup.hasSpeakableContent(text)]
		for earlier, later in zip(texts, texts[1:]):
			with self.subTest(earlier=earlier, later=later):
				self.assertNotIn(earlier, later)


class AnchorTests(unittest.TestCase):
	"""3.3.5 新增：`commonPrefixSpoken` 的模糊锚点。"""

	LINE = "你好，冒险者。欢迎来到新手村，这里有很多任务等着你。"

	def testExactPrefix(self):
		self.assertEqual(dedup.commonPrefixSpoken("你好，", "你好，世界"), 3)

	def testGrowingText(self):
		self.assertEqual(dedup.commonPrefixSpoken("你好，世界", "你好，世界。"), 5)

	def testJitterVariantAnchorsNearEnd(self):
		jitter = self.LINE[:3] + "X" + self.LINE[4:]
		anchor = dedup.commonPrefixSpoken(self.LINE, jitter)
		self.assertGreaterEqual(anchor, len(self.LINE) - 3)
		self.assertLessEqual(anchor, len(jitter))

	def testCompletelyDifferentReturnsMinusOne(self):
		self.assertEqual(
			dedup.commonPrefixSpoken(self.LINE, "老板你好，有什么可以帮你的吗？"),
			-1,
		)

	def testShortTextDoesNotUseFuzzyAnchor(self):
		"""短文本冗余不足，不做模糊锚定（否则 `HP: 100`→`HP: 90` 会锚错位置）。"""
		self.assertEqual(dedup.commonPrefixSpoken("HP: 100", "HP: 90"), -1)

	def testTruncatedTextAnchors(self):
		anchor = dedup.commonPrefixSpoken(self.LINE, self.LINE[:20])
		self.assertEqual(anchor, 20)


class SpeakableChunkTests(unittest.TestCase):
	"""3.3.5：纯标点片段不单独播报。"""

	def testChineseTextIsSpeakable(self):
		self.assertTrue(dedup.hasSpeakableContent("你好"))

	def testLatinTextIsSpeakable(self):
		self.assertTrue(dedup.hasSpeakableContent("Hello"))

	def testDigitsAreSpeakable(self):
		self.assertTrue(dedup.hasSpeakableContent("100"))

	def testPunctuationOnlyIsNotSpeakable(self):
		for chunk in ("！", "。", "，", "...", "——", " 「」"):
			with self.subTest(chunk=chunk):
				self.assertFalse(dedup.hasSpeakableContent(chunk))

	def testBlankIsNotSpeakable(self):
		self.assertFalse(dedup.hasSpeakableContent("   "))


class DigitDifferenceTests(unittest.TestCase):
	"""3.3.5：差异里的 ASCII 数字代表状态变化，必须算新内容。"""

	def testDifferenceSnippet(self):
		self.assertEqual(dedup.differenceSnippet("第1帧", "第2帧"), ("1", "2"))
		self.assertEqual(dedup.differenceSnippet("你好，冒险者。", "你好，X险者。"), ("冒", "X"))
		self.assertEqual(dedup.differenceSnippet("abc", "abc"), ("", ""))

	def testCounterChangesAreReplace(self):
		for previous, current in (
			("第1帧", "第2帧"),
			("HP: 100", "HP: 90"),
			("ABCDEFG1", "ABCDEFG2"),
		):
			with self.subTest(previous=previous, current=current):
				self.assertEqual(dedup.classifyChange(previous, current), "replace")

	def testAsciiDigitDetection(self):
		self.assertTrue(dedup.hasAsciiDigit("abc1"))
		self.assertFalse(dedup.hasAsciiDigit("你好"))
		self.assertFalse(dedup.hasAsciiDigit(""))


class BoundaryRegressionTests(unittest.TestCase):
	"""3.3.4：边界规则修正（开括号不再当边界；一行以标点结尾仍可切）。"""

	def testLineEndingWithCommaCanBeReleased(self):
		self.assertEqual(dedup.findBoundary("你好，", 0), 3)

	def testOpeningBracketIsNotBoundary(self):
		self.assertEqual(dedup.findBoundary("他说「你好", 0), -1)
		self.assertEqual(dedup.findBoundary("He said (hello", 0), -1)

	def testClosingBracketIsBoundary(self):
		self.assertEqual(dedup.findBoundary("他说「你好」", 0), 6)

	def testEnglishWordNotSplitWithoutPunctuation(self):
		self.assertEqual(dedup.findBoundary("Hello adv", 0), -1)

	def testEnglishCommaBoundary(self):
		self.assertEqual(dedup.findBoundary("Hello, world", 0), 6)

	def testMinChunkLengthRespected(self):
		self.assertEqual(dedup.findBoundary("你，好世界", 0, minChars=4), -1)


class ChangeClassifierTests(unittest.TestCase):
	"""3.3.4 新增的分类器：抖动 / 增长 / 替换 的判定是全部修复的基础。"""

	def testSame(self):
		self.assertEqual(dedup.classifyChange("一句话", "一句话"), "same")
		self.assertEqual(dedup.classifyChange("一行 两行", "一行两行"), "same")

	def testEmpty(self):
		self.assertEqual(dedup.classifyChange("一句话", ""), "empty")

	def testGrowthBeatsJitter(self):
		self.assertEqual(dedup.classifyChange("你好", "你好世界"), "growth")
		self.assertEqual(dedup.classifyChange("你好", "你好世"), "growth")

	def testJitter(self):
		self.assertEqual(dedup.classifyChange("你好世界", "你好世材"), "jitter")
		self.assertEqual(dedup.classifyChange("Hello world", "Hello warld"), "jitter")

	def testTrailingPunctuationIsGrowth(self):
		"""末尾多一个标点是前缀延长（增长），不是抖动；两者都不刷新播报记录。"""
		self.assertEqual(dedup.classifyChange("你好世界", "你好世界。"), "growth")

	def testReplace(self):
		self.assertEqual(dedup.classifyChange("你好世界", "老板你好有什么可以帮你的吗"), "replace")

	def testEmptyPreviousIsGrowth(self):
		self.assertEqual(dedup.classifyChange("", "第一句"), "growth")


class EmptyGraceTests(unittest.TestCase):
	def testOneEmptyFrameKeepsState(self):
		engine = dedup.DedupEngine()
		engine.observe(1, "一句话", "commit", 1500, 1.0)
		engine.observe(1, "", "commit", 1500, 2.0)
		self.assertIsNotNone(engine.stateFor(1))
		self.assertEqual(engine.stateFor(1).stableText, "一句话")

	def testLongEmptyResetsState(self):
		engine = dedup.DedupEngine()
		engine.observe(1, "一句话", "commit", 1500, 1.0)
		for index in range(2, 8):
			engine.observe(1, "", "commit", 1500, float(index))
		state = engine.stateFor(1)
		self.assertEqual(state.stableText, "")
		self.assertEqual(state.announced, "")


class ModeSwitchTests(unittest.TestCase):
	def testSwitchingModeClearsStrategyState(self):
		engine = dedup.DedupEngine()
		engine.observe(1, "一句话", "classic", 1500, 1.0)
		engine.observe(1, "一句话", "commit", 1500, 1.5)
		state = engine.stateFor(1)
		self.assertEqual(state.mode, "commit")
		self.assertEqual(state.announced, "", "切换策略后不应继承旧的播报记录")

	def testHoldMsIsClamped(self):
		engine = dedup.DedupEngine()
		# 传字符串也不应抛异常
		engine.observe(1, "一句话", "commit", "1500", 1.0)
		engine.observe(1, "一句话", "commit", 99999, 2.0)


class StateLifecycleTests(unittest.TestCase):
	def testResetClearsEverything(self):
		engine = dedup.DedupEngine()
		timeline = Timeline(engine, mode="commit")
		timeline.hold("一句话")
		self.assertEqual(timeline.texts(), ["一句话"])
		self.assertIsNotNone(engine.stateFor(1))
		engine.reset()
		self.assertIsNone(engine.stateFor(1))
		timeline.hold("一句话")
		self.assertEqual(timeline.texts(), ["一句话", "一句话"])

	def testResetRegionOnlyAffectsOne(self):
		engine = dedup.DedupEngine()
		engine.observe(1, "甲", "commit", HOLD, 0.0)
		engine.observe(2, "乙", "commit", HOLD, 0.0)
		engine.resetRegion(1)
		self.assertIsNone(engine.stateFor(1))
		self.assertIsNotNone(engine.stateFor(2))

	def testPruneDropsRemovedRegions(self):
		engine = dedup.DedupEngine()
		engine.observe(1, "甲", "commit", HOLD, 0.0)
		engine.observe(2, "乙", "commit", HOLD, 0.0)
		engine.prune([{"id": 2}])
		self.assertIsNone(engine.stateFor(1))
		self.assertIsNotNone(engine.stateFor(2))

	def testPruneToleratesGarbage(self):
		engine = dedup.DedupEngine()
		engine.prune(None)
		engine.prune([None, {}, {"id": 1}])
		self.assertIsNone(engine.stateFor(1))

	def testRegionsAreIndependent(self):
		"""两个区域各自一套状态：A 念过的东西不影响 B。"""
		engine = dedup.DedupEngine()
		engine.observe(1, "同一句话", "classic", HOLD, 0.0, 0.5)
		second = engine.observe(2, "同一句话", "classic", HOLD, 0.0, 0.5)
		self.assertEqual([event.text for event in second], ["同一句话"])

	def testModeSwitchTakesEffectImmediately(self):
		engine = dedup.DedupEngine()
		first = Timeline(engine, mode="classic")
		first.feed("一句话")
		second = Timeline(engine, mode="commit")
		second.now = first.now
		second.feed("一句话")
		self.assertEqual(second.texts(), [])


if __name__ == "__main__":
	unittest.main(verbosity=2)
