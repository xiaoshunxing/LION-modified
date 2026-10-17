# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""`lion.ocrClient` 的离线 + 在线测试。

离线部分用本地 `http.server` 桩覆盖全部错误码；在线部分（若服务可达）对
`http://127.0.0.1:23353/ocr` 跑真实请求，验证响应映射与过滤。

运行::

    python -m unittest discover -s tests -v

可用环境变量覆盖在线测试的目标：`LION_TEST_URL`、`LION_TEST_TOKEN`、
`LION_TEST_LIVE=0` 关闭在线用例。
"""

from __future__ import annotations

import http.server
import os
import pathlib
import sys
import threading
import time
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nvda_stubs  # noqa: E402

nvda_stubs.install()
nvda_stubs.package("lion")

from lion import ocrClient  # noqa: E402

LIVE_URL = os.environ.get("LION_TEST_URL", "http://127.0.0.1:23353/ocr")
TOKEN_FILE = os.environ.get("LION_TEST_TOKEN_FILE", "")
LIVE_ENABLED = os.environ.get("LION_TEST_LIVE", "1") not in ("0", "false", "False")


def liveToken() -> str:
	explicit = os.environ.get("LION_TEST_TOKEN")
	if explicit:
		return explicit.strip()
	if not TOKEN_FILE:
		return ""
	try:
		return pathlib.Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
	except OSError:
		return ""


# --------------------------------------------------------------------- HTTP 桩


class _Handler(http.server.BaseHTTPRequestHandler):
	mode = "ok"

	#: `/models` 的 OpenAI 风格响应（带 endpoint 字段，本服务改造后应长这样）
	MODELS_BODY = (
		b'{"object":"list","data":['
		b'{"id":"pp-ocrv6-fast","object":"model","label":"PP-OCRv6 (fast)","endpoint":"/ocr"},'
		b'{"id":"paddleocr-vl-1.6","object":"model","label":"PaddleOCR-VL 1.6",'
		b'"endpoint":"/layout-parsing"}]}'
	)

	#: OpenAPI 兜底用的最小文档
	OPENAPI_BODY = (
		b'{"openapi":"3.1.0","paths":{'
		b'"/health":{"get":{}},"/ocr":{"post":{}},'
		b'"/layout-parsing":{"post":{}},"/restructure-pages":{"post":{}}}}'
	)

	def log_message(self, *args):  # noqa: D102 - 静音
		pass

	def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 接口
		path = urllib.parse.urlsplit(self.path).path
		if self.mode == "health_ok":
			self._send(200, b'{"errorCode":0,"errorMsg":"Healthy"}')
		elif self.mode == "health_500":
			self._send(500, b"boom")
		elif self.mode == "health_garbage":
			self._send(200, b"not json at all")
		elif self.mode == "models_ok" and path == "/models":
			self._send(200, self.MODELS_BODY)
		elif self.mode == "v1_models_ok" and path == "/v1/models":
			self._send(200, self.MODELS_BODY)
		elif self.mode == "models_401":
			self._send(401, b'{"errorMsg":"Unauthorized"}')
		elif self.mode == "openapi_ok":
			if path == "/openapi.json":
				self._send(200, self.OPENAPI_BODY)
			else:
				self._send(404, b'{"errorMsg":"not found"}')
		elif self.mode == "openapi_garbage":
			if path == "/openapi.json":
				self._send(200, b"not json at all")
			else:
				self._send(404, b'{"errorMsg":"not found"}')
		else:
			self._send(404, b'{"errorMsg":"not found"}')

	def do_POST(self):  # noqa: N802
		length = int(self.headers.get("Content-Length") or 0)
		self.rfile.read(length)
		if self.mode == "ok":
			self._send(200, b'{"errorCode":0,"result":{"ocrResults":[{"prunedResult":'
				+ b'{"rec_texts":["hello","","world"],"rec_scores":[0.99,0.0,0.2]}}]}}')
		elif self.mode == "empty":
			self._send(200, b'{"errorCode":0,"result":{"ocrResults":[{"prunedResult":'
				+ b'{"rec_texts":[],"rec_scores":[]}}]}}')
		elif self.mode == "no_results":
			self._send(200, b'{"errorCode":0,"result":{"ocrResults":[]}}')
		elif self.mode == "error_code":
			self._send(200, b'{"errorCode":400,"errorMsg":"Unsupported capability",'
				+ b'"hint":"use /layout-parsing"}')
		elif self.mode in ("401", "models_401"):
			self._send(401, b'{"errorCode":401,"errorMsg":"Unauthorized: missing or invalid token",'
				+ b'"hint":"Set header  Authorization: Bearer <token>"}')
		elif self.mode == "probe_422":
			# 令牌正确但请求体不合法：verifyToken 应判定为"令牌有效"
			self._send(422, b'{"detail":[{"loc":["body","file"],"msg":"field required"}]}')
		elif self.mode == "probe_404":
			self._send(404, b'{"errorMsg":"not found"}')
		elif self.mode == "500":
			self._send(500, b'{"detail":"the input params for model settings are invalid!"}')
		elif self.mode == "garbage":
			self._send(200, b"<html>not json</html>")
		elif self.mode == "slow":
			time.sleep(3)
			self._send(200, b'{"errorCode":0,"result":{"ocrResults":[]}}')
		else:
			self._send(404, b"{}")

	def _send(self, status: int, body: bytes):
		self.send_response(status)
		self.send_header("Content-Type", "application/json; charset=utf-8")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)


class StubServer:
	def __init__(self):
		self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
		self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

	def __enter__(self):
		self.thread.start()
		return self

	def __exit__(self, *exc):
		self.httpd.shutdown()
		self.httpd.server_close()
		self.thread.join(timeout=5)

	@property
	def url(self) -> str:
		host, port = self.httpd.server_address[:2]
		return "http://%s:%s/ocr" % (host, port)

	@property
	def service_base(self) -> str:
		"""不带端点路径的服务基址（3.3.6 的 serviceUrl 语义）。"""
		host, port = self.httpd.server_address[:2]
		return "http://%s:%s" % (host, port)

	def setMode(self, mode: str) -> None:
		_Handler.mode = mode


class _ModeTestCase(unittest.TestCase):
	"""公共基类：起一个桩服务器并按需切换行为。"""

	mode = "ok"
	timeout = 5.0

	def setUp(self):
		self.server = StubServer()
		self.server.__enter__()
		self.addCleanup(lambda: self.server.__exit__(None, None, None))
		self.server.setMode(self.mode)
		self.client = ocrClient.HttpOcrClient(self.server.url, timeout=self.timeout)

	def runBytes(self):
		return self.client.runBytes(b"\x89PNG\r\n\x1a\nfake")


# ------------------------------------------------------------------ URL 规范化


class NormalizeBaseUrlTests(unittest.TestCase):
	def test_bareHostGetsSchemeAndEndpoint(self):
		self.assertEqual(
			ocrClient.normalizeBaseUrl("127.0.0.1:23353"),
			"http://127.0.0.1:23353/ocr",
		)

	def test_rootPathGetsDefaultEndpoint(self):
		self.assertEqual(
			ocrClient.normalizeBaseUrl("http://127.0.0.1:23353/"),
			"http://127.0.0.1:23353/ocr",
		)
		self.assertEqual(
			ocrClient.normalizeBaseUrl("http://127.0.0.1:23353"),
			"http://127.0.0.1:23353/ocr",
		)

	def test_explicitEndpointAndTrailingSlash(self):
		self.assertEqual(
			ocrClient.normalizeBaseUrl("http://127.0.0.1:23353/ocr/"),
			"http://127.0.0.1:23353/ocr",
		)
		self.assertEqual(
			ocrClient.normalizeBaseUrl("  http://127.0.0.1:23353/ocr-acc  "),
			"http://127.0.0.1:23353/ocr-acc",
		)

	def test_unknownPathIsKept(self):
		self.assertEqual(
			ocrClient.normalizeBaseUrl("http://host:1/custom/ocr"),
			"http://host:1/custom/ocr",
		)

	def test_emptyAndInvalid(self):
		for bad in ("", "   ", None, 42, "http://", "ftp://host/x", "/ocr"):
			self.assertEqual(ocrClient.normalizeBaseUrl(bad), "", msg=repr(bad))

	def test_healthUrl(self):
		self.assertEqual(
			ocrClient.healthUrl("http://127.0.0.1:23353/ocr"),
			"http://127.0.0.1:23353/health",
		)
		self.assertEqual(ocrClient.healthUrl(""), "")

	def test_httpsPreserved(self):
		self.assertEqual(
			ocrClient.normalizeBaseUrl("https://ocr.example.com/ocr"),
			"https://ocr.example.com/ocr",
		)


# -------------------------------------------------------------------- 请求体


class PayloadTests(unittest.TestCase):
	def test_payloadDisablesCapabilities(self):
		client = ocrClient.HttpOcrClient("http://127.0.0.1:23353/ocr", visualize=False)
		payload = client.buildPayload(b"abc")
		self.assertEqual(payload["fileType"], 1)
		self.assertIs(payload["visualize"], False)
		self.assertIs(payload["useDocOrientationClassify"], False)
		self.assertIs(payload["useDocUnwarping"], False)
		self.assertIs(payload["useTextlineOrientation"], False)
		self.assertNotIn("textDetLimitSideLen", payload)
		self.assertNotIn("textRecScoreThresh", payload)
		self.assertEqual(payload["file"], "YWJj")

	def test_payloadOptionalFields(self):
		client = ocrClient.HttpOcrClient(
			"http://127.0.0.1:23353/ocr",
			det_limit_side_len=960,
			score_thresh=0.5,
		)
		payload = client.buildPayload(b"abc")
		self.assertEqual(payload["textDetLimitSideLen"], 960)
		self.assertEqual(payload["textRecScoreThresh"], 0.5)

	def test_imageFormatMapping(self):
		self.assertEqual(ocrClient.readImageFormat(0), "PNG")
		self.assertEqual(ocrClient.readImageFormat(1), "JPEG")
		self.assertEqual(ocrClient.readImageFormat(7), "JPEG")
		self.assertEqual(ocrClient.readImageFormat("bad"), "JPEG")
		self.assertEqual(ocrClient.imageFormatIndex("PNG"), 0)
		self.assertEqual(ocrClient.imageFormatIndex("nope"), 1)

	def test_timeoutFallback(self):
		client = ocrClient.HttpOcrClient("http://h:1/ocr", timeout=0)
		self.assertEqual(client.timeout, 15.0)
		client = ocrClient.HttpOcrClient("http://h:1/ocr", timeout="abc")
		self.assertEqual(client.timeout, 15.0)


# ------------------------------------------------------------------ 响应解析


class ParseResultTests(unittest.TestCase):
	def setUp(self):
		self.client = ocrClient.HttpOcrClient("http://127.0.0.1:23353/ocr")

	def test_successFiltersEmptyText(self):
		body = {
			"errorCode": 0,
			"result": {
				"ocrResults": [
					{"prunedResult": {"rec_texts": ["a", "", "b"], "rec_scores": [0.9, 0.0, 0.8]}}
				]
			},
		}
		result = self.client.parseResult(body)
		self.assertEqual(result["code"], 100)
		self.assertEqual([line["text"] for line in result["data"]], ["a", "b"])
		self.assertAlmostEqual(result["data"][0]["score"], 0.9)

	def test_scoreThresholdFilters(self):
		client = ocrClient.HttpOcrClient("http://127.0.0.1:23353/ocr", score_thresh=0.5)
		body = {
			"errorCode": 0,
			"result": {"ocrResults": [{"prunedResult": {"rec_texts": ["a", "b"], "rec_scores": [0.9, 0.2]}}]},
		}
		result = client.parseResult(body)
		self.assertEqual([line["text"] for line in result["data"]], ["a"])

	def test_missingScoresDefaultToZero(self):
		body = {"result": {"ocrResults": [{"prunedResult": {"rec_texts": ["x"]}}]}}
		result = self.client.parseResult(body)
		self.assertEqual(result["code"], 100)
		self.assertEqual(result["data"][0]["score"], 0.0)

	def test_layoutParsingShape(self):
		body = {
			"result": {
				"layoutParsingResults": [
					{"prunedResult": {"rec_texts": ["标题"], "rec_scores": [0.99]}}
				]
			}
		}
		result = self.client.parseResult(body)
		self.assertEqual(result["code"], 100)
		self.assertEqual(result["data"][0]["text"], "标题")

	def test_vlParsingResListOrderedByBlockOrder(self):
		"""PaddleOCR-VL 的真实形态：按 block_order 排序，跳过图表与标记内容。"""
		body = {
			"result": {
				"layoutParsingResults": [
					{
						"prunedResult": {
							"parsing_res_list": [
								{"block_label": "text", "block_content": "第二行", "block_order": 2},
								{"block_label": "text", "block_content": "第一行", "block_order": 1},
								{"block_label": "figure", "block_content": "图注", "block_order": 3},
								{"block_label": "table", "block_content": "<table>x</table>", "block_order": 4},
								{"block_label": "text", "block_content": "   ", "block_order": 5},
							]
						},
						"markdown": {"text": "不应被使用"},
					}
				]
			}
		}
		result = self.client.parseResult(body)
		self.assertEqual(result["code"], 100)
		self.assertEqual([line["text"] for line in result["data"]], ["第一行", "第二行"])
		self.assertEqual(result["data"][0]["score"], 1.0)

	def test_vlMarkdownFallback(self):
		body = {
			"result": {
				"layoutParsingResults": [
					{
						"prunedResult": {"parsing_res_list": []},
						"markdown": {"text": "# 标题\n\n![图](x.png)\n正文"},
					}
				]
			}
		}
		result = self.client.parseResult(body)
		self.assertEqual(result["code"], 100)
		self.assertEqual([line["text"] for line in result["data"]], ["# 标题", "正文"])

	def test_vlBlankPageIsEmptyNotAnError(self):
		body = {
			"result": {
				"layoutParsingResults": [
					{"prunedResult": {"parsing_res_list": []}, "markdown": {"text": ""}}
				]
			}
		}
		result = self.client.parseResult(body)
		self.assertEqual(result["code"], 100)
		self.assertEqual(result["data"], [])

	def test_vlLinesNotDroppedByScoreThreshold(self):
		"""VL 没有逐行置信度，score=1.0 不应被用户设的最低分误杀。"""
		client = ocrClient.HttpOcrClient("http://h:1/ocr", score_thresh=0.9)
		body = {
			"result": {
				"layoutParsingResults": [
					{"prunedResult": {"parsing_res_list": [{"block_label": "text", "block_content": "文本"}]}}
				]
			}
		}
		result = client.parseResult(body)
		self.assertEqual([line["text"] for line in result["data"]], ["文本"])

	def test_errorCodeInBody(self):
		body = {"errorCode": 400, "errorMsg": "Unsupported capability", "hint": "use /layout-parsing"}
		result = self.client.parseResult(body)
		self.assertEqual(result["code"], 904)
		self.assertIn("Unsupported capability", result["data"])
		self.assertIn("use /layout-parsing", result["data"])

	def test_missingResult(self):
		self.assertEqual(self.client.parseResult({"errorCode": 0}).get("code"), 905)
		self.assertEqual(self.client.parseResult({"result": {"ocrResults": []}}).get("code"), 905)
		self.assertEqual(self.client.parseResult([]).get("code"), 905)
		self.assertEqual(self.client.parseResult(None).get("code"), 905)


# ------------------------------------------------------------------- 桩服务器


class StubServerSuccessTests(_ModeTestCase):
	mode = "ok"

	def test_success(self):
		result = self.runBytes()
		self.assertEqual(result["code"], 100)
		self.assertEqual([line["text"] for line in result["data"]], ["hello", "world"])
		self.assertEqual(len(result["data"]), 2)


class StubServerFailureTests(_ModeTestCase):
	mode = "401"

	def test_401(self):
		self.server.setMode("401")
		result = self.runBytes()
		self.assertEqual(result["code"], 904)
		self.assertIn("Unauthorized", result["data"])
		self.assertEqual(self.client._getContext("status"), 401)


class StubServer500Tests(_ModeTestCase):
	mode = "500"

	def test_500(self):
		result = self.runBytes()
		self.assertEqual(result["code"], 904)
		self.assertIn("invalid", result["data"])
		self.assertEqual(self.client._getContext("status"), 500)


class StubServerGarbageTests(_ModeTestCase):
	mode = "garbage"

	def test_garbage(self):
		result = self.runBytes()
		self.assertEqual(result["code"], 905)


class StubServerEmptyTests(_ModeTestCase):
	mode = "empty"

	def test_empty(self):
		result = self.runBytes()
		self.assertEqual(result["code"], 100)
		self.assertEqual(result["data"], [])


class StubServerNoResultsTests(_ModeTestCase):
	mode = "no_results"

	def test_no_results(self):
		self.assertEqual(self.runBytes()["code"], 905)


class StubServerSlowTests(unittest.TestCase):
	def test_timeout(self):
		with StubServer() as server:
			server.setMode("slow")
			client = ocrClient.HttpOcrClient(server.url, timeout=1.0)
			start = time.time()
			result = client.runBytes(b"x")
			elapsed = time.time() - start
			self.assertEqual(result["code"], 903)
			self.assertLess(elapsed, 3.0)


class ConnectionRefusedTests(unittest.TestCase):
	def test_refused(self):
		# 端口 9（discard）在 Windows/Linux 上都不会有人监听，必然连接被拒
		client = ocrClient.HttpOcrClient("http://127.0.0.1:9/ocr", timeout=3.0)
		result = client.runBytes(b"x")
		self.assertEqual(result["code"], 902, result)
		self.assertTrue(result["data"])

	def test_closedStubServerIsRefused(self):
		with StubServer() as server:
			url = server.url
			port = server.httpd.server_address[1]
		self.assertFalse(port == 0)
		client = ocrClient.HttpOcrClient(url, timeout=3.0)
		result = client.runBytes(b"x")
		self.assertEqual(result["code"], 902, result)

	def test_invalidUrlGives901(self):
		client = ocrClient.HttpOcrClient("", timeout=2.0)
		self.assertEqual(client.runBytes(b"x")["code"], 901)
		self.assertEqual(client.testConnection()[0], False)


class TestConnectionTests(unittest.TestCase):
	def test_healthOk(self):
		with StubServer() as server:
			server.setMode("health_ok")
			client = ocrClient.HttpOcrClient(server.url, timeout=3.0)
			ok, detail = client.testConnection()
			self.assertTrue(ok, detail)

	def test_health500(self):
		with StubServer() as server:
			server.setMode("health_500")
			client = ocrClient.HttpOcrClient(server.url, timeout=3.0)
			ok, detail = client.testConnection()
			self.assertFalse(ok)
			self.assertIn("500", detail)

	def test_healthRefused(self):
		client = ocrClient.HttpOcrClient("http://127.0.0.1:9/ocr", timeout=3.0)
		ok, detail = client.testConnection()
		self.assertFalse(ok)
		self.assertTrue(detail)


# ------------------------------------------- 3.3.6：服务基址 / 模型 / 模型发现


class ServiceBaseTests(unittest.TestCase):
	def test_keepsPathPrefix(self):
		self.assertEqual(ocrClient.normalizeServiceBase("127.0.0.1:23353"), "http://127.0.0.1:23353")
		self.assertEqual(ocrClient.normalizeServiceBase("http://h:1/"), "http://h:1")
		self.assertEqual(ocrClient.normalizeServiceBase("http://h/paddle/ocr/"), "http://h/paddle/ocr")

	def test_invalid(self):
		for bad in ("", "   ", None, 42, "http://", "ftp://h/x"):
			self.assertEqual(ocrClient.normalizeServiceBase(bad), "", msg=repr(bad))


class ComposeUrlTests(unittest.TestCase):
	def test_basePlusModel(self):
		self.assertEqual(ocrClient.composeUrl("http://h:1", "/ocr"), "http://h:1/ocr")
		self.assertEqual(ocrClient.composeUrl("http://h:1/", "ocr"), "http://h:1/ocr")
		self.assertEqual(ocrClient.composeUrl("http://h/paddle", "/ocr"), "http://h/paddle/ocr")

	def test_modelAsFullUrlWins(self):
		self.assertEqual(
			ocrClient.composeUrl("http://h:1", "http://other:2/ocr"),
			"http://other:2/ocr",
		)
		self.assertEqual(ocrClient.composeUrl("", "http://other:2/ocr"), "http://other:2/ocr")

	def test_emptyModelKeepsLegacyBehavior(self):
		self.assertEqual(ocrClient.composeUrl("http://h:1", ""), "http://h:1/ocr")
		self.assertEqual(ocrClient.composeUrl("http://h:1/ocr", ""), "http://h:1/ocr")

	def test_invalidBase(self):
		self.assertEqual(ocrClient.composeUrl("", "/ocr"), "")
		self.assertEqual(ocrClient.composeUrl("ftp://h/x", "/ocr"), "")


class SplitServiceUrlTests(unittest.TestCase):
	CASES = (
		"http://127.0.0.1:23353",
		"http://127.0.0.1:23353/",
		"http://127.0.0.1:23353/ocr",
		"http://h:23353/ocr-acc",
		"http://h:23353/layout-parsing",
		"http://h/paddle/ocr",
		"127.0.0.1:23353",
		"https://ocr.example.com/ocr",
	)

	def test_roundTripsToLegacyUrl(self):
		"""迁移必须等价：拆完再组合 == 3.3.5 的 normalizeBaseUrl 结果。"""
		for raw in self.CASES:
			with self.subTest(raw=raw):
				base, model = ocrClient.splitServiceUrl(raw)
				self.assertEqual(
					ocrClient.composeUrl(base, model),
					ocrClient.normalizeBaseUrl(raw),
				)

	def test_expectedSplit(self):
		self.assertEqual(ocrClient.splitServiceUrl("http://h:1/ocr"), ("http://h:1", "/ocr"))
		self.assertEqual(ocrClient.splitServiceUrl("http://h/paddle/ocr"), ("http://h/paddle", "/ocr"))
		self.assertEqual(ocrClient.splitServiceUrl("http://h:1"), ("http://h:1", "/ocr"))
		self.assertEqual(
			ocrClient.splitServiceUrl("http://h:1/layout-parsing"),
			("http://h:1", "/layout-parsing"),
		)

	def test_invalid(self):
		base, model = ocrClient.splitServiceUrl("")
		self.assertEqual(base, "")
		self.assertEqual(model, "/ocr")


class ModelListParsingTests(unittest.TestCase):
	def test_openaiShapeWithEndpoint(self):
		body = {
			"object": "list",
			"data": [
				{"id": "pp-ocrv6-fast", "label": "PP-OCRv6", "endpoint": "/ocr"},
				{"id": "vl", "path": "/layout-parsing"},
			],
		}
		models = ocrClient.parseModelList(body)
		self.assertEqual([m["id"] for m in models], ["pp-ocrv6-fast", "vl"])
		self.assertEqual(models[0]["endpoint"], "/ocr")
		self.assertEqual(models[0]["label"], "PP-OCRv6")
		self.assertEqual(models[1]["endpoint"], "/layout-parsing")

	def test_pathLikeIdIsItsOwnEndpoint(self):
		models = ocrClient.parseModelList({"data": [{"id": "/custom-ocr"}]})
		self.assertEqual(models[0]["endpoint"], "/custom-ocr")

	def test_ollamaStyleWithoutEndpointIsDropped(self):
		"""Ollama 的 /api/tags 没有端点字段，无法路由，不能当作可用模型。"""
		self.assertEqual(
			ocrClient.parseModelList({"models": [{"name": "gemma4", "model": "gemma4"}]}),
			[],
		)

	def test_invalidBodies(self):
		for body in (None, [], {}, {"data": "x"}, {"data": [1, 2]}, {"data": [{}]}):
			self.assertEqual(ocrClient.parseModelList(body), [], msg=repr(body))

	def test_openapiFallbackExcludesNonOcrAndGetOnly(self):
		body = {
			"paths": {
				"/health": {"get": {}},
				"/ocr": {"post": {}},
				"/layout-parsing": {"post": {}},
				"/restructure-pages": {"post": {}},
				"/custom": {"put": {}},
			}
		}
		models = ocrClient.parseOpenApiModels(body)
		self.assertEqual([m["endpoint"] for m in models], ["/layout-parsing", "/ocr"])

	def test_openapiInvalidBodies(self):
		for body in (None, [], {}, {"paths": []}):
			self.assertEqual(ocrClient.parseOpenApiModels(body), [], msg=repr(body))


class ClientModelTests(unittest.TestCase):
	def test_baseUrlComposedFromModel(self):
		client = ocrClient.HttpOcrClient("http://h:1", model="/ocr-acc")
		self.assertEqual(client.base_url, "http://h:1/ocr-acc")
		self.assertEqual(client.service_base, "http://h:1")

	def test_legacyFullUrlStillWorks(self):
		client = ocrClient.HttpOcrClient("http://h:1/ocr")
		self.assertEqual(client.base_url, "http://h:1/ocr")

	def test_fullUrlModelWinsOverBase(self):
		client = ocrClient.HttpOcrClient("", model="http://other:2/ocr")
		self.assertEqual(client.base_url, "http://other:2/ocr")


class DiscoverModelsTests(unittest.TestCase):
	def client(self, server):
		return ocrClient.HttpOcrClient(server.service_base, model="/ocr", timeout=3.0)

	def test_modelsEndpointHasPriority(self):
		with StubServer() as server:
			server.setMode("models_ok")
			models, source, error = self.client(server).listModels()
		self.assertEqual(source, "models")
		self.assertEqual(error, "")
		self.assertEqual([m["id"] for m in models], ["pp-ocrv6-fast", "paddleocr-vl-1.6"])
		self.assertEqual(models[1]["endpoint"], "/layout-parsing")

	def test_v1ModelsFallback(self):
		with StubServer() as server:
			server.setMode("v1_models_ok")
			models, source, _error = self.client(server).listModels()
		self.assertEqual(source, "v1/models")
		self.assertEqual(len(models), 2)

	def test_unauthorized(self):
		with StubServer() as server:
			server.setMode("models_401")
			models, source, error = self.client(server).listModels()
		self.assertEqual(models, [])
		self.assertEqual(source, "")
		self.assertIn("Unauthorized", error)

	def test_openapiFallback(self):
		with StubServer() as server:
			server.setMode("openapi_ok")
			models, source, error = self.client(server).listModels()
		self.assertEqual(source, "openapi")
		self.assertEqual(error, "")
		self.assertEqual([m["endpoint"] for m in models], ["/layout-parsing", "/ocr"])

	def test_openapiGarbage(self):
		with StubServer() as server:
			server.setMode("openapi_garbage")
			models, _source, error = self.client(server).listModels()
		self.assertEqual(models, [])
		self.assertIn("Invalid JSON", error)

	def test_connectionRefused(self):
		client = ocrClient.HttpOcrClient("http://127.0.0.1:9", timeout=2.0)
		models, source, error = client.listModels()
		self.assertEqual(models, [])
		self.assertEqual(source, "")
		self.assertTrue(error)


class VerifyTokenTests(unittest.TestCase):
	def test_invalidPayloadMeansTokenAccepted(self):
		with StubServer() as server:
			server.setMode("probe_422")
			client = ocrClient.HttpOcrClient(server.service_base, model="/ocr", timeout=3.0)
			ok, detail = client.verifyToken()
		self.assertTrue(ok, detail)

	def test_unauthorized(self):
		with StubServer() as server:
			server.setMode("models_401")
			client = ocrClient.HttpOcrClient(server.service_base, model="/ocr", timeout=3.0)
			ok, detail = client.verifyToken()
		self.assertFalse(ok)
		self.assertIn("Unauthorized", detail)

	def test_endpointMissing(self):
		with StubServer() as server:
			server.setMode("probe_404")
			client = ocrClient.HttpOcrClient(server.service_base, model="/ocr-acc", timeout=3.0)
			ok, detail = client.verifyToken()
		self.assertFalse(ok)
		self.assertIn("Endpoint not found", detail)

	def test_connectionRefused(self):
		client = ocrClient.HttpOcrClient("http://127.0.0.1:9", timeout=2.0)
		ok, _detail = client.verifyToken()
		self.assertFalse(ok)


# -------------------------------------------------------------------- 在线用例


@unittest.skipUnless(LIVE_ENABLED, "LION_TEST_LIVE=0")
class LiveServiceTests(unittest.TestCase):
	"""对真实 PaddleOCR 服务跑一遍。服务不可达时跳过（不算失败）。"""

	@classmethod
	def setUpClass(cls):
		cls.token = liveToken()
		cls.client = ocrClient.HttpOcrClient(LIVE_URL, token=cls.token, timeout=30.0)
		try:
			ok, detail = cls.client.testConnection()
		except Exception as error:  # noqa: BLE001
			raise unittest.SkipTest("服务不可达：%s" % error) from error
		if not ok:
			raise unittest.SkipTest("服务不可达：%s" % detail)

	def _screenImage(self):
		from PIL import ImageGrab

		return ImageGrab.grab()

	def test_healthAndOcr(self):
		image = self._screenImage()
		raw = self.client.encodeImage(image)
		self.assertTrue(raw)
		result = self.client.runBytes(raw)
		self.assertEqual(result["code"], 100, result.get("data"))
		self.assertIsInstance(result["data"], list)
		for line in result["data"]:
			self.assertTrue(line["text"].strip(), "空文本必须被过滤")
			self.assertGreaterEqual(line["score"], 0.0)

	def test_wrongTokenGets401OrUnauthorized(self):
		bad = ocrClient.HttpOcrClient(LIVE_URL, token="definitely-wrong-token", timeout=30.0)
		result = bad.runBytes(self.client.encodeImage(self._screenImage()))
		self.assertIn(result["code"], (904, 902), result)
		self.assertIn("401", result["data"] + str(bad._getContext("status")))

	def test_unsupportedCapabilityGets400(self):
		if "23353" not in LIVE_URL:
			self.skipTest("仅针对本项目服务验证能力协商")
		# 直接绕过 buildPayload 下发图表识别开关，验证服务端 400 与 hint 提取
		client = ocrClient.HttpOcrClient(LIVE_URL, token=self.token, timeout=30.0)
		payload = client.buildPayload(client.encodeImage(self._screenImage()))
		payload["useChartRecognition"] = True
		status, result = client._postRaw(payload)
		self.assertEqual(status, 400, result)
		self.assertEqual(client._getContext("status"), 400)
		self.assertIn("Unsupported capability", str(client._getContext("message")))
		self.assertIn("layout-parsing", str(client._getContext("hint")))

	def test_scoreThresholdOnLiveService(self):
		client = ocrClient.HttpOcrClient(LIVE_URL, token=self.token, timeout=30.0, score_thresh=0.9)
		result = client.runBytes(client.encodeImage(self._screenImage()))
		self.assertEqual(result["code"], 100)
		for line in result["data"]:
			self.assertGreaterEqual(line["score"], 0.9)


if __name__ == "__main__":
	unittest.main(verbosity=2)
