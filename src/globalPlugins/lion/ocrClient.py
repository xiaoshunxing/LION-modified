# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""外部 PaddleOCR HTTP 服务的客户端。

本模块把 PaddleOCR 本地服务的 REST 接口封装成与原 PaddleOCR-json 管道 API
等价的返回契约，使 LION 主循环可以无感替换后端：::

	{"code": 100, "data": [{"text": "...", "score": 0.99}, ...]}   # 成功
	{"code": 90x, "data": "失败原因"}                              # 失败

只依赖 Python 标准库（urllib / json / base64），不引入 requests，避免依赖
NVDA 自身打包库的版本差异。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

import addonHandler

addonHandler.initTranslation()

log = logging.getLogger(__name__)

#: 服务地址里若没有路径时，默认补上的端点。
DEFAULT_ENDPOINT = "/ocr"

#: 新配置项 ``model`` 的默认值（请求目标：端点路径或完整 URL）。
DEFAULT_MODEL = "/ocr"

#: 除 `/ocr` 外也认可的端点名，用于把裸主机地址补全成具体端点。
KNOWN_ENDPOINTS = ("/ocr", "/ocr-acc", "/layout-parsing")

#: 设置面板里默认给出的模型候选（用户仍可手动输入其它值）。
DEFAULT_MODEL_CHOICES = KNOWN_ENDPOINTS

#: 惯用的"模型列表"接口，按顺序探测（OpenAI 及其兼容实现）。
MODEL_LIST_PATHS = ("/models", "/v1/models")

#: 不是 OCR 推理的 POST 端点，从 OpenAPI 兜底枚举里排除。
NON_OCR_ENDPOINTS = ("/restructure-pages",)

#: PaddleOCR-VL 版面结果里视为"非文本"的块类型（不朗读）。
NON_TEXT_BLOCK_LABELS = frozenset(("figure", "image", "chart", "seal"))

#: 看起来是标记语言/图片链接的内容，不当作正文朗读。
MARKUP_PREFIXES = ("<table", "<html", "<img", "![")

#: HTTP 状态码 -> 返回给主循环的错误码
HTTP_STATUS_TO_CODE = {401: 904, 403: 904, 404: 904, 429: 904, 500: 904}

IMAGE_FORMATS = ("PNG", "JPEG")


def readImageFormat(value) -> str:
	"""把配置里的整数值翻译成 PIL 的格式名（0=PNG, 1=JPEG）。"""
	try:
		index = int(value)
	except (TypeError, ValueError):
		return "JPEG"
	if index < 0 or index >= len(IMAGE_FORMATS):
		return "JPEG"
	return IMAGE_FORMATS[index]


def imageFormatIndex(name: str) -> int:
	"""格式名 -> 配置整数，未知值回退到 JPEG。"""
	try:
		return IMAGE_FORMATS.index(name)
	except ValueError:
		return IMAGE_FORMATS.index("JPEG")


def normalizeBaseUrl(raw: str) -> str:
	"""规范化服务地址。

	接受下列写法，并统一成 `scheme://netloc/path`（无尾斜杠）：

	* ``http://127.0.0.1:23353``            -> 补默认端点 `/ocr`
	* ``http://127.0.0.1:23353/``           -> 补默认端点 `/ocr`
	* ``http://127.0.0.1:23353/ocr``        -> 原样
	* ``http://127.0.0.1:23353/ocr/``       -> 去掉尾斜杠
	* ``127.0.0.1:23353``                   -> 补 `http://` 与端点

	非法输入（空串、无法解析）返回空串，由调用方给出 901。
	"""
	if not isinstance(raw, str):
		return ""
	text = raw.strip()
	if not text:
		return ""
	if "://" not in text:
		# 允许省略协议：127.0.0.1:23353/ocr
		text = "http://" + text
	parts = urllib.parse.urlsplit(text)
	if parts.scheme not in ("http", "https") or not parts.netloc:
		return ""
	path = parts.path or ""
	if path in ("", "/"):
		path = DEFAULT_ENDPOINT
	path = path.rstrip("/")
	if path not in KNOWN_ENDPOINTS:
		log.debug("LION: 服务地址路径 %s 不在已知端点列表内，将按原样请求", path)
	return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def healthUrl(base_url: str) -> str:
	"""由端点地址推出 `/health` 地址；地址非法时返回空串。"""
	parts = urllib.parse.urlsplit(base_url or "")
	if parts.scheme not in ("http", "https") or not parts.netloc:
		return ""
	return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


def openapiUrl(base_url: str) -> str:
	"""由端点地址推出 `/openapi.json` 地址；地址非法时返回空串。"""
	parts = urllib.parse.urlsplit(base_url or "")
	if parts.scheme not in ("http", "https") or not parts.netloc:
		return ""
	return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/openapi.json", "", ""))


def normalizeServiceBase(raw) -> str:
	"""规范化**服务基址**（3.3.6 起 `serviceUrl` 的新语义）。

	与 `normalizeBaseUrl` 的区别：**保留路径前缀**（反向代理可能挂在 `/paddle` 下），
	且不自动补端点。例如::

		http://127.0.0.1:23353        -> http://127.0.0.1:23353
		http://127.0.0.1:23353/       -> http://127.0.0.1:23353
		http://host/paddle/ocr/       -> http://host/paddle/ocr（路径保留，只去尾斜杠）
		127.0.0.1:23353               -> http://127.0.0.1:23353

	非法输入（空串、无法解析）返回空串。
	"""
	if not isinstance(raw, str):
		return ""
	text = raw.strip()
	if not text:
		return ""
	if "://" not in text:
		text = "http://" + text
	parts = urllib.parse.urlsplit(text)
	if parts.scheme not in ("http", "https") or not parts.netloc:
		return ""
	path = (parts.path or "").rstrip("/")
	return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def normalizeModel(raw) -> str:
	"""规范化"模型/端点"设置值：去空白；空串表示沿用旧行为。"""
	if not isinstance(raw, str):
		return ""
	return raw.strip()


def composeUrl(base, model="") -> str:
	"""把"服务基址 + 模型/端点"组合成真正的请求地址。

	* ``model`` 是完整 URL（``http(s)://…``）→ 直接使用（可指向另一台机器的服务，
	  此时基址可以为空）；
	* ``model`` 非空 → ``基址 + "/" + model``；
	* ``model`` 为空 → 退回 3.3.5 的行为（无路径时自动补 `/ocr`）。
	"""
	model = normalizeModel(model)
	if model.startswith(("http://", "https://")):
		return normalizeBaseUrl(model)
	base = normalizeServiceBase(base)
	if not base:
		return ""
	if not model:
		return normalizeBaseUrl(base)
	return base + "/" + model.strip("/")


def splitServiceUrl(raw):
	"""把 3.3.5 的完整服务地址拆成 ``(基址, 模型/端点)``，供首次启动迁移使用。

	保证"拆完再组合回来"与旧行为等价：

	* ``http://h:23353``            -> ``(http://h:23353, /ocr)``
	* ``http://h:23353/ocr``        -> ``(http://h:23353, /ocr)``
	* ``http://h/paddle/ocr``       -> ``(http://h/paddle, /ocr)``
	* 非法地址                     -> ``("", /ocr)``
	"""
	base = normalizeServiceBase(raw)
	if not base:
		return "", DEFAULT_MODEL
	parts = urllib.parse.urlsplit(base)
	origin = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))
	path = parts.path or ""
	if not path:
		return origin, DEFAULT_MODEL
	if path in KNOWN_ENDPOINTS:
		return origin, path
	head, _, tail = path.rpartition("/")
	if not tail:
		return origin, DEFAULT_MODEL
	if head:
		return urllib.parse.urlunsplit((parts.scheme, parts.netloc, head, "", "")), "/" + tail
	return origin, "/" + tail


def parseModelList(body, source: str = "") -> list:
	"""解析"模型列表"响应，返回 ``[{"id","label","endpoint"}]``。

	宽容支持两种常见形状：

	* OpenAI 兼容：``{"object":"list","data":[{"id":…}]}``
	* Ollama 风格：``{"models":[{"name":…}]}``

	条目里可用 ``endpoint`` / ``path`` / ``url`` 指定真正的推理端点；没有该字段时，
	只有 ``id`` 本身以 ``/`` 开头才当作端点。**没有可路由端点的条目会被丢弃**，
	因为本插件最终必须知道往哪里 POST。
	"""
	if not isinstance(body, dict):
		return []
	entries = body.get("data")
	if not isinstance(entries, list):
		entries = body.get("models")
	if not isinstance(entries, list):
		return []
	models = []
	for item in entries:
		if not isinstance(item, dict):
			continue
		identifier = item.get("id") or item.get("name") or item.get("model")
		if not isinstance(identifier, str) or not identifier.strip():
			continue
		identifier = identifier.strip()
		endpoint = ""
		for key in ("endpoint", "path", "url"):
			value = item.get(key)
			if isinstance(value, str) and value.strip():
				endpoint = value.strip()
				break
		if not endpoint and identifier.startswith("/"):
			endpoint = identifier
		if not endpoint:
			continue
		label = item.get("label") or item.get("display_name") or identifier
		models.append({"id": identifier, "label": str(label), "endpoint": endpoint})
	return models


def parseOpenApiModels(body) -> list:
	"""OpenAPI 兜底：把 ``paths`` 里带 ``post`` 的端点当作"可用模型"。

	这不是通行的"模型列表"，只是没有 `/models` 时的兜底；``/restructure-pages``
	这类非 OCR 端点会被排除。返回 ``[{"id","label","endpoint"}]``。
	"""
	if not isinstance(body, dict):
		return []
	paths = body.get("paths")
	if not isinstance(paths, dict):
		return []
	models = []
	for path in sorted(paths):
		if not isinstance(path, str) or not path.startswith("/"):
			continue
		if path in NON_OCR_ENDPOINTS:
			continue
		operations = paths[path]
		if not isinstance(operations, dict) or "post" not in operations:
			continue
		models.append({"id": path, "label": path, "endpoint": path})
	return models


class _ProbeError(Exception):
	"""设置面板探测用的轻量异常：带 HTTP 状态码，便于区分 401/404。"""

	def __init__(self, message: str, status: int = 0):
		super().__init__(message)
		self.status = status


def _getJson(url: str, timeout: float, token: str = ""):
	"""GET 一个 JSON 接口；网络/HTTP/解析错误统一抛 `_ProbeError`。"""
	if not url:
		raise _ProbeError(_("OCR service address is empty or invalid."))
	headers = {"Accept": "application/json"}
	if token:
		headers["Authorization"] = "Bearer %s" % token
	request = urllib.request.Request(url, headers=headers, method="GET")
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			raw = response.read()
	except urllib.error.HTTPError as error:
		raise _ProbeError(_("HTTP status code: %s") % error.code, error.code)
	except urllib.error.URLError as error:
		raise _ProbeError(_("Connection failed: %s") % getattr(error, "reason", error))
	except (TimeoutError, OSError) as error:
		raise _ProbeError(_("Connection failed: %s") % error)
	try:
		return json.loads(raw.decode("utf-8", errors="replace"))
	except ValueError as error:
		raise _ProbeError(_("Invalid JSON response: %s") % error)


class HttpOcrClient:
	"""调用外部 PaddleOCR 服务的客户端。

	参数与 ``config.conf["LION-modified"]`` 里的设置一一对应，实例本身不读配置，
	方便单测；主循环与设置面板各自按需构造。
	"""

	def __init__(
		self,
		url: str,
		token: str = "",
		timeout: float = 15.0,
		visualize: bool = False,
		score_thresh: float = 0.0,
		det_limit_side_len: int = 0,
		image_format: str = "JPEG",
		model: str = "",
	):
		self.service_base = normalizeServiceBase(url)
		self.model = normalizeModel(model)
		self.base_url = composeUrl(url, self.model)
		self.token = (token or "").strip()
		try:
			self.timeout = float(timeout)
		except (TypeError, ValueError):
			self.timeout = 15.0
		if self.timeout <= 0:
			self.timeout = 15.0
		self.visualize = bool(visualize)
		try:
			self.score_thresh = float(score_thresh)
		except (TypeError, ValueError):
			self.score_thresh = 0.0
		try:
			self.det_limit_side_len = int(det_limit_side_len)
		except (TypeError, ValueError):
			self.det_limit_side_len = 0
		self.image_format = image_format if image_format in IMAGE_FORMATS else "JPEG"
		# 保存最近一次失败原因，供主循环日志与设置面板"测试连接"使用；
		# 用 thread local，避免后台 OCR 线程与 GUI 线程互相覆盖。
		self._context = threading.local()

	# ---------------------------------------------------------------- 上下文

	def _setContext(self, *, url: str = "", status: int = 0, message: str = "", hint: str = "") -> None:
		self._context.url = url
		self._context.status = status
		self._context.message = message
		self._context.hint = hint

	def _getContext(self, name: str, default=""):
		return getattr(self._context, name, default)

	@property
	def lastError(self) -> str:
		"""最近一次失败的完整描述（含服务端 errorMsg / hint / URL）。"""
		parts = []
		status = self._getContext("status", 0)
		message = self._getContext("message", "")
		hint = self._getContext("hint", "")
		url = self._getContext("url", "")
		if status:
			parts.append(_("HTTP status code: %s") % status)
		if message:
			parts.append(_("Server message: %s") % message)
		if hint:
			parts.append(_("Server hint: %s") % hint)
		if url:
			parts.append(_("Address: %s") % url)
		return _("; ").join(parts)

	@property
	def lastErrorBrief(self) -> str:
		"""面向语音提示的简短原因。"""
		status = self._getContext("status", 0)
		message = self._getContext("message", "")
		if message:
			return message
		if status == 401:
			return _("Unauthorized")
		if status:
			return _("HTTP status code: %s") % status
		return _("Unknown error")

	# ------------------------------------------------------------------ 图像

	def encodeImage(self, image) -> bytes:
		"""把 PIL 图像编码成配置指定的格式。"""
		buffered = BytesIO()
		if self.image_format == "JPEG":
			# JPEG 不支持 RGBA，必要时先转 RGB
			if image.mode not in ("RGB", "L"):
				image = image.convert("RGB")
			image.save(buffered, format="JPEG", quality=85)
		else:
			image.save(buffered, format="PNG")
		return buffered.getvalue()

	def buildPayload(self, imageBytes: bytes, fileType: int = 1) -> dict:
		"""构造 `/ocr` 请求体。

		显式下发三个能力开关为 ``false``：服务端配置为支持这些开关而把顶层开关打开，
		省略字段会导致按配置执行（实测 1000x700 区域从 146 ms 涨到 329 ms）。
		"""
		payload = {
			"file": base64.b64encode(imageBytes).decode("ascii"),
			"fileType": fileType,
			"visualize": self.visualize,
			"useDocOrientationClassify": False,
			"useDocUnwarping": False,
			"useTextlineOrientation": False,
		}
		if self.det_limit_side_len > 0:
			payload["textDetLimitSideLen"] = self.det_limit_side_len
		if self.score_thresh > 0:
			payload["textRecScoreThresh"] = self.score_thresh
		return payload

	# -------------------------------------------------------------- 请求收发

	def _postRaw(self, payload: dict):
		"""POST 一次请求，返回 ``(httpStatus, parsedBodyOrDict)``。

		网络层错误直接返回 ``{"code", "data"}`` 与状态码 0：
		* 901 地址无效
		* 902 连接失败/被拒
		* 903 超时
		* 904 服务端返回 HTTP 4xx/5xx
		* 905 返回值不是合法 JSON
		"""
		if not self.base_url:
			return 0, {"code": 901, "data": _("OCR service address is empty or invalid.")}
		self._setContext(url=self.base_url)
		data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
		headers = {"Content-Type": "application/json"}
		if self.token:
			headers["Authorization"] = "Bearer %s" % self.token
		request = urllib.request.Request(self.base_url, data=data, headers=headers, method="POST")
		try:
			with urllib.request.urlopen(request, timeout=self.timeout) as response:
				raw = response.read()
				status = getattr(response, "status", 200)
		except urllib.error.HTTPError as error:
			# 服务端的 4xx/5xx 也带 JSON 正文，尽量把 errorMsg/hint 提取出来
			message, hint = _decodeErrorBody(_safeRead(error))
			self._setContext(
				url=self.base_url,
				status=error.code,
				message=message or str(error.reason or ""),
				hint=hint,
			)
			detail = self.lastErrorBrief
			log.error("LION: OCR 服务返回 HTTP %s：%s", error.code, self.lastError)
			return error.code, {
				"code": HTTP_STATUS_TO_CODE.get(error.code, 904),
				"data": _("Service responded with HTTP status code: %s. %s") % (error.code, detail),
			}
		except urllib.error.URLError as error:
			reason = getattr(error, "reason", error)
			self._setContext(url=self.base_url, message=str(reason))
			if _isTimeoutReason(reason):
				log.error("LION: OCR 服务请求超时（%s 秒）", self.timeout)
				return 0, {"code": 903, "data": _("Request timed out after %s seconds") % self.timeout}
			log.error("LION: 连接 OCR 服务失败：%s", self.lastError)
			return 0, {"code": 902, "data": _("Connection failed: %s") % reason}
		except TimeoutError:
			self._setContext(url=self.base_url)
			log.error("LION: OCR 服务请求超时（%s 秒）", self.timeout)
			return 0, {"code": 903, "data": _("Request timed out after %s seconds") % self.timeout}
		except OSError as error:
			self._setContext(url=self.base_url, message=str(error))
			log.error("LION: 访问 OCR 服务出错：%s", self.lastError)
			return 0, {"code": 902, "data": _("Connection failed: %s") % error}
		try:
			return status, json.loads(raw.decode("utf-8", errors="replace"))
		except ValueError as error:
			self._setContext(url=self.base_url, message=str(error))
			log.error("LION: OCR 服务返回值不是合法 JSON：%s", self.lastError)
			return status, {"code": 905, "data": _("Invalid JSON response: %s") % error}

	def _post(self, payload: dict) -> dict:
		"""POST 一次请求并解析结果，返回 ``{"code", "data"}`` 契约。"""
		_, body = self._postRaw(payload)
		if isinstance(body, dict) and "code" in body and "data" in body and "result" not in body:
			# 网络层已经给出了错误码，直接透传
			return body
		return self.parseResult(body)

	def runBytes(self, imageBytes: bytes):
		"""识别一张图片的字节流，返回与原 PaddleOCR-json API 相同的结构。"""
		return self._post(self.buildPayload(imageBytes))

	# ---------------------------------------------------------------- 解析

	def parseResult(self, body) -> dict:
		"""把服务端返回体解析成 ``{"code", "data"}``。

		兼容两种产线的响应形态（3.3.6 起）：

		* `POST /ocr`（PP-OCRv6）：``result.ocrResults[0].prunedResult.rec_texts / rec_scores``
		* `POST /layout-parsing`（PaddleOCR-VL）：
		  ``result.layoutParsingResults[0].prunedResult.parsing_res_list[*].block_content``
		  （按 ``block_order`` 排序），没有时回退 ``markdown.text``。

		VL 不提供逐行置信度，因此其文本行的 ``score`` 记为 1.0，避免被 ``scoreThresh`` 误杀。
		"""
		if not isinstance(body, dict):
			return {"code": 905, "data": _("Unexpected response type.")}
		error_code = body.get("errorCode")
		if error_code not in (None, 0, "0"):
			message = body.get("errorMsg") or ""
			hint = body.get("hint") or ""
			self._setContext(
				url=self.base_url,
				status=int(error_code) if str(error_code).isdigit() else 0,
				message=str(message),
				hint=str(hint),
			)
			log.error("LION: OCR 服务返回错误：%s", self.lastError)
			return {"code": 904, "data": self.lastError}
		result = body.get("result") or {}
		entries = result.get("ocrResults") or result.get("layoutParsingResults") or []
		if not isinstance(entries, list) or not entries:
			return {"code": 905, "data": _("No OCR result found in the response.")}
		first = entries[0] or {}
		pruned = first.get("prunedResult")
		if not isinstance(pruned, dict):
			# 少数端点直接把文本放在顶层
			pruned = first if isinstance(first, dict) else {}
		# 形态一：PP-OCRv6 的逐行文本 + 置信度
		if isinstance(pruned.get("rec_texts"), list):
			return {"code": 100, "data": self._linesFromRecTexts(
				pruned["rec_texts"], pruned.get("rec_scores") or []
			)}
		# 形态二：PaddleOCR-VL 的版面块
		if isinstance(pruned.get("parsing_res_list"), list):
			lines = self._linesFromParsingList(pruned["parsing_res_list"])
			if lines:
				return {"code": 100, "data": lines}
			markdown = first.get("markdown")
			if isinstance(markdown, dict):
				return {"code": 100, "data": self._linesFromMarkdown(markdown)}
			return {"code": 100, "data": []}
		return {"code": 100, "data": []}

	def _linesFromRecTexts(self, texts, scores) -> list:
		"""PP-OCRv6：过滤空串与低于阈值的行。"""
		lines = []
		for index, text in enumerate(texts or []):
			if not isinstance(text, str):
				continue
			if not text.strip():
				# 服务端在低置信度处会返回空串，必须滤掉，否则会被当成"文本变化"
				continue
			try:
				score = float(scores[index]) if index < len(scores) else 0.0
			except (TypeError, ValueError):
				score = 0.0
			if self.score_thresh > 0 and score < self.score_thresh:
				continue
			lines.append({"text": text, "score": score})
		return lines

	def _linesFromParsingList(self, blocks) -> list:
		"""PaddleOCR-VL：按 ``block_order`` 提取文本块的正文。"""
		ordered = []
		total = len(blocks or [])
		for index, block in enumerate(blocks or []):
			if not isinstance(block, dict):
				continue
			label = str(block.get("block_label") or "").strip().lower()
			if label in NON_TEXT_BLOCK_LABELS:
				continue
			content = block.get("block_content")
			if not isinstance(content, str):
				continue
			content = content.strip()
			if not content or content.lower().startswith(MARKUP_PREFIXES):
				continue
			order = block.get("block_order")
			key = order if isinstance(order, int) else (total + index)
			ordered.append((key, index, content))
		ordered.sort(key=lambda item: (item[0], item[1]))
		# VL 没有逐行置信度，统一给 1.0，避免被 scoreThresh 过滤
		return [{"text": content, "score": 1.0} for _key, _index, content in ordered]

	def _linesFromMarkdown(self, markdown: dict) -> list:
		"""PaddleOCR-VL：没有版面块时，按 markdown 文本行兜底。"""
		text = markdown.get("text")
		if not isinstance(text, str) or not text.strip():
			return []
		lines = []
		for raw in text.splitlines():
			line = raw.strip()
			if not line or line.lower().startswith(MARKUP_PREFIXES):
				continue
			lines.append({"text": line, "score": 1.0})
		return lines

	# ------------------------------------------------------ 模型列表与令牌

	def listModels(self, timeout: float | None = None):
		"""探测服务可用的"模型/端点"，返回 ``(models, source, error)``。

		按通行程度依次尝试：

		1. ``GET /models`` —— OpenAI 风格模型列表（若服务提供，额外用 ``endpoint`` 字段路由）；
		2. ``GET /v1/models`` —— OpenAI 兼容实现；
		3. ``GET /openapi.json`` —— **兜底**：把带 ``post`` 的端点当"可用模型"。

		``models`` 是 ``[{"id","label","endpoint"}]``；``source`` 是命中的那一级
		（``"models"`` / ``"v1/models"`` / ``"openapi"``），失败时为 ``""`` 并给出 ``error``。
		"""
		base = self.service_base or normalizeServiceBase(self.base_url)
		if not base:
			return [], "", _("OCR service address is empty or invalid.")
		wait = self.timeout if timeout is None else timeout
		problems = []
		for path in MODEL_LIST_PATHS:
			url = base.rstrip("/") + path
			try:
				body = _getJson(url, wait, self.token)
			except _ProbeError as error:
				if error.status in (401, 403):
					return [], "", _("Unauthorized: check the service token.")
				problems.append("%s: %s" % (path, error))
				continue
			models = parseModelList(body, path)
			if models:
				return models, path.lstrip("/"), ""
			problems.append("%s: %s" % (path, _("No models found in the response.")))
		# OpenAPI 兜底
		try:
			body = _getJson(openapiUrl(base), wait, self.token)
		except _ProbeError as error:
			if error.status in (401, 403):
				return [], "", _("Unauthorized: check the service token.")
			problems.append("openapi: %s" % error)
			return [], "", _("; ").join(problems)
		models = parseOpenApiModels(body)
		if not models:
			return [], "", _("No OCR endpoint found on the service.")
		return models, "openapi", ""

	def verifyToken(self, timeout: float | None = None):
		"""用一个"缺少 ``file``"的请求快速校验令牌，返回 ``(ok, 描述)``。

		令牌中间件在路由之前执行：令牌不对 → 401；令牌有效但请求体不合法 → 400/422。
		因此**不会触发真正的推理**，对 PaddleOCR-VL 也是毫秒级。
		"""
		if not self.base_url:
			return False, _("OCR service address is empty or invalid.")
		wait = self.timeout if timeout is None else timeout
		data = json.dumps({"fileType": 1}).encode("utf-8")
		headers = {"Content-Type": "application/json"}
		if self.token:
			headers["Authorization"] = "Bearer %s" % self.token
		request = urllib.request.Request(self.base_url, data=data, headers=headers, method="POST")
		try:
			with urllib.request.urlopen(request, timeout=wait) as response:
				return True, _("Token accepted (HTTP %s).") % getattr(response, "status", 200)
		except urllib.error.HTTPError as error:
			if error.code in (401, 403):
				return False, _("Unauthorized: check the service token.")
			if error.code == 404:
				return False, _("Endpoint not found: %s") % self.base_url
			if error.code in (400, 422):
				return True, _("Token accepted (HTTP %s).") % error.code
			return False, _("Service responded with HTTP status code: %s") % error.code
		except Exception as error:  # noqa: BLE001 - 探测不应抛出到 GUI
			return False, _("Connection failed: %s") % error

	# -------------------------------------------------------------- 连通性

	def testConnection(self, timeout: float | None = None, sendRequest: bool = False):
		"""探测服务可用性，返回 ``(ok, 描述)``。

		默认只打 `/health`（免鉴权、极快）；``sendRequest=True`` 时额外发一次空白
		小图请求，用于同时验证令牌与端点（PaddleOCR-VL 较慢，调用方应避免对 VL 用）。
		"""
		if not self.base_url:
			return False, _("OCR service address is empty or invalid.")
		url = healthUrl(self.base_url)
		if not url:
			return False, _("OCR service address is empty or invalid.")
		request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
		wait = self.timeout if timeout is None else timeout
		try:
			with urllib.request.urlopen(request, timeout=wait) as response:
				status = getattr(response, "status", 200)
				response.read()
		except urllib.error.HTTPError as error:
			return False, _("Service responded with HTTP status code: %s") % error.code
		except urllib.error.URLError as error:
			return False, _("Connection failed: %s") % getattr(error, "reason", error)
		except Exception as error:  # noqa: BLE001 - 探测不应抛出到 GUI
			return False, _("Connection failed: %s") % error
		if not sendRequest:
			return True, _("Connected (HTTP %s).") % status
		# 发一张 8x8 的白图，验证令牌与端点
		probe = _tinyImageBytes()
		if probe is None:
			return True, _("Connected (HTTP %s).") % status
		result = self.runBytes(probe)
		if result.get("code") == 100:
			return True, _("Connected and OCR request succeeded (HTTP %s).") % status
		return False, self.lastErrorBrief or str(result.get("data", ""))


def _isTimeoutReason(reason) -> bool:
	"""判断 URLError 的原因是否为超时（Python 把超时包成 URLError）。"""
	text = str(reason).lower()
	return "timed out" in text or "timeout" in text


def _safeRead(httpError) -> bytes:
	try:
		return httpError.read()
	except Exception:  # noqa: BLE001 - 读不到正文时退化为空
		return b""


def _decodeErrorBody(raw: bytes):
	"""从错误响应体里取出 ``errorMsg``（或 FastAPI 的 ``detail``）与 ``hint``。"""
	if not raw:
		return "", ""
	try:
		body = json.loads(raw.decode("utf-8", errors="replace"))
	except ValueError:
		text = raw.decode("utf-8", errors="replace")
		return re.sub(r"\s+", " ", text)[:200], ""
	if not isinstance(body, dict):
		return "", ""
	message = body.get("errorMsg") or body.get("detail") or body.get("message") or ""
	return str(message), str(body.get("hint") or "")


_TINY_IMAGE = None


def _tinyImageBytes():
	"""构造 8x8 白色 PNG，用于"测试连接"里的真实请求校验。"""
	global _TINY_IMAGE
	if _TINY_IMAGE is not None:
		return _TINY_IMAGE
	try:
		from PIL import Image

		image = Image.new("RGB", (8, 8), (255, 255, 255))
		buffered = BytesIO()
		image.save(buffered, format="PNG")
		_TINY_IMAGE = buffered.getvalue()
	except Exception:  # noqa: BLE001 - 无 PIL 时退化为只探活
		return None
	return _TINY_IMAGE
