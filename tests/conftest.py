"""全局测试夹具。

env 隔离（autouse）：run_up/apply_env 会按 CLI 语义改写 os.environ
（.env 注入进程环境），测试间必须隔离——否则前一个用例填的假凭据
会让后面"凭据缺失"类对抗用例静默变绿。
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
