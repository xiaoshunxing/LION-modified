# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 xiaoshunxing
"""测试用的 NVDA 环境桩（stub）。

插件的模块会 `import addonHandler` / `import config` 等 NVDA 内置模块，这些模块
只在 NVDA 进程内存在。这里用最小实现顶替它们，使 OCR 客户端与配置规格能在普通
Python 下离线测试。

用法（在测试模块里）::

    import nvda_stubs
    nvda_stubs.install()
    from lion import ocrClient
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, os.pardir, "src"))
PLUGIN_DIR = os.path.join(SRC, "globalPlugins")
LION_DIR = os.path.join(PLUGIN_DIR, "lion")

_installed = False
_packages = set()
_mocked_module_names = []


def package(name: str):
	"""把 `src/globalPlugins/<name>` 注册成一个**空**包并返回它。

	不执行插件的 `__init__.py`（那里会 import wx / gui / ui，普通 Python 下无法
	加载），但提供了 `__path__`，于是 `from lion import ocrClient` 能正常解析。
	"""
	install()
	if name in _packages:
		return sys.modules[name]
	path = os.path.join(PLUGIN_DIR, name)
	if not os.path.isdir(path):
		raise FileNotFoundError(path)
	spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
	module = importlib.util.module_from_spec(spec)
	module.__path__ = [path]
	module.__file__ = os.path.join(path, "__init__.py")
	sys.modules[name] = module
	_packages.add(name)
	return module


def mockModule(name: str, **attrs):
	"""注册一个被 mock 掉的模块，避免真实模块在被 import 时产生副作用。

	与 `_makeStub` 不同：这里记录名字，`removeMocks()` 会显式清理，
	适合 `wx`、`PIL` 这类"仅需存在"的依赖。
	"""
	from unittest import mock

	module = mock.MagicMock(**attrs)
	module.__name__ = name
	sys.modules[name] = module
	if name not in _mocked_module_names:
		_mocked_module_names.append(name)
	return module


def removeMocks() -> None:
	"""移除由 `mockModule` 注册的临时模块。"""
	for name in list(_mocked_module_names):
		sys.modules.pop(name, None)
	_mocked_module_names.clear()


class SpecStub:
	"""顶替 config.conf.spec：只记录注册过的 spec，不做校验。

	真正的校验交给 NVDA 自己的 configobj；测试里我们用
	`configobj.validate.Validator` 单独跑每个 spec 条目。
	"""

	def __init__(self):
		self.sections = {}

	def __setitem__(self, name, spec):
		self.sections[name] = spec

	def __getitem__(self, name):
		return self.sections[name]

	def get(self, name, default=None):
		return self.sections.get(name, default)


class ConfStub:
	"""顶替 config.conf：提供 spec 与可写的 [section] 映射。"""

	def __init__(self):
		self.spec = SpecStub()
		self._sections = {}

	def __getitem__(self, name):
		return self._sections.setdefault(name, {})

	def __contains__(self, name):
		return name in self._sections

	def getConfigValidation(self, path):
		raise NotImplementedError("配置校验在真实 NVDA 中由 configobj 提供")


class _LogHandlerStub(types.SimpleNamespace):
	def __init__(self):
		super().__init__(log=self, error=self._noop, warning=self._noop, info=self._noop, debug=self._noop, debugWarning=self._noop)

	@staticmethod
	def _noop(*args, **kwargs):
		return None

	def exception(self, *args, **kwargs):
		return None


def _identityTranslation(domain="nvda", localedir=None, languages=None, fallback=True):
	return types.SimpleNamespace(gettext=lambda message: message)


def _initTranslation():
	"""顶替 addonHandler.initTranslation()。

	在真实 NVDA 中，它把 gettext 函数写进**调用者模块**的全局命名空间：`_`、
	`ngettext` 等。桩里必须同样把 `_` 装到调用者模块，否则模块里的 `_("...")`
	会解析成 Python 在交互式环境下的 `_`（上一次表达式结果），产生随机的
	`KeyError` / `UnboundLocalError`。
	"""
	import inspect

	frame = inspect.currentframe()
	caller = frame.f_back if frame is not None else None
	module = inspect.getmodule(caller) if caller is not None else None
	if module is not None:
		module.__dict__.setdefault("_", lambda message: message)
		module.__dict__.setdefault("ngettext", lambda singular, plural, n: singular if n == 1 else plural)
		module.__dict__.setdefault("pgettext", lambda context, message: message)


def _makeStub(name, **attrs):
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	sys.modules[name] = module
	return module


def install() -> None:
	"""惰性创建桩模块并把 `src/globalPlugins` 放进 sys.path。"""
	global _installed
	if not _installed:
		if PLUGIN_DIR not in sys.path:
			sys.path.insert(0, PLUGIN_DIR)
		_installed = True

	if "addonHandler" not in sys.modules:
		_makeStub("addonHandler", initTranslation=_initTranslation)
	if "config" not in sys.modules:
		_makeStub("config", conf=ConfStub(), isAppX=False)
	if "logHandler" not in sys.modules:
		_makeStub("logHandler", log=_LogHandlerStub())
	if "gettext" not in sys.modules:
		sys.modules["gettext"] = __import__("gettext")
	if "translation" not in sys.modules:
		sys.modules["translation"] = types.SimpleNamespace(translation=_identityTranslation)


def conf() -> ConfStub:
	"""返回桩 config 模块的 conf 对象。"""
	install()
	return sys.modules["config"].conf

# --------------------------------------------------------------- configobj 来源
# 不往 conda 环境里装包、也不加载 NVDA package 内部的 configobj（那是 3.13 字节码，
# 在 3.12 下 import 会失败**并把失败状态留在 sys.modules 里**，污染后续用例）。
# 顺序：environment 已安装 -> 本目录 vendor/configobj。

VENDOR = os.path.join(HERE, "vendor")
NVDA_LIBRARY_ZIP = os.environ.get(
	"LION_NVDA_LIBRARY_ZIP",
	r"C:\Program Files\NVDA\library.zip",
)


def loadConfigObj():
	"""返回 `(ConfigObj, Validator, VdtTypeError, VdtValueError)`，全部不可用时返回 None。

	成功解析后结果会缓存；无论成功失败都会清理 `sys.modules["configobj"]`，
	避免半成品模块影响其它测试模块。
	"""
	if "configobj" in _configobjCache:
		return _configobjCache["configobj"]
	if os.path.isdir(VENDOR) and VENDOR not in sys.path:
		sys.path.append(VENDOR)
	result = None
	try:
		from configobj import ConfigObj
		from configobj.validate import Validator, VdtTypeError, VdtValueError

		result = (ConfigObj, Validator, VdtTypeError, VdtValueError)
	except Exception:  # noqa: BLE001 - 包括 ZipImportError 之类的加载失败
		sys.modules.pop("configobj", None)
		sys.modules.pop("configobj.validate", None)
	_configobjCache["configobj"] = result
	return result


_configobjCache: dict = {}
