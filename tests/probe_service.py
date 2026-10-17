# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""探测 PaddleOCR 服务与 LION 修改版客户端是否可用。

仅使用标准库；令牌从 `--token`、环境变量 `LION_TEST_TOKEN` 或
`LION_TEST_TOKEN_FILE` 指向的令牌文件读取。

用法::

    python probe_service.py                     # 探活 + 发一张屏幕截图跑一次 OCR
    python probe_service.py --url http://host:23353/ocr
    python probe_service.py --image shot.png
    python probe_service.py --json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, os.pardir, "src", "globalPlugins"))
if SRC not in sys.path:
	sys.path.insert(0, SRC)
sys.path.insert(0, HERE)

import nvda_stubs  # noqa: E402

nvda_stubs.install()
nvda_stubs.package("lion")

from lion import ocrClient  # noqa: E402

DEFAULT_TOKEN_FILE = ""


def resolveToken(explicit: str | None) -> str:
	if explicit:
		return explicit.strip()
	env = (os.environ.get("LION_TEST_TOKEN") or "").strip()
	if env:
		return env
	tokenFile = os.environ.get("LION_TEST_TOKEN_FILE", DEFAULT_TOKEN_FILE)
	if not tokenFile:
		return ""
	path = pathlib.Path(tokenFile)
	if path.is_file():
		return path.read_text(encoding="utf-8").strip()
	return ""


def grabScreen():
	from PIL import ImageGrab

	return ImageGrab.grab()


def loadImage(path: str | None):
	if path:
		from PIL import Image

		return Image.open(path)
	return grabScreen()


def main() -> int:
	parser = argparse.ArgumentParser(description="PaddleOCR 服务探测（LION 修改版）")
	parser.add_argument("--url", default="http://127.0.0.1:23353/ocr", help="服务地址")
	parser.add_argument("--token", default=None, help="访问令牌；默认读令牌文件")
	parser.add_argument("--image", default=None, help="用指定图片代替屏幕截图")
	parser.add_argument("--timeout", type=float, default=30.0)
	parser.add_argument("--json", action="store_true", help="以 JSON 输出")
	args = parser.parse_args()

	token = resolveToken(args.token)
	client = ocrClient.HttpOcrClient(args.url, token=token, timeout=args.timeout)
	report: dict = {
		"url": client.base_url,
		"healthUrl": ocrClient.healthUrl(client.base_url),
		"tokenProvided": bool(token),
	}

	start = time.perf_counter()
	ok, detail = client.testConnection(sendRequest=False)
	report["health"] = {"ok": ok, "detail": detail, "ms": round((time.perf_counter() - start) * 1000, 1)}

	if ok:
		image = loadImage(args.image)
		raw = client.encodeImage(image)
		report["image"] = {"size": list(image.size), "format": client.image_format, "bytes": len(raw)}
		start = time.perf_counter()
		result = client.runBytes(raw)
		elapsed = (time.perf_counter() - start) * 1000
		report["ocr"] = {
			"code": result["code"],
			"ms": round(elapsed, 1),
			"lines": len(result["data"]) if isinstance(result["data"], list) else 0,
			"sample": [line["text"] for line in result["data"][:10]] if isinstance(result["data"], list) else str(result["data"])[:300],
		}
	else:
		report["ocr"] = {"code": None, "error": "health check failed"}

	if args.json:
		print(json.dumps(report, ensure_ascii=False, indent=2))
		return 0 if report["ocr"].get("code") == 100 else 1

	print("服务地址   : %s" % report["url"])
	print("探活地址   : %s" % report["healthUrl"])
	print("携带令牌   : %s" % ("是" if token else "否"))
	print("健康检查   : %s（%s ms）" % (report["health"]["detail"], report["health"]["ms"]))
	if report["ocr"].get("code") == 100:
		print("OCR 调用   : code=100，%d 行，%.1f ms" % (report["ocr"]["lines"], report["ocr"]["ms"]))
		for text in report["ocr"]["sample"][:5]:
			print("    %s" % text)
		return 0
	print("OCR 调用   : 失败 -> %s" % report["ocr"])
	return 1


if __name__ == "__main__":
	raise SystemExit(main())
