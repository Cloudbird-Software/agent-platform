"""doctor：开箱自检——逐面检查并给出修复动作（人读表格 + JSON 双输出）。

检查面（按依赖顺序）：
  python      版本 >= 3.11（requires-python 下界）
  core        agentplatform 核心可导入（uv sync 即可）
  workspace   渲染产物存在且 manifest 自校验
  env         manifest.env_refs 全部已设置（.env 或进程环境）
  drift       声明面 ↔ 渲染面 ↔ 磁盘三方一致（registry 可用时）
  state       state 账本链完整（存在时）
  runtime     openjiuwen/jiuwenswarm 可导入（adapter 执行面需要；
              未装仅降级——治理/观测/漂移面不受影响）
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from agentplatform.bootstrap.dotenv import load_env_file
from agentplatform.render.manifest import MANIFEST_NAME, load_manifest

DOCTOR_PASS, DOCTOR_WARN, DOCTOR_FAIL = "pass", "warn", "fail"


def _check_python() -> dict[str, Any]:
    ok = sys.version_info >= (3, 11)
    return {
        "name": "python",
        "status": DOCTOR_PASS if ok else DOCTOR_FAIL,
        "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "fix": "" if ok else "升级 Python >= 3.11",
    }


def _check_core() -> dict[str, Any]:
    ok = importlib.util.find_spec("agentplatform") is not None
    return {
        "name": "core",
        "status": DOCTOR_PASS if ok else DOCTOR_FAIL,
        "detail": "agentplatform 可导入" if ok else "不可导入",
        "fix": "" if ok else "uv sync（核心依赖）",
    }


def _check_workspace(workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return {
            "name": "workspace",
            "status": DOCTOR_WARN,
            "detail": "未指定",
            "fix": "ap init --registry ... --out ...",
        }
    try:
        m = load_manifest(workspace)
        return {
            "name": "workspace",
            "status": DOCTOR_PASS,
            "detail": f"manifest 自校验通过（{len(m.files)} 文件，spec {m.spec_digest[:8]}…）",
            "fix": "",
        }
    except FileNotFoundError:
        return {
            "name": "workspace",
            "status": DOCTOR_FAIL,
            "detail": f"{workspace} 无 {MANIFEST_NAME}",
            "fix": "ap init --registry ... --out " + str(workspace),
        }
    except ValueError as e:
        return {
            "name": "workspace",
            "status": DOCTOR_FAIL,
            "detail": str(e),
            "fix": "重新渲染（manifest 被篡改？）",
        }


def _check_env(workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return {"name": "env", "status": DOCTOR_WARN, "detail": "未指定 workspace", "fix": ""}
    try:
        m = load_manifest(workspace)
    except (FileNotFoundError, ValueError):
        return {"name": "env", "status": DOCTOR_WARN, "detail": "workspace 未就绪", "fix": ""}
    if not m.env_refs:
        return {"name": "env", "status": DOCTOR_PASS, "detail": "渲染产物无需凭据", "fix": ""}
    dotenv = load_env_file(workspace / ".env")
    missing = [v for v in m.env_refs if not os.environ.get(v) and not dotenv.get(v)]
    if missing:
        return {
            "name": "env",
            "status": DOCTOR_FAIL,
            "detail": f"缺 {len(missing)}/{len(m.env_refs)}：{missing[:5]}",
            "fix": f"填 {workspace}/.env（模板：ap envfile --workspace {workspace}）",
        }
    return {"name": "env", "status": DOCTOR_PASS, "detail": f"{len(m.env_refs)} 项凭据齐备", "fix": ""}


def _check_drift(registry: Path | None, workspace: Path | None) -> dict[str, Any]:
    if registry is None or workspace is None:
        return {"name": "drift", "status": DOCTOR_WARN, "detail": "registry/workspace 未指定", "fix": ""}
    from agentplatform.drift.checker import check_workspace

    report = check_workspace(registry, workspace)
    if report.ok:
        return {"name": "drift", "status": DOCTOR_PASS, "detail": "三方一致", "fix": ""}
    kinds = {i.kind for i in report.issues}
    return {
        "name": "drift",
        "status": DOCTOR_FAIL,
        "detail": f"{len(report.issues)} 处漂移（{sorted(kinds)}）",
        "fix": f"重渲染：ap render --registry {registry} --out {workspace}",
    }


def _check_state(workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return {"name": "state", "status": DOCTOR_WARN, "detail": "未指定 workspace", "fix": ""}
    state = workspace / "state"
    if not (state / "ledger.jsonl").is_file():
        return {
            "name": "state",
            "status": DOCTOR_WARN,
            "detail": "state 未初始化（干预/观测需要）",
            "fix": "ap init（state 随 workspace 初始化创建）",
        }
    from agentplatform.observe.store import RuntimeStore, StoreError

    try:
        RuntimeStore.open(state)
        return {"name": "state", "status": DOCTOR_PASS, "detail": "账本链完整", "fix": ""}
    except StoreError as e:
        return {
            "name": "state",
            "status": DOCTOR_FAIL,
            "detail": str(e),
            "fix": "账本被篡改——检查 workspace/state/ledger.jsonl",
        }


def _check_runtime() -> dict[str, Any]:
    try:
        import jiuwenswarm  # noqa: F401
        import openjiuwen  # noqa: F401

        return {
            "name": "runtime",
            "status": DOCTOR_PASS,
            "detail": "openjiuwen/jiuwenswarm 可导入",
            "fix": "",
        }
    except ImportError:
        return {
            "name": "runtime",
            "status": DOCTOR_WARN,
            "detail": "openjiuwen/jiuwenswarm 未装（adapter 执行面不可用；治理/观测/漂移不受影响）",
            "fix": "make setup-runtime（runtime/requirements.lock 钉版安装）",
        }


def run_doctor(
    *,
    registry: str | Path | None = None,
    workspace: str | Path | None = None,
    human: bool = True,
) -> dict[str, Any]:
    """全清单自检。返回 {checks: [...], ok: bool}；human=True 时同时打印表格。"""
    ws = Path(workspace) if workspace else None
    reg = Path(registry) if registry else None
    checks = [
        _check_python(),
        _check_core(),
        _check_workspace(ws),
        _check_env(ws),
        _check_drift(reg, ws),
        _check_state(ws),
        _check_runtime(),
    ]
    ok = all(c["status"] != DOCTOR_FAIL for c in checks)
    result = {"ok": ok, "checks": checks}
    if human:
        _print_table(result)
    return result


def _print_table(result: dict[str, Any]) -> None:
    icon = {DOCTOR_PASS: "[ok]  ", DOCTOR_WARN: "[warn]", DOCTOR_FAIL: "[FAIL]"}
    print("doctor ── 开箱自检")
    for c in result["checks"]:
        line = f"  {icon[c['status']]} {c['name']:<10} {c['detail']}"
        print(line)
        if c["fix"]:
            print(f"         fix: {c['fix']}")
    print("  " + ("一切就绪" if result["ok"] else "存在 FAIL 项——按 fix 列修复"))
