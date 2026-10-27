# SPDX-License-Identifier: GPL-2.0-or-later
# LION 修改版 (LION modified) — 去重策略
# 本文件为修改版新增，遵循 GNU GPL v2 或更高版本发布。
#
# Copyright (C) 2026 xiaoshunxing — 分区可选的多套去重策略

"""按区域可选的多套去重策略。

背景：原版/3.3.2 的相似度去重假设"一次显示完整文本"（影视字幕）。对 RPG 那种
**逐字出现**的对话，每一帧都是上一帧的前缀延长，相似度一路升高（0.67、0.80、
0.94…），于是只在第一帧念了"你"就把整句判为"没变"而永远不再朗读。

本模块提供四套策略，区域之间互不影响：

``classic``
	原有相似度去重，行为与 3.3.2 完全一致；影视字幕用。
``commit``
	等文本**停止变化** ``holdMs`` 毫秒后，整句朗读一次；打字机对话用。
``incremental``
	在标点/停顿处按短语释放，边说边出；长对话用。
``off``
	每次变化都念，仅用于排障。

**3.3.4 的关键修正：不再用"整串相等/整串前缀"来判断"还是不是同一句"。**
真实 OCR 每帧都会抖：少一个标点、多一个空格、个别字识别不同、偶尔整帧读空。
3.3.3 把任何差异都当成"新内容"，于是（a）念过的记录被清空 → 同一句反复播报；
（b）跟读的位置锚点被清零 → 每段都从头念。现在改为：

* 每一帧先和上一帧**分类**：``same`` / ``empty`` / ``growth`` / ``jitter`` / ``replace``
  （见 :func:`classifyChange`）；
* ``jitter``（一两个字的抖动）只更新"上一帧"，**不**刷新稳定计时、**不**清空已播报记录；
* 播报永远使用 ``stableText``（最后一次实质变化的帧），抖动帧的内容不会被念出来；
* 跟读增量以**已播报的内容前缀**为锚点（:func:`spokenStart`），而不是"上一帧全文"，
  因此抖动、换行重排、行序变化都不会让已念过的部分重念；
* 空帧有**宽限期**（:data:`EMPTY_GRACE_SECONDS` 或 2 倍 hold），只有连续空帧超过宽限
  才真正复位，避免一帧闪空就把整句重念。

本模块**不导入任何 NVDA 模块**（也不做翻译初始化），因此可以在普通 Python 下
完整测试；状态机只依赖时间戳，测试可以用虚拟时间精确驱动。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import NamedTuple

#: 合法策略名，顺序即界面下拉框顺序
MODES = ("classic", "commit", "incremental", "off")

#: 判定"文本已稳定"的默认毫秒数
DEFAULT_HOLD_MS = 1500

#: 允许的最小/最大稳定阈值
HOLD_MIN_MS = 500
HOLD_MAX_MS = 10000

#: 文本持续变化但一直不满足释放条件时，最多等这么久就强制释放一次，
#: 避免屏幕上永远有动画/滚动条时"一句都不读"。
#: **3.3.4 起只统计"内容不再增长"的时间**，所以正常长句逐字打字不会被它打断。
MAX_PENDING_SECONDS = 10.0

#: 空帧宽限：连续空帧超过这个时间才认为"对话框真的清空了"（并复位状态）。
#: 取 ``max(EMPTY_GRACE_SECONDS, 2*holdMs)``，避免一帧 OCR 闪空导致整句重念。
EMPTY_GRACE_SECONDS = 2.0

#: 抖动判定：去掉空白后编辑距离不超过这个值，就认为"还是同一句、只是识别抖了"。
#: 短文本只容忍 1 处编辑，长文本（≥ JITTER_LONG_TEXT_LENGTH）容忍 2 处。
JITTER_EDIT_DISTANCE = 1

#: 长文本允许的编辑距离（3.3.5）：长台词里 OCR 偶尔一次错两个字很常见。
JITTER_EDIT_DISTANCE_LONG = 2

#: 超过这个长度才算"长文本"，可以用更宽的抖动容差。
JITTER_LONG_TEXT_LENGTH = 12

#: 使用放宽容差（2 处编辑）时，整体相似度必须达到这个值。
JITTER_LONG_MIN_SIMILARITY = 0.8

#: 太短的字符串（1–2 字）不判抖动：`是`→`否`、`好`→`坏` 是不同的内容。
JITTER_MIN_LENGTH = 3

#: "这句刚念过"的模糊相似度阈值（防止抖动修好后把同一句再念一遍）
ALREADY_ANNOUNCED_RATIO = 0.90

#: 模糊锚点：已播报内容在新文本里的匹配占比达到这个值，才认为"还是同一段、可续读"。
ANCHOR_MATCH_RATIO = 0.85

#: 模糊锚点的最小长度：短文本（如 `HP: 100`）冗余太少，模糊匹配容易锚到错误位置，
#: 短文本仍按"换了一句"处理，整句重念更安全。
ANCHOR_MIN_LENGTH = 12

#: 跟读片段的最小长度：太短（1 个字）不单独播报，避免中文按字蹦
MIN_CHUNK_CHARS = 2

#: 前缀比较的扫描上限，避免超长文本上的性能问题
PREFIX_CAP = 160

#: 强边界字符：句读标点与闭括号。出现在它们之后**一定**可以切分（后面接什么都行）。
STRONG_BOUNDARY = set("，。！？；：、…—」』）】〉》》,.!?;:)]}")

#: 弱边界字符：空白。只有在它后面紧跟标点/空白时才切分，
#: 否则 `"Hello a"` 这种"空格后面还在打词"的情况会切出半个词。
WEAK_BOUNDARY = set(" \t\n\r\f\v")

#: 兼容旧名字（3.3.3 的测试/外部引用）
BOUNDARY_CHARS = STRONG_BOUNDARY | WEAK_BOUNDARY

_PUNCT = STRONG_BOUNDARY
_BLANK = WEAK_BOUNDARY


def findBoundary(text: str, start: int = 0, minChars: int = MIN_CHUNK_CHARS) -> int:
	"""返回可以"切到这里为止"的位置（最后一个完整短语的结尾），没有则 -1。

	规则（3.3.4）：

	* **强边界**（句读标点、闭括号）：之后即使还有文字也可以切，例如
	  ``"你好，世界"`` → 3，可念 ``"你好，"``；
	* **弱边界**（空白）：只有它后面是标点/空白时才切，避免切出半个英文词
	  （``"Hello a"`` → 不切；``"Hello, a"`` → 在逗号后切）；
	* 开括号（``「（【``）**不是**边界，否则 ``"他说「你好"`` 会被切成 ``"他说「"``；
	* 片段长度不小于 ``minChars``，避免中文按字蹦。
	"""
	text = text or ""
	start = max(0, start)
	if start >= len(text):
		return -1
	end = len(text)
	# 只剥末尾空白；**末尾标点要保留**——"你好，"这种以逗号结尾的一行正应在此切分
	while end > start and text[end - 1] in _BLANK:
		end -= 1
	if end <= start:
		return -1
	for position in range(end, start, -1):
		candidate = position
		char = text[candidate - 1]
		if candidate - start < minChars:
			continue
		if char in _PUNCT:
			# 强边界：短语已完整，后面剩什么都无所谓
			return candidate
		if char in _BLANK:
			# 弱边界：要求后面紧跟标点/空白，否则可能切在半个词中间
			following = text[candidate] if candidate < len(text) else ""
			if following and (following in _BLANK or following in _PUNCT):
				return candidate
			continue
	return -1


def clampMode(value) -> str:
	"""把任意输入规整成合法策略名（未知值一律退回 classic，即旧行为）。"""
	if isinstance(value, str):
		candidate = value.strip().lower()
		if candidate in MODES:
			return candidate
	return "classic"


def clampHold(value, default: int = DEFAULT_HOLD_MS) -> int:
	"""把稳定阈值规整成 HOLD_MIN_MS–HOLD_MAX_MS 的整数毫秒。"""
	try:
		number = int(round(float(value)))
	except (TypeError, ValueError):
		return default
	return max(HOLD_MIN_MS, min(HOLD_MAX_MS, number))


def similarityRatio(previous: str, current: str) -> float:
	"""比较两段识别文本的相似度（0.0–1.0）。``classic`` 策略用。

	**必须显式传 `autojunk=False`。** `difflib` 默认会对长度 ≥ 200 的序列启用
	"autojunk"，把出现次数超过 1% 的元素当成噪声忽略；中日韩文本里常用字重复率极高，
	一旦文本位置/行序有偏移（例如本帧多识别出上一行、行序被重排），相似度会被**大幅低估**
	（实测 250 字重复字幕只偏移 30 字：0.94 → 0.11）。而 `classic` 的策略是
	"相似度低于阈值才朗读"，低估就会把同一句当成"变了"，**每帧反复朗读**。
	识别结果通常只有几十个字，关掉这个启发式既准确又几乎不增加开销。
	"""
	try:
		return SequenceMatcher(None, previous, current, autojunk=False).ratio()
	except Exception:  # noqa: BLE001 - 比较失败时视为"变了"，宁可多读一次
		return 0.0


def squash(text: str) -> str:
	"""去掉所有空白后的文本，用于"忽略空格差异"的比较。"""
	return "".join((text or "").split())


def editDistanceWithin(a: str, b: str, limit: int) -> bool:
	"""判断 ``a`` 与 ``b`` 的编辑距离是否 **不超过** ``limit``。

	用于"抖动"判定（limit 通常为 1）。用滚动行的 Levenshtein DP，超限提前退出：
	插入、删除、替换都算一次编辑（只按位置比较会把 ``ABXDE``/``ABYDE`` 之外的情况算错，
	例如 ``ABCDE`` 与 ``ABCXDE`` 的插入需要真正的最短编辑距离）。
	"""
	a = a or ""
	b = b or ""
	if abs(len(a) - len(b)) > limit:
		return False
	if a == b:
		return True
	previous = list(range(len(b) + 1))
	for indexA in range(1, len(a) + 1):
		current = [indexA] + [0] * len(b)
		best = current[0]
		for indexB in range(1, len(b) + 1):
			cost = 0 if a[indexA - 1] == b[indexB - 1] else 1
			value = min(
				previous[indexB] + 1,  # 删除
				current[indexB - 1] + 1,  # 插入
				previous[indexB - 1] + cost,  # 替换
			)
			current[indexB] = value
			if value < best:
				best = value
		if best > limit:
			# 本行最小值已超限，不可能再降回来
			return False
		previous = current
	return previous[-1] <= limit


def differenceSnippet(a: str, b: str):
	"""返回 ``a`` 与 ``b`` 各自"去掉公共前缀/后缀之后"的中间片段。

	用于判断差异是不是"数字在变"（计数器、血量、倒计时）：`第1帧`→`第2帧` 得到
	``("1", "2")``；`你好，冒险者。`→`你好，X险者。` 得到 ``("冒", "X")``。
	"""
	a = a or ""
	b = b or ""
	limit = min(len(a), len(b))
	prefix = 0
	while prefix < limit and a[prefix] == b[prefix]:
		prefix += 1
	suffix = 0
	while suffix < limit - prefix and a[len(a) - 1 - suffix] == b[len(b) - 1 - suffix]:
		suffix += 1
	return a[prefix:len(a) - suffix], b[prefix:len(b) - suffix]


def hasAsciiDigit(text: str) -> bool:
	"""片段里是否含 ASCII 数字（这类变化通常代表游戏状态，应算新内容）。"""
	return any(char.isascii() and char.isdigit() for char in (text or ""))


def hasSpeakableContent(chunk: str) -> bool:
	"""片段里是否有可读内容（字母/数字/汉字…）。

	纯标点（例如单独一个 `"！"`）不值得单独播报；`str.isalnum()` 对中日韩汉字返回 True。
	"""
	return any(char.isalnum() for char in (chunk or ""))


def classifyChange(previous: str, current: str, jitterDistance: int = JITTER_EDIT_DISTANCE) -> str:
	"""把"这一帧相对上一帧"分类成 ``same`` / ``empty`` / ``growth`` / ``jitter`` / ``replace``。

	顺序很重要：

	1. ``empty``：这一帧没识别到文字（闪空）。
	2. ``same``：完全相同，或只差空白（行拼接产生的空格差异）。
	3. ``growth``：以旧文本为前缀的延长 —— 打字机在继续打字。必须在 jitter 之前判断，
	   否则"多打了一个字"会被误判成抖动。
	4. ``jitter``：**长度自适应**的编辑距离内，且差异不是数字在变（3.3.5 起不再看
	   "公共前缀占比"——那会让长台词里靠前的错字被误判成换了一句）。
	5. ``replace``：其它情况 —— 换了一句新台词。
	"""
	if not current:
		return "empty"
	if not previous:
		return "growth"
	if current == previous or squash(current) == squash(previous):
		return "same"
	if current.startswith(previous):
		return "growth"
	left = squash(previous)
	right = squash(current)
	shortSide = min(len(left), len(right))
	if shortSide < JITTER_MIN_LENGTH:
		# 太短的文本没有冗余，任何差异都可能是新内容（如选项 `是`/`否`）
		return "replace"
	longText = shortSide >= JITTER_LONG_TEXT_LENGTH
	limit = max(jitterDistance, JITTER_EDIT_DISTANCE_LONG) if longText else jitterDistance
	if not editDistanceWithin(left, right, limit):
		return "replace"
	middleLeft, middleRight = differenceSnippet(left, right)
	if hasAsciiDigit(middleLeft) or hasAsciiDigit(middleRight):
		# 数字/编号在变（`第1帧`→`第2帧`、`HP: 100`→`HP: 90`）→ 新内容
		return "replace"
	if longText and limit > jitterDistance and similarityRatio(left, right) < JITTER_LONG_MIN_SIMILARITY:
		# 两处编辑但整体不够像：更可能是另一句话
		return "replace"
	return "jitter"


def commonPrefixSpoken(spoken: str, text: str, cap: int = PREFIX_CAP) -> int:
	"""已播报内容 ``spoken`` 在新文本 ``text`` 里"覆盖到"的位置（-1 表示找不到）。

	三层判定（3.3.5 起加入第三层）：

	1. **精确公共前缀**：最常见的情况，O(n)。
	2. **容忍位置错动**：``spoken`` 的某个后缀正好是 ``text`` 的开头
	   （例如已念 ``你好，``、新帧是 ``你 好，世界``）。
	3. **模糊锚点**：上面都不成立时，用 `SequenceMatcher` 求匹配块；若已播报内容有
	   ``ANCHOR_MATCH_RATIO`` 以上仍出现在新文本里，返回其结束位置。

	第 3 层是修复"跟读在句子稳定后重复播报"的关键：长台词里靠前的一个错字会让
	精确前缀判定立刻失败，旧实现返回 -1 后就把播报记录清空、从头重念。
	"""
	spoken = spoken or ""
	text = text or ""
	if not spoken or not text:
		return -1
	length = min(len(spoken), len(text), cap)
	matched = 0
	while matched < length and spoken[matched] == text[matched]:
		matched += 1
	if matched >= length:
		return min(len(spoken), len(text))
	# 容忍位置错动：spoken 的尾部 == text 的开头
	for candidate in range(min(len(text), cap), 0, -1):
		if candidate <= matched:
			break
		if spoken.endswith(text[:candidate]):
			return candidate
	# 模糊锚点：已念内容大部分仍在新文本里 → 认为续读点在匹配块的末尾。
	# 只在长文本上启用：短文本冗余不足，模糊匹配可能锚错位置（见 ANCHOR_MIN_LENGTH）。
	if len(spoken) < ANCHOR_MIN_LENGTH:
		return -1
	try:
		window = text[:cap]
		matcher = SequenceMatcher(None, spoken, window, autojunk=False)
		covered = 0
		end = 0
		for block in matcher.get_matching_blocks():
			if block.size == 0:
				break
			covered += block.size
			if block.b + block.size > end:
				end = block.b + block.size
		if covered >= max(2, int(ANCHOR_MATCH_RATIO * len(spoken))):
			return min(end, len(text))
	except Exception:  # noqa: BLE001 - 匹配失败时退回"从头念"由调用方决定
		return -1
	return -1


class SpeechEvent(NamedTuple):
	"""一次"该念这段文本"的裁决结果。"""

	text: str
	#: classic / commit / incremental / off / forced，便于日志与测试断言
	reason: str


class _RegionState:
	"""单个区域的状态机数据。"""

	__slots__ = (
		"mode",
		"lastSeen",
		"lastRealText",
		"stableText",
		"holdSince",
		"lastGrowthSince",
		"unstableSince",
		"announced",
		"announcedFull",
		"released",
		"emptySince",
	)

	def __init__(self, now: float, mode: str):
		self.mode = mode
		self.lastSeen = ""
		self.lastRealText = ""
		self.stableText = ""
		self.holdSince = now
		self.lastGrowthSince = now
		self.unstableSince = now
		self.announced = ""
		self.announcedFull = ""
		self.released = False
		self.emptySince = None

	def reset(self, now: float) -> None:
		self.lastSeen = ""
		self.lastRealText = ""
		self.stableText = ""
		self.holdSince = now
		self.lastGrowthSince = now
		self.unstableSince = now
		self.announced = ""
		self.announcedFull = ""
		self.released = False
		self.emptySince = None


class DedupEngine:
	"""按区域维护去重状态，并给出"这一帧该念什么"。

	典型用法（插件每帧对每个勾选区域调用一次）::

		events = engine.observe(regionId, text, mode, holdMs, now, threshold)

	`now` 由调用方传入（插件用 `time.monotonic()`，测试用虚拟时间），
	状态机内部不读时钟，因此完全可重现。
	"""

	def __init__(self):
		self._states: dict = {}

	def reset(self) -> None:
		"""清空全部区域状态（开始/停止识别时调用）。"""
		self._states.clear()

	def resetRegion(self, regionId) -> None:
		self._states.pop(regionId, None)

	def prune(self, configuredRegions) -> None:
		"""丢掉已删除区域的状态，避免字典随着增删区域无限增长。"""
		alive = set()
		for region in configuredRegions or []:
			try:
				alive.add(region["id"])
			except (TypeError, KeyError):
				continue
		for regionId in list(self._states):
			if regionId not in alive:
				self._states.pop(regionId, None)

	def stateFor(self, regionId) -> _RegionState | None:
		"""测试/排障用：取状态快照的**副本**（不暴露可变内部状态）。"""
		state = self._states.get(regionId)
		if state is None:
			return None
		clone = _RegionState(0.0, state.mode)
		for name in _RegionState.__slots__:
			setattr(clone, name, getattr(state, name))
		return clone

	def observe(
		self,
		regionId,
		text: str,
		mode: str,
		holdMs: int,
		now: float,
		threshold: float = 0.5,
	) -> list:
		"""处理一帧识别结果，返回本帧应当朗读的事件列表（多数帧为空）。

		`threshold` 只在 `classic` 策略里使用（相似度**低于**阈值才朗读）；
		`commit`/`incremental`/`off` 不读它。调用方可以按区域传入解析后的
		有效阈值（区域覆盖或全局默认），本状态机与全局配置无关。
		"""
		mode = clampMode(mode)
		holdMs = clampHold(holdMs)
		text = text or ""
		state = self._states.get(regionId)
		if state is None:
			state = _RegionState(now, mode)
			self._states[regionId] = state
		elif state.mode != mode:
			# 切换策略：旧策略留下的 released/announced 对新策略没有意义
			self._states[regionId] = state = _RegionState(now, mode)
			state.lastSeen = text

		# ---- 空帧：宽限处理，避免一帧闪空就把整句重念
		if not text:
			if state.emptySince is None:
				state.emptySince = now
			elif (now - state.emptySince) >= max(EMPTY_GRACE_SECONDS, holdMs * 2 / 1000.0):
				state.reset(now)
			state.lastSeen = text
			return []
		state.emptySince = None

		kind = classifyChange(state.lastRealText, text)
		if kind in ("jitter", "same"):
			# 抖动/仅空白差异：只记录上一帧，不刷新稳定计时、不动已播报记录，
			# 也不把这一帧当作播报文本（播报始终用 stableText）
			state.lastSeen = text
		else:  # growth / replace
			state.lastSeen = text
			state.lastRealText = text
			state.stableText = text
			state.holdSince = now
			# 有实质进展（增长或换内容）就重置兜底计时：
			# 正常打字是 growth，永远不会被 10 秒兜底打断；
			# 只有"内容反复变却始终不稳定"（渲染动画/滚动）或抖动时它才会累积。
			state.unstableSince = now
			if kind == "growth":
				state.lastGrowthSince = now
			state.released = False

		if mode == "classic":
			return self._classic(state, text, threshold, now)
		if mode == "off":
			return self._off(state, text, now)
		if mode == "incremental":
			return self._incremental(state, text, holdMs, now)
		return self._commit(state, text, holdMs, now)

	# ---------------------------------------------------------------- 辅助判定

	def _isStable(self, state: _RegionState, now: float, holdMs: int) -> bool:
		return (now - state.holdSince) * 1000.0 >= holdMs

	def _shouldForce(self, state: _RegionState, now: float) -> bool:
		"""内容"不再有新的进展"已超过上限 → 视为屏幕上永远有动画/滚动，强制释放一次。

		3.3.4 起：
		* ``unstableSince`` 只在出现**实质进展**（growth：文字变长；replace：换了一句）
		  时刷新，所以正常的长句逐字打字永远不会被 10 秒兜底打断；
		* 抖动帧（jitter：只差一个错字/空白）与完全相同的帧**不刷新**它；
		  因此"每帧都在变、却一直不稳定"的滚动框/动画，会在最后一次实质进展
		  过去 10 秒后被兜底念一次，而不是永远不念。
		"""
		return (now - state.unstableSince) >= MAX_PENDING_SECONDS

	# ---------------------------------------------------------------- 策略实现

	def _classic(self, state: _RegionState, text: str, threshold: float, now: float) -> list:
		"""原有行为：与上次朗读的文本相似度**低于**阈值才念（阈值越低越严格）。"""
		changed = similarityRatio(state.announcedFull, text) < threshold
		state.holdSince = now
		if not changed:
			return []
		state.announced = text
		state.announcedFull = text
		state.released = True
		return [SpeechEvent(text, "classic")]

	def _off(self, state: _RegionState, text: str, now: float) -> list:
		"""排障用：只要和上次朗读的文本不同就念。"""
		# 与上一帧是否相同由 observe() 的 jitter/same 分支处理；
		# 这里只关心"和已朗读过的内容是否不同"，避免同一句念两遍。
		changed = state.announcedFull != text
		state.holdSince = now
		if not changed:
			return []
		state.announced = text
		state.announcedFull = text
		state.released = True
		return [SpeechEvent(text, "off")]

	def _unspokenTail(self, announced: str, text: str) -> str:
		"""返回 ``text`` 里**还没念过**的部分；没有新内容时返回空串。

		这是修复"重复播报"的核心：只有确认出现了新内容才返回非空。
		判定顺序很关键 —— 先判"已念过的变体/截断"，再判"前缀延长"，
		否则 `ABCDE` vs `ABXDE` 这种同长抖动会被当成前缀分支而把整句再念一遍。
		"""
		if not announced:
			return text
		# 已念内容比当前帧长（末尾被裁掉）→ 没有新内容
		if announced.startswith(text):
			return ""
		# 忽略空白后与已念内容几乎一样（一个字符的抖动/换行差异）→ 没有新内容
		squashed = squash(text)
		spoken = squash(announced)
		if spoken and len(squashed) <= len(spoken) and editDistanceWithin(spoken, squashed, JITTER_EDIT_DISTANCE):
			return ""
		# 真正的延长：只念新增部分
		if text.startswith(announced):
			return text[len(announced):]
		# 滚动框：已念内容的一部分仍留在开头，只念它之后的新内容
		anchor = commonPrefixSpoken(announced, text)
		if anchor >= max(1, len(announced) // 2):
			return text[anchor:]
		return text

	def _commit(self, state: _RegionState, text: str, holdMs: int, now: float) -> list:
		"""稳定后朗读：文本停止变化 holdMs 后念一次；同一句不再重复。"""
		stable = self._isStable(state, now, holdMs)
		forced = (not stable) and self._shouldForce(state, now)
		# 已经释放过、并且这一帧只是同一句的抖动变体 → 直接忽略（防重复播报）
		if state.released and not forced:
			return []
		source = state.stableText or text
		# 抖动帧不播报：当前帧与已播报内容明显不同、但又不是新内容
		if state.announcedFull and similarityRatio(state.announcedFull, source) >= ALREADY_ANNOUNCED_RATIO:
			state.released = True
			return []
		if not stable and not forced:
			return []
		speech = self._unspokenTail(state.announced, source).strip()
		state.announced = source
		state.announcedFull = source
		state.released = True
		if not speech:
			return []
		return [SpeechEvent(speech, "forced" if forced else "commit")]

	def _incremental(self, state: _RegionState, text: str, holdMs: int, now: float) -> list:
		"""跟读增量：按短语释放；位置锚点是**已播报的内容**，不是上一帧全文。

		3.3.5 修复：拿到 `replace` 就清空播报记录会"整段重念"。现在先用模糊锚点找续读点，
		再用"已播报内容相似度"兜底，最后才考虑 `replace`。
		"""
		source = state.stableText or text
		start = 0
		if state.announced:
			anchor = commonPrefixSpoken(state.announced, source)
			if anchor >= 0:
				start = anchor
			elif state.announcedFull and similarityRatio(state.announcedFull, source) >= ALREADY_ANNOUNCED_RATIO:
				# 几乎同一段文本（重抖动/换行重排）：不要重念，按已播报长度前进
				state.announcedFull = source
				start = min(len(state.announced), len(source))
			elif classifyChange(state.announcedFull or state.announced, source) in ("jitter", "same"):
				# 只是抖动/空格差异：已念过的部分仍在，不要重念
				start = min(len(state.announced), len(source))
			else:
				# 真正换了一句：从头开始
				state.announced = ""
				state.announcedFull = ""
				start = 0
		start = min(start, len(source))
		boundary = findBoundary(source, start)
		if boundary > start:
			speech = source[start:boundary].strip()
			state.announced = source[:boundary]
			state.announcedFull = source
			state.released = True
			if not speech or not hasSpeakableContent(speech):
				# 纯标点片段（如单独一个"！"）不值得念，但锚点已推进，不会重试
				return []
			return [SpeechEvent(speech, "incremental")]
		if start >= len(source):
			return []
		stable = self._isStable(state, now, holdMs)
		forced = (not stable) and self._shouldForce(state, now)
		if not stable and not forced:
			return []
		speech = source[start:].strip()
		state.announced = source
		state.announcedFull = source
		state.released = True
		if not speech or not hasSpeakableContent(speech):
			return []
		return [SpeechEvent(speech, "forced" if forced else "incremental")]