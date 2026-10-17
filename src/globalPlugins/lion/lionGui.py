# SPDX-License-Identifier: GPL-2.0-or-later
# LION 修改版 (LION modified) settings panel for NVDA
# 本文件基于 LION 的 lionGui.py（作者 hwf1324 等）修改，
# 增加外部 PaddleOCR 服务与多选区识别相关设置，遵循 GNU GPL v2 或更高版本发布。
#
# Copyright (C) 2025-2026 LION contributors
# Copyright (C) 2026 xiaoshunxing — 外部服务接入、多选区识别、排队朗读

import addonHandler
import config
import gui
import ui
from gui import guiHelper
from gui import nvdaControls
import wx

addonHandler.initTranslation()

from . import regions as regionModel  # noqa: E402
from .ocrClient import (  # noqa: E402
	DEFAULT_MODEL,
	DEFAULT_MODEL_CHOICES,
	HttpOcrClient,
	normalizeServiceBase,
)

#: 与 __init__.py 保持一致
CONFIG_SECTION = "LION-modified"

#: "测试连接/获取模型列表"用的超时上限：避免在设置对话框里长时间阻塞 NVDA 主线程
TEST_CONNECTION_TIMEOUT = 2.5

#: 识别较慢的端点：设置面板里对它跳过"发小图识别"这一步（否则几乎必然超时）
SLOW_ENDPOINT_SUFFIX = "/layout-parsing"

#: 区域编辑对话框里裁剪百分比的取值范围
CROP_MIN = 0
CROP_MAX = 100

#: 去重策略在界面上的说明（顺序与 regionModel.DEDUP_MODES 一致）
DEDUP_LABELS = {
	"classic": _("Classic (similarity)"),
	"commit": _("Read once when stable"),
	"incremental": _("Read as it grows"),
	"off": _("Off (every change)"),
}


def loadPanelRegions() -> list:
	"""读取区域列表供面板使用；空配置时给一个默认区域，保证列表里始终能编辑。"""
	regions = regionModel.loadFromJson(config.conf[CONFIG_SECTION].get("regions", ""))
	if regions:
		return regions
	return [regionModel.normalizeRegion(regionModel.DEFAULT_REGION, 0)]


def nextRegionId(regions) -> int:
	"""给新区域分配一个没被占用的 id。"""
	used = {region["id"] for region in regions}
	candidate = max(used) + 1 if used else 1
	while candidate in used:
		candidate += 1
	return candidate


class RegionEditDialog(gui.settingsDialogs.SettingsDialog):
	"""添加/编辑一个识别区域。"""

	title = _("OCR region")

	def __init__(self, parent, region: dict, index: int = 0):
		self._region = regionModel.normalizeRegion(region, index)
		self._index = index
		super().__init__(parent, resizeable=False, buttons={wx.OK, wx.CANCEL})

	def makeSettings(self, settingsSizer: wx.BoxSizer):
		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		self.nameEdit = helper.addLabeledControl(_("&Name:"), wx.TextCtrl)
		self.nameEdit.SetValue(self._region["name"] or regionModel.defaultName(self._region, self._index))

		self.targetList = helper.addLabeledControl(
			_("&Target:"),
			wx.Choice,
			choices=[_(regionModel.TARGET_LABELS[target]) for target in regionModel.TARGETS],
		)
		self.targetList.SetSelection(regionModel.TARGETS.index(self._region["target"]))

		#: (键, 控件) 顺序与 TARGETS 之外的裁剪键一致
		self.cropEdits = {}
		for key, label in (
			("cropUp", _("Crop &above (%):")),
			("cropDown", _("Crop &below (%):")),
			("cropLeft", _("Crop &left (%):")),
			("cropRight", _("Crop &right (%):")),
		):
			self.cropEdits[key] = helper.addLabeledControl(
				label,
				nvdaControls.SelectOnFocusSpinCtrl,
				min=CROP_MIN,
				max=CROP_MAX,
				initial=int(self._region[key]),
			)

		# ------------------------------------------------------ 去重策略
		self.dedupList = helper.addLabeledControl(
			_("De&duplication:"),
			wx.Choice,
			choices=[DEDUP_LABELS[mode] for mode in regionModel.DEDUP_MODES],
		)
		self.dedupList.SetSelection(regionModel.DEDUP_MODES.index(self._region["dedup"]))

		self.dedupHoldEdit = helper.addLabeledControl(
			_("Stable after (ms):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=regionModel.DEDUP_HOLD_MIN,
			max=regionModel.DEDUP_HOLD_MAX,
			initial=int(self._region["dedupHoldMs"]),
		)

	def onOk(self, evt: wx.CommandEvent):
		# 先校验：左右/上下之和不能超过 100，否则矩形会退化
		crops = {key: control.GetValue() for key, control in self.cropEdits.items()}
		problems = []
		if crops["cropLeft"] + crops["cropRight"] > 100:
			problems.append(_("Left and right crop cannot add up to more than 100%."))
		if crops["cropUp"] + crops["cropDown"] > 100:
			problems.append(_("Top and bottom crop cannot add up to more than 100%."))
		if problems:
			gui.messageBox(
				"\n".join(problems),
				_("Invalid crop values"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			return
		super().onOk(evt)

	def getRegion(self) -> dict:
		"""把界面上的值收集成一个区域字典。"""
		region = dict(self._region)
		region["name"] = self.nameEdit.GetValue().strip()
		region["target"] = regionModel.TARGETS[self.targetList.GetSelection()]
		for key, control in self.cropEdits.items():
			region[key] = control.GetValue()
		region["dedup"] = regionModel.DEDUP_MODES[self.dedupList.GetSelection()]
		region["dedupHoldMs"] = self.dedupHoldEdit.GetValue()
		return regionModel.normalizeRegion(region, self._index)


class LIONSettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = _("LION modified")
	panelDescription = _("External OCR service and OCR regions")

	def makeSettings(self, settingsSizer: wx.BoxSizer):
		settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.regions = loadPanelRegions()
		self.currentRegion = 0

		# ------------------------------------------------ 外部 OCR 服务
		self.serviceUrlEdit = settingsSizerHelper.addLabeledControl(
			_("OCR &service URL:"),
			wx.TextCtrl,
		)
		self.serviceUrlEdit.SetValue(config.conf[CONFIG_SECTION]["serviceUrl"])

		# 模型/OCR 端点：可编辑组合框。候选项在"测试/获取模型列表"后按服务端实际返回刷新。
		self.modelCombo = settingsSizerHelper.addLabeledControl(
			_("&Model / endpoint:"),
			wx.ComboBox,
			style=wx.CB_DROPDOWN,
			choices=list(DEFAULT_MODEL_CHOICES),
		)
		self.modelCombo.SetValue(config.conf[CONFIG_SECTION]["model"] or DEFAULT_MODEL)
		#: 组合框显示值 -> 真正的请求端点（探测到模型列表后刷新）
		self.modelEndpoints = {choice: choice for choice in DEFAULT_MODEL_CHOICES}

		self.serviceTokenEdit = settingsSizerHelper.addLabeledControl(
			_("Service &token:"),
			wx.TextCtrl,
			style=wx.TE_PASSWORD,
		)
		self.serviceTokenEdit.SetValue(config.conf[CONFIG_SECTION]["serviceToken"])

		self.testButton = settingsSizerHelper.addItem(
			wx.Button(self, label=_("&Test / get models")),
		)
		self.testButton.Bind(wx.EVT_BUTTON, self.onTestAndDiscover)
		self.testResultText = settingsSizerHelper.addItem(wx.StaticText(self, label=""))

		valTimeout = config.conf.getConfigValidation((CONFIG_SECTION, "requestTimeout"))
		self.requestTimeoutEdit = settingsSizerHelper.addLabeledControl(
			_("Request &timeout (ms):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=int(float(valTimeout.kwargs["min"]) * 1000),
			max=int(float(valTimeout.kwargs["max"]) * 1000),
			initial=int(float(config.conf[CONFIG_SECTION]["requestTimeout"]) * 1000),
		)

		self.imageFormatList = settingsSizerHelper.addLabeledControl(
			_("Image &format:"),
			wx.Choice,
			choices=[_("PNG (larger, lossless)"), _("JPEG (smaller, faster)")],
		)
		self.imageFormatList.SetSelection(int(config.conf[CONFIG_SECTION]["imageFormat"]))

		self.visualizeCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Return &visualisation image (slow)")),
		)
		self.visualizeCheckBox.SetValue(bool(config.conf[CONFIG_SECTION]["visualize"]))

		valScore = config.conf.getConfigValidation((CONFIG_SECTION, "scoreThresh"))
		self.scoreThreshEdit = settingsSizerHelper.addLabeledControl(
			_("Minimum recognition &score (%):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=int(float(valScore.kwargs["min"]) * 100),
			max=int(float(valScore.kwargs["max"]) * 100),
			initial=int(float(config.conf[CONFIG_SECTION]["scoreThresh"]) * 100),
		)

		valDetLimit = config.conf.getConfigValidation((CONFIG_SECTION, "detLimitSideLen"))
		self.detLimitEdit = settingsSizerHelper.addLabeledControl(
			_("Detection longest side (0 = default):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=int(valDetLimit.kwargs["min"]),
			max=int(valDetLimit.kwargs["max"]),
			initial=int(config.conf[CONFIG_SECTION]["detLimitSideLen"]),
		)

		self.notifyOnErrorCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("Announce &errors once")),
		)
		self.notifyOnErrorCheckBox.SetValue(bool(config.conf[CONFIG_SECTION]["notifyOnError"]))

		# ------------------------------------------------ 识别区域（多选区）
		settingsSizerHelper.addItem(
			wx.StaticText(self, label=_("OCR regions:")),
		)
		self.regionList = settingsSizerHelper.addItem(
			nvdaControls.AutoWidthColumnCheckListCtrl(
				self,
				style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
				autoSizeColumn="LAST",
				size=(-1, 150),
			),
		)
		self.regionList.InsertColumn(0, _("Region"))
		self.regionList.InsertColumn(1, _("Target"))
		self.regionList.InsertColumn(2, _("Crop"))
		self.regionList.Bind(wx.EVT_CHECKLISTBOX, self.onRegionChecked)
		self.regionList.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onRegionActivated)

		buttonRow = guiHelper.ButtonHelper(wx.HORIZONTAL)
		self.enableAllButton = buttonRow.addButton(self, label=_("Enable &all"))
		self.disableAllButton = buttonRow.addButton(self, label=_("Disable a&ll"))
		self.addRegionButton = buttonRow.addButton(self, label=_("&Add..."))
		self.editRegionButton = buttonRow.addButton(self, label=_("&Edit..."))
		self.removeRegionButton = buttonRow.addButton(self, label=_("&Remove"))
		self.moveUpButton = buttonRow.addButton(self, label=_("Move &up"))
		self.moveDownButton = buttonRow.addButton(self, label=_("Move &down"))
		settingsSizerHelper.addItem(buttonRow.sizer)
		self.enableAllButton.Bind(wx.EVT_BUTTON, self.onEnableAll)
		self.disableAllButton.Bind(wx.EVT_BUTTON, self.onDisableAll)
		self.addRegionButton.Bind(wx.EVT_BUTTON, self.onAddRegion)
		self.editRegionButton.Bind(wx.EVT_BUTTON, self.onEditRegion)
		self.removeRegionButton.Bind(wx.EVT_BUTTON, self.onRemoveRegion)
		self.moveUpButton.Bind(wx.EVT_BUTTON, self.onMoveUp)
		self.moveDownButton.Bind(wx.EVT_BUTTON, self.onMoveDown)

		self.queueSpeechCheckBox = settingsSizerHelper.addItem(
			wx.CheckBox(self, label=_("&Queue speech")),
		)
		self.queueSpeechCheckBox.SetValue(bool(config.conf[CONFIG_SECTION]["queueSpeech"]))

		# ------------------------------------------------ 识别参数
		valThreshold = config.conf.getConfigValidation((CONFIG_SECTION, "threshold"))
		self.similarityThresholdEdit = settingsSizerHelper.addLabeledControl(
			_("&Text similarity threshold (%):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=int(float(valThreshold.kwargs["min"]) * 100),
			max=int(float(valThreshold.kwargs["max"]) * 100),
			initial=int(config.conf[CONFIG_SECTION]["threshold"] * 100),
		)

		valInterval = config.conf.getConfigValidation((CONFIG_SECTION, "interval"))
		self.intervalEdit = settingsSizerHelper.addLabeledControl(
			_("OCR &interval (ms):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=int(float(valInterval.kwargs["min"]) * 1000),
			max=int(float(valInterval.kwargs["max"]) * 1000),
			initial=int(config.conf[CONFIG_SECTION]["interval"] * 1000),
		)

		self.regexFiltersEdit = settingsSizerHelper.addLabeledControl(
			_("Regular expression filters (one per &line):"),
			wx.TextCtrl,
			style=wx.TE_MULTILINE,
			size=(-1, 100),
		)
		self.regexFiltersEdit.SetValue(config.conf[CONFIG_SECTION]["regexFilters"])

		# 核心：拦截按键钩子，防止回车关闭对话框
		self.regexFiltersEdit.Bind(wx.EVT_CHAR_HOOK, self.onRegexKeyHook)

		self.refreshRegionList()

	# ------------------------------------------------------------ 区域列表

	def refreshRegionList(self, select: int | None = None):
		"""按 `self.regions` 重建列表内容与勾选状态。"""
		self.regionList.DeleteAllItems()
		for index, region in enumerate(self.regions):
			self.regionList.InsertItem(index, regionModel.defaultName(region, index))
			self.regionList.SetItem(index, 1, _(regionModel.TARGET_LABELS[region["target"]]))
			self.regionList.SetItem(
				index,
				2,
				_("top %(up)d left %(left)d right %(right)d bottom %(down)d") % {
					"up": region["cropUp"],
					"left": region["cropLeft"],
					"right": region["cropRight"],
					"down": region["cropDown"],
				},
			)
			self.regionList.CheckItem(index, bool(region["enabled"]))
		if self.regions:
			target = 0 if select is None else max(0, min(select, len(self.regions) - 1))
			self.currentRegion = target
			self.regionList.Select(target)
			self.regionList.Focus(target)
		else:
			self.currentRegion = -1

	def selectedRegionIndex(self) -> int:
		"""当前选中的区域下标；没有有效选择时返回 -1。"""
		index = self.regionList.GetFirstSelected()
		if index == -1:
			return -1
		return index if 0 <= index < len(self.regions) else -1

	def onRegionChecked(self, evt):
		index = getattr(evt, "GetIndex", lambda: None)()
		if index is not None and 0 <= index < len(self.regions):
			# 勾选框状态由控件维护，这里同步到数据模型
			self.regions[index]["enabled"] = bool(self.regionList.IsChecked(index))
			self.currentRegion = index

	def onRegionActivated(self, evt):
		index = getattr(evt, "GetIndex", lambda: None)()
		if index is not None and 0 <= index < len(self.regions):
			self.editRegion(index)

	def onEnableAll(self, evt):
		for region in self.regions:
			region["enabled"] = True
		self.refreshRegionList()

	def onDisableAll(self, evt):
		for region in self.regions:
			region["enabled"] = False
		self.refreshRegionList()

	def onAddRegion(self, evt):
		region = regionModel.normalizeRegion(
			dict(regionModel.DEFAULT_REGION, id=nextRegionId(self.regions)),
			len(self.regions),
		)
		dialog = RegionEditDialog(self, region, len(self.regions))
		if dialog.ShowModal() == wx.ID_OK:
			self.regions.append(dialog.getRegion())
			self.refreshRegionList(len(self.regions) - 1)
		dialog.Destroy()

	def onEditRegion(self, evt):
		self.editRegion(self.selectedRegionIndex())

	def editRegion(self, index: int):
		if index < 0 or index >= len(self.regions):
			ui.message(_("Select a region first."))
			return
		dialog = RegionEditDialog(self, self.regions[index], index)
		if dialog.ShowModal() == wx.ID_OK:
			self.regions[index] = dialog.getRegion()
			self.refreshRegionList(index)
		dialog.Destroy()

	def onRemoveRegion(self, evt):
		index = self.selectedRegionIndex()
		if index < 0:
			ui.message(_("Select a region first."))
			return
		del self.regions[index]
		self.refreshRegionList(min(index, len(self.regions) - 1))

	def onMoveUp(self, evt):
		self.moveRegion(-1)

	def onMoveDown(self, evt):
		self.moveRegion(1)

	def moveRegion(self, delta: int):
		index = self.selectedRegionIndex()
		if index < 0:
			ui.message(_("Select a region first."))
			return
		target = index + delta
		if target < 0 or target >= len(self.regions):
			return
		self.regions[index], self.regions[target] = self.regions[target], self.regions[index]
		self.refreshRegionList(target)

	def getRegions(self) -> list:
		"""保存前把列表里的勾选状态同步进数据模型。"""
		for index, region in enumerate(self.regions):
			region["enabled"] = bool(self.regionList.IsChecked(index))
		return regionModel.normalizeRegions(self.regions)

	# ------------------------------------------- 测试连接 / 获取模型列表

	def currentServiceBase(self) -> str:
		"""读取界面上的服务基址并规范化（未保存时也能用于测试）。"""
		return normalizeServiceBase(self.serviceUrlEdit.GetValue())

	def currentModel(self) -> str:
		"""组合框里的显示值（探测到模型列表后是模型 id）。"""
		return self.modelCombo.GetValue().strip()

	def modelTarget(self, value: str) -> str:
		"""把组合框显示值映射成真正的请求端点；未命中映射时原样返回。"""
		value = (value or "").strip()
		return self.modelEndpoints.get(value, value)

	def setModelChoices(self, models) -> None:
		"""用探测结果刷新组合框候选，同时保留用户当前输入。"""
		current = self.modelCombo.GetValue()
		mapping = {}
		choices = []
		for model in models or []:
			display = (model.get("id") or model.get("endpoint") or "").strip()
			endpoint = (model.get("endpoint") or display).strip()
			if not display or display in mapping:
				continue
			choices.append(display)
			mapping[display] = endpoint
		# 已知的默认端点始终保留，方便用户手选
		for fallback in DEFAULT_MODEL_CHOICES:
			if fallback not in mapping:
				choices.append(fallback)
				mapping[fallback] = fallback
		if not choices:
			return
		self.modelEndpoints = mapping
		self.modelCombo.Clear()
		for choice in choices:
			self.modelCombo.Append(choice)
		if current:
			self.modelCombo.SetValue(current)
		else:
			self.modelCombo.SetValue(choices[0])

	def buildPanelClient(self, timeout: float) -> HttpOcrClient:
		"""按界面上的当前值构造一个探测用客户端。"""
		return HttpOcrClient(
			self.serviceUrlEdit.GetValue().strip(),
			model=self.modelTarget(self.currentModel()),
			token=self.serviceTokenEdit.GetValue().strip(),
			timeout=timeout,
		)

	def onTestAndDiscover(self, event):
		"""一个按钮完成：探活 → 获取模型列表 → 校验令牌 → 快端点识别测试。

		模型列表按"通行契约优先"探测（`/models`、`/v1/models`），都没有时回退到
		`/openapi.json` 的端点枚举，并在结果里说明来源。PaddleOCR-VL 单次推理较慢，
		对它跳过"发小图识别"这一步，避免误报超时。
		"""
		base = self.currentServiceBase()
		model = self.modelTarget(self.currentModel())
		if not base and not model.startswith(("http://", "https://")):
			self.setTestResult(_("Invalid service address."), ok=False)
			return
		configuredSeconds = max(1.0, float(self.requestTimeoutEdit.GetValue()) / 1000.0)
		timeout = min(configuredSeconds, TEST_CONNECTION_TIMEOUT)
		client = self.buildPanelClient(timeout)
		if not client.base_url:
			self.setTestResult(_("Invalid service address."), ok=False)
			return
		self.setTestResult(_("Testing..."), ok=None)
		parts = []
		models, source, error = client.listModels(timeout=TEST_CONNECTION_TIMEOUT)
		if models:
			self.setModelChoices(models)
			parts.append(_("Found %d models.") % len(models))
			if source == "openapi":
				parts.append(
					_("Source: OpenAPI endpoints")
				)
			else:
				parts.append(_("Source: %s.") % source)
		else:
			connected, detail = client.testConnection(timeout=TEST_CONNECTION_TIMEOUT)
			if not connected:
				self.setTestResult(detail, ok=False)
				return
			parts.append(
				_("Connected; model list unavailable: %s") % (error or detail)
			)
		# 令牌校验：缺 file 的请求体不会触发推理，对 PaddleOCR-VL 也是毫秒级
		tokenOk, tokenDetail = client.verifyToken(timeout=TEST_CONNECTION_TIMEOUT)
		parts.append(tokenDetail)
		if not tokenOk:
			self.setTestResult(_("; ").join(parts), ok=False)
			return
		# 识别测试：PaddleOCR-VL 较慢，跳过并说明
		if client.base_url.rstrip("/").endswith(SLOW_ENDPOINT_SUFFIX):
			parts.append(_("Recognition test skipped (PaddleOCR-VL is slow)."))
		else:
			probe = self.buildPanelClient(timeout)
			ok, detail = probe.testConnection(sendRequest=True)
			parts.append(detail)
			if not ok:
				self.setTestResult(_("; ").join(parts), ok=False)
				return
		self.setTestResult(_("; ").join(parts), ok=True)

	def setTestResult(self, text: str, ok):
		"""把结果同时写到面板与语音输出，方便屏幕阅读器用户。"""
		self.testResultText.SetLabel(text)
		self.testResultText.GetParent().Layout()
		ui.message(text)

	def onRegexKeyHook(self, event):
		"""处理多行文本框的回车键，确保其换行而不是关闭对话框"""
		keycode = event.GetKeyCode()
		if keycode in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			# 手动插入换行符
			self.regexFiltersEdit.WriteText("\n")
			# 不执行 event.Skip()，从而拦截该事件，不让对话框捕获到回车
			return
		# 其他按键正常放行
		event.Skip()

	# ---------------------------------------------------------------- 保存

	def onSave(self):
		conf = config.conf[CONFIG_SECTION]
		base = normalizeServiceBase(self.serviceUrlEdit.GetValue())
		# 基址写回规范化结果；非法地址保留用户原样输入，便于发现笔误
		conf["serviceUrl"] = base or self.serviceUrlEdit.GetValue().strip()
		# 组合框显示值可能是模型 id，实际保存"请求端点"，配置语义始终稳定
		conf["model"] = self.modelTarget(self.currentModel()) or DEFAULT_MODEL
		conf["serviceToken"] = self.serviceTokenEdit.GetValue().strip()
		conf["requestTimeout"] = float(self.requestTimeoutEdit.GetValue() / 1000)
		conf["imageFormat"] = int(self.imageFormatList.GetSelection())
		conf["visualize"] = bool(self.visualizeCheckBox.GetValue())
		conf["scoreThresh"] = float(self.scoreThreshEdit.GetValue() / 100)
		conf["detLimitSideLen"] = int(self.detLimitEdit.GetValue())
		conf["notifyOnError"] = bool(self.notifyOnErrorCheckBox.GetValue())
		conf["regions"] = regionModel.toJson(self.getRegions())
		conf["queueSpeech"] = bool(self.queueSpeechCheckBox.GetValue())
		conf["interval"] = float(self.intervalEdit.GetValue() / 1000)
		conf["threshold"] = float(self.similarityThresholdEdit.GetValue() / 100)
		conf["regexFilters"] = self.regexFiltersEdit.GetValue()
