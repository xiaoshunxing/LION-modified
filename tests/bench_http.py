# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""端到端基准：不同截图范围 × 图像格式下，LION 修改版一次 OCR 的耗时。

含 base64 编码与 HTTP 往返，与插件实际路径一致。用于验收"取词性能没退化"。

用法::

    python bench_http.py --repeat 5                       # 屏幕全屏 / 1080p / 字幕条，JPEG+PNG
    python bench_http.py --image shot.png --repeat 10
    python bench_http.py --json --out artifacts/bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
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
from probe_service import resolveToken  # noqa: E402


def regions(image):
	"""按屏幕尺寸切出三种典型识别范围。"""
	width, height = image.size
	cases = [("full", image)]
	if width >= 1280 and height >= 720:
		cases.append(("1080p", image.crop((0, 0, 1920, 1080))))
	# 字幕条：屏幕底部 25%，贴近插件裁剪 70% 上方后的实际范围
	cases.append(("subtitle-band", image.crop((0, int(height * 0.75), width, height))))
	cases.append(("subtitle-strip", image.crop((0, int(height * 0.80), width, int(height * 0.92)))))
	return cases


def benchOne(client, image, repeats: int):
	times = []
	result = None
	for _ in range(repeats):
		raw = client.encodeImage(image)
		start = time.perf_counter()
		result = client.runBytes(raw)
		times.append((time.perf_counter() - start) * 1000)
	ordered = sorted(times)
	return {
		"format": client.image_format,
		"size": list(image.size),
		"code": result["code"],
		"min": round(ordered[0], 1),
		"p50": round(statistics.median(ordered), 1),
		"max": round(ordered[-1], 1),
		"lines": len(result["data"]) if isinstance(result["data"], list) else 0,
	}


def main() -> int:
	parser = argparse.ArgumentParser(description="LION 修改版 OCR 端到端基准")
	parser.add_argument("--url", default="http://127.0.0.1:23353/ocr")
	parser.add_argument("--token", default=None)
	parser.add_argument("--image", default=None, help="用指定图片（默认抓屏）")
	parser.add_argument("--repeat", type=int, default=5)
	parser.add_argument("--json", action="store_true")
	parser.add_argument("--out", default=None, help="把结果 JSON 写到文件")
	args = parser.parse_args()

	from PIL import Image

	image = Image.open(args.image) if args.image else __import__("PIL.ImageGrab", fromlist=["grab"]).grab()
	token = resolveToken(args.token)
	results = []
	for name, region in regions(image):
		for fmt in ("JPEG", "PNG"):
			client = ocrClient.HttpOcrClient(args.url, token=token, timeout=60.0, image_format=fmt)
			entry = benchOne(client, region, max(1, args.repeat))
			entry["case"] = name
			results.append(entry)

	payload = {"url": args.url, "repeat": args.repeat, "results": results}
	if args.out:
		path = pathlib.Path(args.out)
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

	if args.json:
		print(json.dumps(payload, ensure_ascii=False, indent=2))
		return 0

	print("%-16s %-11s %-5s %9s %9s %9s %6s" % ("范围", "分辨率", "格式", "min(ms)", "p50(ms)", "max(ms)", "行数"))
	for entry in results:
		print(
			"%-16s %-11s %-5s %9.1f %9.1f %9.1f %6d"
			% (
				entry["case"],
				"%dx%d" % tuple(entry["size"]),
				entry["format"],
				entry["min"],
				entry["p50"],
				entry["max"],
				entry["lines"],
			)
		)
	return 0 if all(entry["code"] == 100 for entry in results) else 1


if __name__ == "__main__":
	raise SystemExit(main())
