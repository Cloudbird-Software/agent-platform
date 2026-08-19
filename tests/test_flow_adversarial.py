"""flow/ 对抗测试：坏图（G 规则）+ 坏脚本（D 规则）+ 产物指纹敏感性。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from agentplatform.flow import lint_workflow_source, load_phase_graph, validate_graph
from agentplatform.flow.codegen import compile_team_flow
from agentplatform.spec import RegistryLoader
from agentplatform.spec.errors import SpecError

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"


def _reg(tmp_path: Path) -> Path:
    dst = tmp_path / "reg"
    shutil.copytree(FIXTURE, dst)
    return dst


def _tc_path(reg: Path) -> Path:
    return reg / "standards" / "team-collaboration.yaml"


def _edit_tc(reg: Path, mutate) -> None:
    path = _tc_path(reg)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _rules(issues) -> set[str]:
    return {i.rule for i in issues}


# ---- A1 图破坏 ----

def test_a1_missing_own_exit_edge_detected(tmp_path: Path) -> None:
    reg = _reg(tmp_path)

    def drop(m):
        m["flow"]["phases"]["graph"] = [
            e for e in m["flow"]["phases"]["graph"] if not (e["from"] == "build" and e["to"] == "verify")
        ]

    _edit_tc(reg, drop)
    rules = _rules(validate_graph(load_phase_graph(reg)))
    assert "G3" in rules  # build 无专属出边（any 边不计数）


def test_a1_wildcard_edge_does_not_satisfy_g3(tmp_path: Path) -> None:
    reg = _reg(tmp_path)

    def only_wildcard(m):
        # verify 的专属出边全部拿掉，只剩 any 通配出边——恒真边不能消除死锁
        m["flow"]["phases"]["graph"] = [
            e for e in m["flow"]["phases"]["graph"] if e["from"] != "verify"
        ] + [
            {"from": "any", "to": "integrate", "when": "reverted"},  # 保可达（any 通配）
            {"from": "integrate", "to": "handoff", "when": "reverted"},  # integrate 保专属出边
        ]

    _edit_tc(reg, only_wildcard)
    rules = _rules(validate_graph(load_phase_graph(reg)))
    assert "G3" in rules
    assert "G5" not in rules  # 只验 G3 语义（可达性另测）


def test_a2_dangling_event_detected(tmp_path: Path) -> None:
    reg = _reg(tmp_path)

    def ghost(m):
        m["flow"]["phases"]["graph"][0]["when"] = "cards.ratified AND ghost.event"

    _edit_tc(reg, ghost)
    issues = validate_graph(load_phase_graph(reg))
    assert any(i.rule == "G6" and "ghost.event" in i.message for i in issues)


def test_a3_unreachable_phase(tmp_path: Path) -> None:
    reg = _reg(tmp_path)

    def isolate(m):
        m["flow"]["phases"]["graph"] = [
            e for e in m["flow"]["phases"]["graph"] if e["to"] != "verify" and e["from"] != "verify"
        ] + [
            {"from": "integrate", "to": "handoff", "when": "reverted"},  # 补 integrate 专属出边
        ]

    _edit_tc(reg, isolate)
    rules = _rules(validate_graph(load_phase_graph(reg)))
    assert "G5" in rules  # verify 孤立


def test_a4_unknown_phase_in_edge(tmp_path: Path) -> None:
    reg = _reg(tmp_path)

    def ghost_phase(m):
        m["flow"]["phases"]["graph"][0]["to"] = "deploy"

    _edit_tc(reg, ghost_phase)
    rules = _rules(validate_graph(load_phase_graph(reg)))
    assert "G2" in rules


def test_a5_structural_fail_closed(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    (_tc_path(reg)).write_text("flow: {phases: {graph: []}}\n", encoding="utf-8")
    with pytest.raises(SpecError):
        load_phase_graph(reg)


# ---- A6 坏脚本 lint ----

_GOOD = '''\
META = {"name": "t", "phases": ["p"]}
async def run(args):
    from swarmflow import agent, phase
    phase("p")
    await agent("hi", label="a", phase="p")
'''


def test_a6_lint_passes_good() -> None:
    assert lint_workflow_source(_GOOD) == []


def test_a6_syntax_error() -> None:
    assert any(i.startswith("D1") for i in lint_workflow_source(_GOOD[:-2]))


def test_a6_dangerous_import() -> None:
    bad = "import os\n" + _GOOD
    assert any("D3" in i and "os" in i for i in lint_workflow_source(bad))


def test_a6_non_whitelist_operator() -> None:
    bad = _GOOD.replace("from swarmflow import agent, phase", "from swarmflow import agent, rm_rf")
    assert any("D2" in i and "rm_rf" in i for i in lint_workflow_source(bad))


def test_a6_dangerous_call() -> None:
    bad = _GOOD.replace('await agent("hi", label="a", phase="p")', 'eval("x")')
    assert any("D3" in i and "eval" in i for i in lint_workflow_source(bad))


def test_a6_missing_meta_and_run() -> None:
    issues = lint_workflow_source("X = 1\n")
    assert any(i.startswith("D4") for i in issues)


def test_a6_phase_not_in_meta() -> None:
    bad = _GOOD.replace('phase("p")', 'phase("ghost")')
    assert any("D5" in i for i in lint_workflow_source(bad))


def test_a6_duplicate_label() -> None:
    bad = _GOOD.replace(
        'await agent("hi", label="a", phase="p")',
        'await agent("hi", label="a", phase="p")\n    await agent("hi2", label="a", phase="p")',
    )
    assert any("D6" in i for i in lint_workflow_source(bad))


def test_a6_import_outside_swarmflow() -> None:
    bad = _GOOD.replace("from swarmflow import agent, phase", "from json import dumps")
    assert any("D2" in i for i in lint_workflow_source(bad))


# ---- A7 编译产物指纹敏感性（漂移检测的伏笔） ----

def test_a7_compiled_digest_tracks_spec(tmp_path: Path) -> None:
    from agentplatform.spec.fingerprint import sha256_hex

    reg = _reg(tmp_path)
    snap = RegistryLoader().load(reg)
    g = load_phase_graph(reg)
    d1 = sha256_hex(compile_team_flow(snap, snap.teams["mini-wave"], g))

    # 声明面一动（goal 变更），产物摘要必须变
    team_path = reg / "registry" / "teams" / "mini-wave.yaml"
    doc = yaml.safe_load(team_path.read_text(encoding="utf-8"))
    doc["goal"] = "变更后的目标"
    team_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

    snap2 = RegistryLoader().load(reg)
    d2 = sha256_hex(compile_team_flow(snap2, snap2.teams["mini-wave"], g))
    assert d1 != d2


def test_a7_compiled_digest_tracks_graph(tmp_path: Path) -> None:
    from agentplatform.spec.fingerprint import sha256_hex

    reg = _reg(tmp_path)
    snap = RegistryLoader().load(reg)
    g1 = load_phase_graph(reg)
    d1 = sha256_hex(compile_team_flow(snap, snap.teams["mini-wave"], g1))

    def add_edge(m):
        m["flow"]["phases"]["graph"].append({"from": "plan", "to": "build", "when": "reverted"})

    _edit_tc(reg, add_edge)
    g2 = load_phase_graph(reg)
    d2 = sha256_hex(compile_team_flow(snap, snap.teams["mini-wave"], g2))
    assert d1 != d2  # 相位图变化 → 产物变化（META.phase_graph_digest 嵌入）
