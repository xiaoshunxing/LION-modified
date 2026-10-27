# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""`scripts/make_readme_html.py` 的单元测试。

重点覆盖 3.3.8 新增的图片支持：插件手册文末的 dsh 徽章是
`[![alt](图片URL)](项目URL)`（图片套链接），转换器必须输出
`<a href="项目URL"><img src="图片URL" alt="alt" width="121" height="20"></a>`，
同时不影响普通链接与 HTML 转义。

运行::

    python -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
SCRIPT = os.path.join(ROOT, "scripts", "make_readme_html.py")

_spec = importlib.util.spec_from_file_location("lion_make_readme_html", SCRIPT)
converter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(converter)

PROJECT_URL = "https://github.com/deepseek-ai/deepseek-harness"
SHIELDS_URL = (
	"https://img.shields.io/badge/powered_by-dsh-4D6BFE"
	"?style=flat-square&logo=deepseek&logoColor=white"
)


class InlineTests(unittest.TestCase):
	def testLinkedBadgeBecomesLinkedImage(self):
		markdown = f"[![powered by dsh]({SHIELDS_URL})]({PROJECT_URL})"
		html = converter.inline(markdown)
		self.assertIn(f'<a href="{PROJECT_URL}">', html)
		self.assertIn('<img src="', html)
		self.assertIn("width=\"121\"", html)
		self.assertIn("height=\"20\"", html)
		self.assertIn('alt="powered by dsh"', html)
		# URL 里的 & 必须转义成 &amp; 才符合 HTML
		self.assertIn("&amp;logo=deepseek", html)
		self.assertNotIn("[![", html)

	def testPlainImageBecomesImage(self):
		html = converter.inline("[![logo](https://example.com/badge.png)](https://example.com/)")
		self.assertIn("<img", html)
		self.assertIn('alt="logo"', html)
		self.assertIn(f'href="https://example.com/"', html)

	def testPlainLinkUnchanged(self):
		html = converter.inline("[手册](https://example.com/doc)")
		self.assertEqual(html, '<a href="https://example.com/doc">手册</a>')

	def testImageOnly(self):
		html = converter.inline(f"![](https://img.shields.io/x.png)")
		self.assertIn("<img", html)
		self.assertNotIn("<a ", html)


class ConvertTests(unittest.TestCase):
	def testBadgeAtEndOfDocument(self):
		markdown = "# 标题\n\n正文。\n\n" + f"[![powered by dsh]({SHIELDS_URL})]({PROJECT_URL})\n"
		html = converter.convert(markdown, "zh_CN")
		self.assertIn("powered_by-dsh-4D6BFE", html)
		self.assertIn(PROJECT_URL, html)
		self.assertTrue(html.rstrip().endswith("</html>"))

	def testEscapingStillWorks(self):
		html = converter.inline("a < b & c")
		self.assertIn("a &lt; b &amp; c", html)


if __name__ == "__main__":
	unittest.main(verbosity=2)
