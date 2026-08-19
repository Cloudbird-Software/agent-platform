"""flow/ 功能测试：相位图加载/验证 + 编译确定性 + dry-run 全链路。"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from agentplatform.flow import (
    compile_team_flow,
    flow_outputs,
    lint_workflow_source,
    load_phase_graph,
    validate_graph,
)
from agentplatform.spec import RegistryLoader

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"
REAL_REGISTRY = Path(__file__).parent.parent.parent


@pytest.fixture()
def reg(tmp_path: Path) -> Path:
    dst = tmp_path / "reg"
    shutil.copytree(FIXTURE, dst)
    return dst


def _graph(reg: Path):
    return load_phase_graph(reg)


# ---- graph ----


def test_load_phase_graph_structure(reg: Path) -> None:
    g = _graph(reg)
    assert g.phases == ("plan", "build", "verify", "integrate", "handoff")
    assert len(g.edges) == 6
    assert "cards.ratified" in g.producers
    assert g.terminal() == "handoff"
    assert len(g.digest) == 64


def test_validate_graph_passes(reg: Path) -> None:
    assert validate_graph(_graph(reg)) == []


def test_events_in_when_extraction() -> None:
    from agentplatform.flow.graph import events_in_when

    # 事件 / 状态表达式 / 函数调用（含实参）/ 比较右侧值 / 裸词事件
    assert "cards.ratified" in events_in_when("cards.ratified AND planner.state == exited")
    assert "planner.state" not in events_in_when("cards.ratified AND planner.state == exited")
    assert "exited" not in events_in_when("cards.ratified AND planner.state == exited")
    assert "wave" not in events_in_when("no_release_face(wave)")
    assert "no_release_face" not in events_in_when("no_release_face(wave)")
    assert "reverted" in events_in_when("released_behind_flag OR reverted")


# ---- codegen ----


def test_compile_team_flow_deterministic(reg: Path) -> None:
    snap = RegistryLoader().load(reg)
    g = _graph(reg)
    team = snap.teams["mini-wave"]
    src1 = compile_team_flow(snap, team, g)
    src2 = compile_team_flow(snap, team, g)
    assert src1 == src2  # 同快照+图 → 字节级相同


def test_compile_structure(reg: Path) -> None:
    snap = RegistryLoader().load(reg)
    g = _graph(reg)
    src = compile_team_flow(snap, team=snap.teams["mini-wave"], graph=g)
    tree = ast.parse(src)
    names = {n.id for n in tree.body if isinstance(n, ast.Assign) for n in n.targets}
    assert "META" in names
    assert "SCHEMA_WAVE_PLAN" in names
    meta = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign) and any(t.id == "META" for t in n.targets)
    )
    assert meta["name"] == "mini-wave"
    assert meta["phases"] == list(g.phases)
    assert meta["source"]["spec_digest"] == snap.digest
    assert meta["source"]["phase_graph_digest"] == g.digest
    # 机制面：确定性判卷 + 预算收束 + 返工上限
    assert "_merge_verdict" in src
    assert "budget.remaining()" in src
    assert "while attempt <" in src
    for p in g.phases:
        assert f'phase("{p}")' in src


def test_flow_outputs_skips_non_renderable(reg: Path) -> None:
    snap = RegistryLoader().load(reg)
    outs = flow_outputs(snap, _graph(reg))
    assert list(outs) == ["swarmflow/mini-wave.py"]
    for src in outs.values():
        assert lint_workflow_source(src) == []


def test_compiled_workflow_lint_clean(reg: Path) -> None:
    snap = RegistryLoader().load(reg)
    src = compile_team_flow(snap, snap.teams["mini-wave"], _graph(reg))
    assert lint_workflow_source(src) == []


# ---- dry-run ----


def test_dryrun_registry_clean(reg: Path) -> None:
    from agentplatform.flow import dryrun_registry

    report = dryrun_registry(reg)
    assert report["graph_issues"] == []
    assert all(not v for v in report["teams"].values())


# ---- 真实 registry 冒烟 ----


@pytest.mark.skipif(not (REAL_REGISTRY / "registry").is_dir(), reason="真实 registry 不在预期路径")
def test_real_registry_end_to_end() -> None:
    from agentplatform.flow import dryrun_registry

    report = dryrun_registry(REAL_REGISTRY)
    assert report["graph_issues"] == []
    compiled: dict = report["teams"]  # type: ignore[assignment]
    assert len(compiled) >= 3  # dev-wave / incident-cell / stewardship
    assert all(not issues for issues in compiled.values())
