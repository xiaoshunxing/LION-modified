# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""把 `src/doc/<lang>/readme.md` 转成 NVDA 插件文档用的 readme.html。

不引入第三方 markdown 库；NVDA 自带的 `l10nUtil md2html` 只认它自己的文档类型，
所以这里只实现我们文档实际用到的构件：标题、表格、有序/无序列表、段落、
行内粗体/代码/链接/转义竖线。

用法::

    python make_readme_html.py                       # 转换默认 src/doc 下的 zh_CN 与 en
    python make_readme_html.py --src variants/lite/src  # 转换指定插件源的文档
"""

from __future__ import annotations

import argparse
import html
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.abspath(os.path.join(HERE, os.pardir, "src"))
LANGS = {"zh_CN": "zh-CN", "en": "en"}
TITLE = "LION modified"

_INLINE = (
	(re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
	(re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
	(re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
)

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ORDERED = re.compile(r"^\d+\.\s+(.*)$")
_UNORDERED = re.compile(r"^[-*]\s+(.*)$")
_TABLE_ROW = re.compile(r"^\|(.*)\|\s*$")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|\s*$")


def inline(text: str) -> str:
	"""先转义再还原行内标记，避免把 &、<、> 直接写进 HTML。"""
	escaped = html.escape(text, quote=False)
	for pattern, replacement in _INLINE:
		escaped = pattern.sub(replacement, escaped)
	return escaped


def splitRow(line: str) -> list[str]:
	cells = _TABLE_ROW.match(line).group(1).split("|")
	return [cell.strip().replace("\\|", "|") for cell in cells]


def convert(markdown: str, lang: str) -> str:
	lines = markdown.replace("\r\n", "\n").split("\n")
	out: list[str] = []
	paragraph: list[str] = []
	listItems: list[str] = []
	listTag: str | None = None
	tableRows: list[list[str]] = []

	def flushParagraph():
		if paragraph:
			out.append("<p>%s</p>" % inline(" ".join(paragraph).strip()))
			paragraph.clear()

	def flushList():
		nonlocal listTag
		if listItems and listTag:
			body = "\n".join("<li>%s</li>" % inline(item) for item in listItems)
			out.append("<%s>\n%s\n</%s>" % (listTag, body, listTag))
		listItems.clear()
		listTag = None

	def flushTable():
		if not tableRows:
			return
		header, *rows = tableRows
		parts = ["<table>", "<thead>", "<tr>"]
		parts += ["<th>%s</th>" % inline(cell) for cell in header]
		parts += ["</tr>", "</thead>", "<tbody>"]
		for row in rows:
			parts.append("<tr>")
			parts += ["<td>%s</td>" % inline(cell) for cell in row]
			parts.append("</tr>")
		parts += ["</tbody>", "</table>"]
		out.append("\n".join(parts))
		tableRows.clear()

	for line in lines:
		stripped = line.strip()

		if _TABLE_ROW.match(stripped):
			if _TABLE_SEP.match(stripped):
				continue  # 表头分隔行
			flushParagraph()
			flushList()
			tableRows.append(splitRow(stripped))
			continue
		flushTable()

		if not stripped:
			flushParagraph()
			flushList()
			continue

		heading = _HEADING.match(stripped)
		if heading:
			flushParagraph()
			flushList()
			level = len(heading.group(1))
			out.append("<h%d>%s</h%d>" % (level, inline(heading.group(2)), level))
			continue

		ordered = _ORDERED.match(stripped)
		unordered = _UNORDERED.match(stripped)
		if ordered or unordered:
			flushParagraph()
			tag = "ol" if ordered else "ul"
			if listTag and listTag != tag:
				flushList()
			listTag = tag
			listItems.append((ordered or unordered).group(1))
			continue

		flushList()
		paragraph.append(stripped)

	flushParagraph()
	flushList()
	flushTable()

	return """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" type="text/css" href="../style.css" media="screen">
<title>{title}</title>
</head>
<body>
{body}
</body>
</html>
""".format(lang=LANGS[lang], title=html.escape(TITLE), body="\n".join(out))


def readVersion(srcDir: str) -> str:
	"""从目标插件源的 manifest.ini 读取版本号。"""
	manifestPath = os.path.join(srcDir, "manifest.ini")
	try:
		with open(manifestPath, encoding="utf-8") as handle:
			for line in handle:
				if line.strip().startswith("version"):
					return line.split("=", 1)[1].strip()
	except OSError:
		pass
	return ""


def main() -> int:
	parser = argparse.ArgumentParser(description="把插件源的 doc/<lang>/readme.md 转成 readme.html")
	parser.add_argument("--src", default=DEFAULT_SRC, help="插件源目录（含 manifest.ini 与 doc/）")
	args = parser.parse_args()
	global TITLE
	version = readVersion(args.src)
	TITLE = ("LION modified " + version).strip()
	docDir = os.path.join(os.path.abspath(args.src), "doc")
	for lang in LANGS:
		mdPath = os.path.join(docDir, lang, "readme.md")
		htmlPath = os.path.join(docDir, lang, "readme.html")
		with open(mdPath, encoding="utf-8") as handle:
			markdown = handle.read()
		result = convert(markdown, lang)
		with open(htmlPath, "w", encoding="utf-8", newline="\n") as handle:
			handle.write(result)
		print("wrote %s (%d bytes)" % (htmlPath, len(result.encode("utf-8"))))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
