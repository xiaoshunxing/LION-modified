# SPDX-License-Identifier: GPL-2.0-or-later
# LION 修改版 (LION modified) — 识别区域模型
# 本文件为修改版新增，遵循 GNU GPL v2 或更高版本发布。
#
# Copyright (C) 2026 xiaoshunxing — 多选区识别

"""多选区识别用的区域模型。

本模块只依赖标准库，外加 `addonHandler` 的 `_()` 翻译；**不导入任何 NVDA 的
UI/配置模块**，只做数据与几何计算：

* 区域的容错解析、归一化与 JSON 序列化；
* 由"基底矩形"换算实际识别矩形（四向百分比裁剪，算法与原版 `cropRectLTWH` 一致）；
* 多个区域矩形的并集（供"合并区域截图"一次抓屏）；
* 旧版单区域配置（`target` + 四个裁剪键）到新模型的迁移。

基底矩形的解析（导航对象 / 全屏 / 当前窗口 / 当前控件）由调用方通过 `resolveBase`
注入，因此本模块可以在普通 Python 下完整测试。
"""

from __future__ import annotations

import json

import addonHandler

addonHandler.initTranslation()

#: 目标类型，数值与原版 LION 的 `target` 配置保持一致
NAVIGATOR_OBJECT = 0
WHOLE_SCREEN = 1
CURRENT_WINDOW = 2
CURRENT_CONTROL = 3

TARGETS = (NAVIGATOR_OBJECT, WHOLE_SCREEN, CURRENT_WINDOW, CURRENT_CONTROL)

#: 目标名（英文 msgid，供插件里的 `_()` 翻译；原版 .po 已有这些译文）
TARGET_LABELS = {
	NAVIGATOR_OBJECT: "Navigator object",
	WHOLE_SCREEN: "Whole Screen",
	CURRENT_WINDOW: "current window",
	CURRENT_CONTROL: "current control",
}

#: 旧配置里表示目标与裁剪的键
LEGACY_TARGET_KEY = "target"
LEGACY_CROP_KEYS = ("cropUp", "cropDown", "cropLeft", "cropRight")

#: 单个区域允许的最大名称长度
NAME_LIMIT = 40

#: 识别矩形的最小边长（原版同样是 10 像素）
MIN_SIDE = 10

#: 默认区域：整屏、不裁剪、经典去重（与 3.3.2 行为一致）
DEFAULT_REGION = {
	"id": 1,
	"name": "",
	"enabled": True,
	"target": WHOLE_SCREEN,
	"cropUp": 0,
	"cropDown": 0,
	"cropLeft": 0,
	"cropRight": 0,
	"dedup": "classic",
	"dedupHoldMs": 1500,
	#: 经典相似度阈值覆盖；None = 跟随全局默认值（旧配置/新区域的默认行为）
	"classicThreshold": None,
}

#: 去重策略名（与 dedup.MODES 一致；这里单独列出以免 regions 依赖 dedup）
DEDUP_MODES = ("classic", "commit", "incremental", "off")

#: 稳定阈值的默认值与范围（与 dedup 模块保持一致）
DEDUP_HOLD_DEFAULT = 1500
DEDUP_HOLD_MIN = 500
DEDUP_HOLD_MAX = 10000


def clampPercent(value, default: int = 0) -> int:
	"""把裁剪百分比规整成 0–100 的整数，非法输入退回默认值。"""
	try:
		number = int(round(float(value)))
	except (TypeError, ValueError):
		return default
	return max(0, min(100, number))


def clampTarget(value, default: int = WHOLE_SCREEN) -> int:
	"""把目标类型规整成 0–3 的整数。"""
	try:
		number = int(value)
	except (TypeError, ValueError):
		return default
	return number if number in TARGETS else default


def clampDedup(value, default: str = "classic") -> str:
	"""把去重策略名规整成合法值；未知值退回 classic（即旧行为）。"""
	if isinstance(value, str):
		candidate = value.strip().lower()
		if candidate in DEDUP_MODES:
			return candidate
	return default


def clampHold(value, default: int = DEDUP_HOLD_DEFAULT) -> int:
	"""把稳定阈值规整成 500–10000 的整数毫秒。"""
	try:
		number = int(round(float(value)))
	except (TypeError, ValueError):
		return default
	return max(DEDUP_HOLD_MIN, min(DEDUP_HOLD_MAX, number))


def clampOptionalThreshold(value):
	"""把区域可选的经典相似度阈值规整成 0.0–1.0；None/非法/布尔 → None（跟随全局）。"""
	if value is None or isinstance(value, bool):
		return None
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	return max(0.0, min(1.0, number))


def resolveClassicThreshold(region, globalThreshold: float = 0.5) -> float:
	"""解析区域实际使用的经典相似度阈值。

	区域设置（0.0–1.0）优先；`None` 或非法值时跟随全局默认值；
	全局值也非法时退回 0.5（与 confspec 默认一致）。
	"""
	if isinstance(region, dict):
		override = clampOptionalThreshold(region.get("classicThreshold"))
		if override is not None:
			return override
	try:
		return max(0.0, min(1.0, float(globalThreshold)))
	except (TypeError, ValueError):
		return 0.5


def normalizeRegion(raw, index: int = 0) -> dict:
	"""把一个可能不完整的区域字典规整成合法区域。

	任何字段缺失或类型错误都不会抛异常——配置损坏时退回默认值，保证插件能启动。
	裁剪值会额外保证 `left + right <= 100`、`top + bottom <= 100`
	（超出时削 `right` / `bottom`），否则矩形会退化成 10×10 的补丁。
	"""
	raw = raw if isinstance(raw, dict) else {}
	region = dict(DEFAULT_REGION)
	try:
		region["id"] = int(raw.get("id", index + 1))
	except (TypeError, ValueError):
		region["id"] = index + 1
	if region["id"] < 1:
		region["id"] = index + 1
	name = raw.get("name")
	region["name"] = str(name).strip()[:NAME_LIMIT] if name is not None else ""
	enabled = raw.get("enabled", True)
	region["enabled"] = bool(enabled)
	region["target"] = clampTarget(raw.get("target", WHOLE_SCREEN))
	region["cropUp"] = clampPercent(raw.get("cropUp", 0))
	region["cropDown"] = clampPercent(raw.get("cropDown", 0))
	region["cropLeft"] = clampPercent(raw.get("cropLeft", 0))
	region["cropRight"] = clampPercent(raw.get("cropRight", 0))
	if region["cropLeft"] + region["cropRight"] > 100:
		region["cropRight"] = max(0, 100 - region["cropLeft"])
	if region["cropUp"] + region["cropDown"] > 100:
		region["cropDown"] = max(0, 100 - region["cropUp"])
	region["dedup"] = clampDedup(raw.get("dedup", "classic"))
	region["dedupHoldMs"] = clampHold(raw.get("dedupHoldMs", DEDUP_HOLD_DEFAULT))
	# 旧配置没有这个键：保持 None（跟随全局），行为与 3.3.7 完全一致
	region["classicThreshold"] = clampOptionalThreshold(raw.get("classicThreshold"))
	return region


def normalizeRegions(items) -> list:
	"""规整一个区域列表，并保证 `id` 唯一（重复时重新分配）。"""
	if not isinstance(items, (list, tuple)):
		return []
	regions = []
	used = set()
	for index, item in enumerate(items):
		region = normalizeRegion(item, index)
		if region["id"] in used:
			region["id"] = max(used) + 1 if used else index + 1
		used.add(region["id"])
		regions.append(region)
	return regions


def loadFromJson(text) -> list:
	"""容错解析区域列表 JSON。任何问题都退回"一个默认区域"。

	返回空列表只在输入是**合法的空数组**（`[]`）时发生——此时表示用户确实停用了
	所有区域，调用方应当什么都不做。
	"""
	if not text or not str(text).strip():
		return [normalizeRegion(DEFAULT_REGION)]
	try:
		parsed = json.loads(str(text))
	except ValueError:
		return [normalizeRegion(DEFAULT_REGION)]
	if not isinstance(parsed, list):
		return [normalizeRegion(DEFAULT_REGION)]
	if not parsed:
		return []
	if not any(isinstance(item, dict) for item in parsed):
		# 形如 [1,2,3] 的内容不是区域列表，视为配置损坏
		return [normalizeRegion(DEFAULT_REGION)]
	return normalizeRegions(parsed)


def toJson(regions) -> str:
	"""序列化区域列表（紧凑、保留中文）。"""
	return json.dumps(normalizeRegions(regions), ensure_ascii=False, separators=(",", ":"))


def defaultName(region: dict, index: int = 0) -> str:
	"""区域显示名：用户没起名时给出"区域 N"。"""
	name = (region.get("name") or "").strip()
	return name if name else _("Area %d") % (index + 1)


def clippedRect(base, crop, minSide: int = MIN_SIDE, screen=None) -> dict:
	"""按四向百分比裁剪基底矩形，返回 `{"left","top","width","height"}`。

	`base` 需要 `left/top/width/height`；`screen` 为 `(resX, resY)`，用于把结果夹在
	屏幕范围内（原版用 `resX - left` / `resY - top` 做上界）。
	"""
	resX, resY = screen if screen else (base["left"] + base["width"], base["top"] + base["height"])
	left = base["left"] + int(base["width"] * crop["cropLeft"] / 100.0)
	top = base["top"] + int(base["height"] * crop["cropUp"] / 100.0)
	width = base["width"] - int(base["width"] * (crop["cropLeft"] + crop["cropRight"]) / 100.0)
	height = base["height"] - int(base["height"] * (crop["cropUp"] + crop["cropDown"]) / 100.0)
	return {
		"left": max(base["left"], min(left, base["left"] + base["width"])),
		"top": max(base["top"], min(top, base["top"] + base["height"])),
		"width": max(minSide, min(width, resX - left)),
		"height": max(minSide, min(height, resY - top)),
	}


def unionRect(rects) -> dict | None:
	"""算出多个矩形的并集（最小外接矩形）；没有有效矩形时返回 None。

	"合并区域截图"用它把所有启用区域一次抓下来，再在本地按区域裁剪，
	从而把 N 次抓屏降成 1 次。注意：区域彼此相隔很远时并集面积会远大于
	各区域面积之和，单次全屏抓屏反而更慢，所以这个功能是可选项、默认关闭。
	"""
	boxes = []
	for rect in rects or []:
		try:
			width = int(rect["width"])
			height = int(rect["height"])
			left = int(rect["left"])
			top = int(rect["top"])
		except (KeyError, TypeError, ValueError):
			continue
		if width <= 0 or height <= 0:
			continue
		boxes.append((left, top, left + width, top + height))
	if not boxes:
		return None
	left = min(box[0] for box in boxes)
	top = min(box[1] for box in boxes)
	right = max(box[2] for box in boxes)
	bottom = max(box[3] for box in boxes)
	return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def regionRect(region: dict, resolveBase, screen=None) -> dict:
	"""算出区域的实际识别矩形。

	`resolveBase(target) -> rect|None`：由调用方（插件）解析导航对象/全屏/当前窗口/
	当前控件；返回 `None` 或宽高非正时退回整屏。`screen` 为 `(resX, resY)`。
	"""
	region = normalizeRegion(region)
	base = None
	try:
		base = resolveBase(region["target"])
	except Exception:  # noqa: BLE001 - 单个目标解析失败不应影响其它区域
		base = None
	resX, resY = screen if screen else (0, 0)
	if base is None or base.get("width", 0) <= 0 or base.get("height", 0) <= 0:
		base = {"left": 0, "top": 0, "width": resX, "height": resY}
	return clippedRect(base, region, screen=(resX, resY))


def migrateFromLegacy(conf) -> list:
	"""把旧版单区域配置（`target` + 四个裁剪键）迁移成一个区域。

	只要配置里出现过任一旧键就迁移（旧版本一定写过 `target`，但手工编辑过的
	配置文件可能只有裁剪键）；一个旧键都没有时直接返回默认区域。
	"""
	legacyKeys = (LEGACY_TARGET_KEY,) + LEGACY_CROP_KEYS
	hasLegacy = False
	for key in legacyKeys:
		try:
			if key in conf:
				hasLegacy = True
				break
		except TypeError:
			hasLegacy = False
			break
	raw = dict(DEFAULT_REGION)
	if hasLegacy:
		raw["target"] = conf.get(LEGACY_TARGET_KEY, WHOLE_SCREEN)
		for key in LEGACY_CROP_KEYS:
			raw[key] = conf.get(key, 0)
	region = normalizeRegion(raw, 0)
	region["name"] = defaultName(region, 0)
	return [region]


def pruneState(state: dict, regions) -> dict:
	"""丢掉已不存在区域的去重/报错状态，避免字典无限增长。"""
	alive = {region["id"] for region in regions}
	return {key: value for key, value in state.items() if key in alive}


def describe(region: dict, index: int = 0) -> str:
	"""列表里该区域的一行说明，例如 `字幕 | 全屏幕 | 上70 左0 右0 下0`。"""
	region = normalizeRegion(region, index)
	target = _(TARGET_LABELS[region["target"]])
	crop = _("top %(up)d left %(left)d right %(right)d bottom %(down)d") % {
		"up": region["cropUp"],
		"left": region["cropLeft"],
		"right": region["cropRight"],
		"down": region["cropDown"],
	}
	return "%s | %s | %s" % (defaultName(region, index), target, crop)
