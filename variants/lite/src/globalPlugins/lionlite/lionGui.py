# SPDX-License-Identifier: GPL-2.0-or-later
# LION 修改版 lite（LION modified lite）settings panel for NVDA
# 本文件基于 LION 的 lionGui.py（作者 hwf1324 等）修改，
# 以单区域、原版式界面为基线，额外加入外部 PaddleOCR 服务与模型/端点设置。
# 遵循 GNU GPL v2 或更高版本发布（见 COPYING.txt）。
#
# Copyright (C) 2025-2026 LION contributors
# Copyright (C) 2026 xiaoshunxing — 外部服务接入与简洁版

import addonHandler
import config
import gui
import ui
from gui import guiHelper
from gui import nvdaControls
import wx

addonHandler.initTranslation()

from .ocrClient import (  # noqa: E402
	DEFAULT_MODEL,
	DEFAULT_MODEL_CHOICES,
	HttpOcrClient,
	normalizeServiceBase,
)

#: 与 __init__.py 保持一致
CONFIG_SECTION = "LION-modified-lite"

#: "测试/获取模型"用的超时上限：避免在设置对话框里长时间阻塞 NVDA 主线程
TEST_CONNECTION_TIMEOUT = 2.5

#: 识别较慢的端点：跳过"发小图识别"这一步，避免误报超时
SLOW_ENDPOINT_SUFFIX = "/layout-parsing"


class LIONSettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = _("LION modified lite")
	panelDescription = _("External OCR service and OCR area")

	def makeSettings(self, settingsSizer: wx.BoxSizer):
		settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		# ------------------------------------------------ 外部 OCR 服务
		self.serviceUrlEdit = settingsSizerHelper.addLabeledControl(
			_("OCR &service URL:"),
			wx.TextCtrl,
		)
		self.serviceUrlEdit.SetValue(config.conf[CONFIG_SECTION]["serviceUrl"])

		self.modelCombo = settingsSizerHelper.addLabeledControl(
			_("&Model / endpoint:"),
			wx.ComboBox,
			style=wx.CB_DROPDOWN,
			choices=list(DEFAULT_MODEL_CHOICES),
		)
		self.modelCombo.SetValue(config.conf[CONFIG_SECTION]["model"] or DEFAULT_MODEL)
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
			_("Minimum &score (%):"),
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

		# ------------------------------------------------ 识别区域与间隔
		valInterval = config.conf.getConfigValidation((CONFIG_SECTION, "interval"))
		intervalMin = int(float(valInterval.kwargs["min"]) * 1000)
		intervalMax = int(float(valInterval.kwargs["max"]) * 1000)
		self.intervalEdit = settingsSizerHelper.addLabeledControl(
			_("OCR &interval (ms):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=intervalMin,
			max=intervalMax,
			initial=int(config.conf[CONFIG_SECTION]["interval"] * 1000),
		)

		self.targetList = settingsSizerHelper.addLabeledControl(
			_("&OCR target:"),
			wx.Choice,
			choices=[
				_("Navigator object"),
				_("Whole Screen"),
				_("current window"),
				_("current control"),
			],
		)
		self.targetList.SetSelection(config.conf[CONFIG_SECTION]["target"])

		valThreshold = config.conf.getConfigValidation((CONFIG_SECTION, "threshold"))
		thresholdMin = int(float(valThreshold.kwargs["min"]) * 100)
		thresholdMax = int(float(valThreshold.kwargs["max"]) * 100)
		self.similarityThresholdEdit = settingsSizerHelper.addLabeledControl(
			_("&Text similarity threshold (%):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=thresholdMin,
			max=thresholdMax,
			initial=int(config.conf[CONFIG_SECTION]["threshold"] * 100),
		)

		self.cropUpEdit = settingsSizerHelper.addLabeledControl(
			_("Crop &above (%):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=0,
			max=100,
			initial=config.conf[CONFIG_SECTION]["cropUp"],
		)
		self.cropDownEdit = settingsSizerHelper.addLabeledControl(
			_("Crop &below (%):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=0,
			max=100,
			initial=config.conf[CONFIG_SECTION]["cropDown"],
		)
		self.cropLeftEdit = settingsSizerHelper.addLabeledControl(
			_("Crop &left (%):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=0,
			max=100,
			initial=config.conf[CONFIG_SECTION]["cropLeft"],
		)
		self.cropRightEdit = settingsSizerHelper.addLabeledControl(
			_("Crop &right (%):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=0,
			max=100,
			initial=config.conf[CONFIG_SECTION]["cropRight"],
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

	# ------------------------------------------- 测试连接 / 获取模型列表

	def currentServiceBase(self) -> str:
		"""读取界面上的服务基址并规范化（未保存时也能用于测试）。"""
		return normalizeServiceBase(self.serviceUrlEdit.GetValue())

	def currentModel(self) -> str:
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
		self.modelCombo.SetValue(current or choices[0])

	def buildPanelClient(self, timeout: float) -> HttpOcrClient:
		return HttpOcrClient(
			self.serviceUrlEdit.GetValue().strip(),
			model=self.modelTarget(self.currentModel()),
			token=self.serviceTokenEdit.GetValue().strip(),
			timeout=timeout,
		)

	def onTestAndDiscover(self, event):
		"""探活 → 获取模型列表 → 校验令牌 → 快端点识别测试（VL 跳过）。"""
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
				parts.append(_("Source: OpenAPI endpoints"))
			else:
				parts.append(_("Source: %s.") % source)
		else:
			connected, detail = client.testConnection(timeout=TEST_CONNECTION_TIMEOUT)
			if not connected:
				self.setTestResult(detail, ok=False)
				return
			parts.append(_("Connected; model list unavailable: %s") % (error or detail))
		tokenOk, tokenDetail = client.verifyToken(timeout=TEST_CONNECTION_TIMEOUT)
		parts.append(tokenDetail)
		if not tokenOk:
			self.setTestResult(_("; ").join(parts), ok=False)
			return
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

	def onSave(self):
		conf = config.conf[CONFIG_SECTION]
		base = normalizeServiceBase(self.serviceUrlEdit.GetValue())
		conf["serviceUrl"] = base or self.serviceUrlEdit.GetValue().strip()
		conf["model"] = self.modelTarget(self.currentModel()) or DEFAULT_MODEL
		conf["serviceToken"] = self.serviceTokenEdit.GetValue().strip()
		conf["requestTimeout"] = float(self.requestTimeoutEdit.GetValue() / 1000)
		conf["imageFormat"] = int(self.imageFormatList.GetSelection())
		conf["visualize"] = bool(self.visualizeCheckBox.GetValue())
		conf["scoreThresh"] = float(self.scoreThreshEdit.GetValue() / 100)
		conf["detLimitSideLen"] = int(self.detLimitEdit.GetValue())
		conf["notifyOnError"] = bool(self.notifyOnErrorCheckBox.GetValue())
		conf["interval"] = float(self.intervalEdit.GetValue() / 1000)
		conf["target"] = self.targetList.GetSelection()
		conf["threshold"] = float(self.similarityThresholdEdit.GetValue() / 100)
		conf["cropUp"] = self.cropUpEdit.GetValue()
		conf["cropLeft"] = self.cropLeftEdit.GetValue()
		conf["cropDown"] = self.cropDownEdit.GetValue()
		conf["cropRight"] = self.cropRightEdit.GetValue()
		conf["regexFilters"] = self.regexFiltersEdit.GetValue()
