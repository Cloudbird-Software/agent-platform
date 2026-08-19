"""drift/ 功能+对抗测试：三方对账与漂移监控。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from agentplatform.drift import check_workspace
from agentplatform.drift.checker import watch
from agentplatform.render import Renderer
from agentplatform.spec import RegistryLoader

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"


@pytest.fixture()
def env(tmp_path: Path):
    """渲染好的 registry + workspace 环境。"""
    reg = tmp_path / "reg"
    shutil.copytree(FIXTURE, reg)
    ws = tmp_path / "ws"
    snap = RegistryLoader().load(reg)
    Renderer().render(snap, ws)
    return reg, ws


def _kinds(report) -> set[str]:
    return {i.kind for i in report.issues}


# ---- 功能 ----


def test_clean_workspace_passes(env) -> None:
    reg, ws = env
    report = check_workspace(reg, ws)
    assert report.ok
    assert report.checked_files >= 2  # config.yaml + swarmflow/mini-wave.py
    assert report.spec_digest_declared == report.spec_digest_rendered


def test_render_includes_flow_outputs(env) -> None:
    _, ws = env
    assert (ws / "swarmflow" / "mini-wave.py").is_file()
    assert (ws / "config.yaml").is_file()
    manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert "swarmflow/mini-wave.py" in manifest["files"]


def test_idempotent_render_stays_clean(env) -> None:
    reg, ws = env
    snap = RegistryLoader().load(reg)
    Renderer().render(snap, ws)
    assert check_workspace(reg, ws).ok


# ---- 对抗：各类漂移注入 ----


def test_file_modified_detected(env) -> None:
    reg, ws = env
    cfg = ws / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n# 手改\n", encoding="utf-8")
    report = check_workspace(reg, ws)
    assert not report.ok
    assert any(i.kind == "file" and "config.yaml" in i.detail for i in report.issues)


def test_file_deleted_detected(env) -> None:
    reg, ws = env
    (ws / "swarmflow" / "mini-wave.py").unlink()
    report = check_workspace(reg, ws)
    assert "file" in _kinds(report)


def test_orphan_injection_detected(env) -> None:
    reg, ws = env
    (ws / "evil.py").write_text("import os\n", encoding="utf-8")
    report = check_workspace(reg, ws)
    assert any(i.kind == "orphan" and "evil.py" in i.detail for i in report.issues)


def test_pycache_not_orphan(env) -> None:
    """回归：live 执行 import 渲染脚本产生 __pycache__/*.pyc——运行时
    字节码缓存不是漂移（误报则用户跑一次 live 后 doctor 永远黄）。"""
    reg, ws = env
    cache = ws / "swarmflow" / "__pycache__"
    cache.mkdir()
    (cache / "mini-wave.cpython-312.pyc").write_bytes(b"\x00pyc")
    report = check_workspace(reg, ws)
    assert not any("pycache" in i.detail or ".pyc" in i.detail for i in report.issues)


def test_spec_change_detected(env) -> None:
    reg, ws = env
    team = reg / "registry" / "teams" / "mini-wave.yaml"
    doc = yaml.safe_load(team.read_text(encoding="utf-8"))
    doc["goal"] = "新目标——声明面已动"
    team.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report = check_workspace(reg, ws)
    assert "spec" in _kinds(report)


def test_manifest_tamper_detected(env) -> None:
    reg, ws = env
    # 篡改 manifest 记录的哈希（伪造账本）——output_digest 自校验兜底
    p = ws / "manifest.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["files"]["config.yaml"] = "f" * 64
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    report = check_workspace(reg, ws)
    assert "manifest" in _kinds(report)


def test_no_manifest_reported(tmp_path: Path) -> None:
    report = check_workspace(tmp_path, tmp_path / "empty-ws")
    assert not report.ok
    assert "manifest" in _kinds(report)


def test_runtime_owned_paths_exempt(env) -> None:
    reg, ws = env
    (ws / "state").mkdir()
    (ws / "state" / "ledger.jsonl").write_text("{}", encoding="utf-8")
    assert check_workspace(reg, ws).ok  # 运行时自有路径不算孤儿


# ---- watch ----


def test_watch_emits_round_events(env) -> None:
    reg, ws = env
    events = watch(reg, ws, interval_s=0, max_rounds=2, sleeper=lambda _s: None, clock=lambda: 0.0)
    assert len(events) == 2
    assert all(e["type"] == "drift.round" and e["ok"] for e in events)


def test_watch_captures_midstream_tamper(env) -> None:
    reg, ws = env
    rounds: list[dict] = []

    def sleeper(_s: float) -> None:
        cfg = ws / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "#x", encoding="utf-8")

    events = watch(reg, ws, interval_s=0, max_rounds=2, sleeper=sleeper, clock=lambda: 0.0)
    rounds.extend(events)
    assert events[0]["ok"]  # 第一轮干净
    assert not events[1]["ok"]  # sleep 期间被篡改——第二轮捕获
    assert any("config.yaml" in d for d in events[1]["issues"])


def test_cli_drift_check_exit_codes(env, capsys) -> None:
    from agentplatform.cli import main

    reg, ws = env
    assert main(["drift", "check", "--registry", str(reg), "--workspace", str(ws)]) == 0
    cfg = ws / "config.yaml"
    cfg.write_text("tampered", encoding="utf-8")
    assert main(["drift", "check", "--registry", str(reg), "--workspace", str(ws)]) == 1
