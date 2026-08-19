"""codegen：PhaseGraph + team 声明 → SwarmFlow 脚本（确定性编译）。

产物形态（TUI使用SwarmFlow指南）：顶层 META + async def run(args)，
算子 `from swarmflow import ...`（运行时映射 openjiuwen facade）。

忠实性决策（声明 → SwarmFlow 的映射）：
- 相位状态机的 phase_order → 脚本 phase() 序列；回边（verify→build
  changes_requested）→ 返工 while 回路（retries 上限 = 声明的承诺必须有边，
  否则返工振荡无终止条件）；
- 机制原型（gate/verdict/merge/wave.frozen）→ 确定性 Python 函数 +
  schema 约束的 worker 结构化输出——LLM 只产事实，pass/fail 由代码计算；
- 预算熔断 → budget.remaining() < FLOOR 时收束（budget.enforcement 落地面）；
- 非波次团队（topology 非 leader-teammate 波次）→ 顺序骨架（相位标记+在场
  座位逐相位 agent 调用），编排自由度留给声明指定的 single-seat/常驻形态。
"""

from __future__ import annotations

from typing import Any

import agentplatform
from agentplatform.flow.graph import PhaseGraph
from agentplatform.spec import SpecSnapshot
from agentplatform.spec.errors import SpecError
from agentplatform.spec.loader import TEAM_RENDERABLE, Entity

MAX_RETRIES = 3
BUDGET_FLOOR_TOKENS = 2000

_INDENT = "    "

# dev-wave 相位在场骨架（delivery_squad 波次）——seat 名来自声明的 present_in_phases
_DELIVERY_SEATS = {
    "plan": ("planner",),
    "build": ("builder",),
    "verify": ("test_author",),
    "integrate": (),
    "handoff": ("test_author",),
}


def _seat_members(team: Entity) -> dict[str, list[dict[str, Any]]]:
    """seat → 成员声明列表（count 展开前的分组）。"""
    seats: dict[str, list[dict[str, Any]]] = {}
    for m in team.raw.get("members") or []:
        seat = str(m.get("seat") or "")
        seats.setdefault(seat, []).append(m)
    return seats


def _steps_text(snap: SpecSnapshot, agent_ref: str) -> str:
    """座位 agent 的固定流程（steps.md）正文——嵌入 prompt 使脚本自含。"""
    agent = snap.resolve_agent(agent_ref)
    wf = agent.raw.get("workflow") or {}
    ref = wf.get("steps_ref") if wf.get("mode") == "fixed" else None
    if not ref:
        return ""
    rel = ref if ref.startswith("registry/") else f"registry/{ref}"
    if rel not in snap.resources:
        raise SpecError("reference", f"agent:{agent.id} steps_ref={ref} 不可解析")
    return (snap.root / rel).read_text(encoding="utf-8").strip()


def _prompt(title: str, seat: str, steps: str) -> str:
    body = steps or "（无固定流程声明——按卡目标与验收引用执行）"
    return f"{title}（seat={seat}）。固定流程：\n{body}"


def _schema_cards() -> dict[str, Any]:
    """wave-plan schema：卡字段对齐 team-collaboration artifacts.card.fields_required。"""
    return {
        "type": "object",
        "properties": {
            "cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "goal": {"type": "string"},
                        "acceptance_refs": {"type": "array", "items": {"type": "string"}},
                        "capability_tags": {"type": "array", "items": {"type": "string"}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "risk_class": {"type": "string"},
                        "change_class": {"type": "string"},
                    },
                    "required": ["id", "goal", "acceptance_refs"],
                },
            }
        },
        "required": ["cards"],
    }


def _schema_gate() -> dict[str, Any]:
    """verifier 判卷事实（pass 由确定性代码采信——不是 LLM 说 pass 就 pass）。"""
    return {
        "type": "object",
        "properties": {
            "test_tree_sha": {"type": "string"},
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["name", "passed"],
                },
            },
        },
        "required": ["checks"],
    }


def _schema_review() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approve", "changes_requested", "waived"]},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["decision"],
    }


def _merge_verdict_src() -> str:
    return (
        "def _merge_verdict(gate, review):\n"
        '    """确定性判卷（机制原型——无 LLM 决定权）。\n\n'
        "    merge_policy 声明：gate.pass AND (review.approve OR review.waived)。\n"
        '    """\n'
        "    if gate is None or review is None:\n"
        '        return {"ok": False, "changes_requested": True, "reason": "worker 输出缺失"}\n'
        '    checks = gate.get("checks") or []\n'
        '    gate_pass = bool(checks) and all(c.get("passed") for c in checks)\n'
        '    decision = review.get("decision")\n'
        "    return {\n"
        '        "ok": gate_pass and decision in ("approve", "waived"),\n'
        '        "changes_requested": decision == "changes_requested",\n'
        '        "gate_pass": gate_pass,\n'
        '        "review": decision,\n'
        "    }\n"
    )


def _head(team: Entity, snap: SpecSnapshot, graph: PhaseGraph) -> str:
    return (
        f'"""{team.id} 波次编排（SwarmFlow 编译产物——勿手改）。\n\n'
        f"source: {team.rel_path}\n"
        f"spec_digest: {snap.digest}\n"
        f"phase_graph_digest: {graph.digest}\n"
        f"compiler: agent-platform {agentplatform.__version__}\n"
        '"""\n'
    )


def _meta(team: Entity, snap: SpecSnapshot, graph: PhaseGraph) -> str:
    order = ", ".join(f'"{p}"' for p in graph.phases)
    return (
        "META = {\n"
        f'    "name": "{team.id}",\n'
        f'    "phases": [{order}],\n'
        '    "source": {\n'
        f'        "team": "{team.id}",\n'
        f'        "spec_digest": "{snap.digest}",\n'
        f'        "phase_graph_digest": "{graph.digest}",\n'
        "    },\n"
        "}\n"
    )


def _delivery_body(snap: SpecSnapshot, team: Entity, seats: dict[str, list[dict[str, Any]]]) -> str:
    """波次骨架：plan → (build ⇄ verify)* → integrate → handoff。"""
    plan_m = seats.get("planner") or seats.get(next(iter(seats)))
    build_m = seats.get("builder") or plan_m
    review_m = seats.get("test_author") or plan_m
    plan_steps = _steps_text(snap, plan_m[0]["agent"]) if plan_m else ""
    build_steps = _steps_text(snap, build_m[0]["agent"]) if build_m else ""
    review_steps = _steps_text(snap, review_m[0]["agent"]) if review_m else ""

    L: list[str] = []
    a = L.append
    a('PLAN_PROMPT = """按波次分解意图并产工作卡。' + _prompt_body("planner", plan_steps) + '"""')
    a('BUILD_PROMPT = """实现指定工作卡。' + _prompt_body("builder", build_steps) + '"""')
    a('GATE_PROMPT = """对冻结测试树判卷，逐项产出检查事实（只报事实，结论由机制计算）。"""')
    a('REVIEW_PROMPT = """裁定实现是否满足卡意图。' + _prompt_body("test_author", review_steps) + '"""')
    a("")
    a(_merge_verdict_src())
    a("")
    a("async def _build_one(card):")
    a(f"{_INDENT}from swarmflow import agent")
    a(f"{_INDENT}return await agent(")
    a(f'{_INDENT}{_INDENT}f\'{{BUILD_PROMPT}}\\n卡：{{card["id"]}} {{card["goal"]}}\',')
    a('{i}{i}label=f\'builder:{{card["id"]}}\', phase="build", schema=None,'.replace("{i}", _INDENT * 2))
    a(f"{_INDENT})")
    a("")
    a("async def run(args):")
    a(f"{_INDENT}from swarmflow import agent, budget, log, map_parallel, parallel, phase")
    a("")
    a(f"{_INDENT}# ---- plan：planner 产波次计划（cards 结构化输出）----")
    a(f'{_INDENT}phase("plan")')
    a(f'{_INDENT}plan = await agent(PLAN_PROMPT, label="planner", phase="plan", schema=SCHEMA_WAVE_PLAN)')
    a(f'{_INDENT}cards = (plan or {{}}).get("cards", [])')
    a(f'{_INDENT}log(f"波次计划：{{len(cards)}} 卡")')
    a("")
    a(f"{_INDENT}# ---- build ⇄ verify：返工回路（changes_requested 回边；retries 上限防振荡）----")
    a(f'{_INDENT}verdict = {{"ok": False, "changes_requested": True}}')
    a(f"{_INDENT}attempt = 0")
    a(f"{_INDENT}while attempt < {MAX_RETRIES}:")
    a(f"{_INDENT}{_INDENT}# remaining() 无界时为 None——不熔断（治理面预算由 agent_gate 闸）")
    a(f"{_INDENT}{_INDENT}if budget.remaining() is not None and budget.remaining() < {BUDGET_FLOOR_TOKENS}:")
    a(f'{_INDENT}{_INDENT}{_INDENT}log("预算撞顶——收束（wave.frozen 语义）")')
    a(f"{_INDENT}{_INDENT}{_INDENT}break")
    a(f'{_INDENT}{_INDENT}phase("build")')
    a(f"{_INDENT}{_INDENT}await map_parallel(cards, _build_one)")
    a(f'{_INDENT}{_INDENT}phase("verify")')
    i3, i4 = _INDENT * 3, _INDENT * 4
    a(f"{_INDENT}{_INDENT}gate, review = await parallel([")
    a(f"{i3}lambda: agent(")
    a(f'{i4}GATE_PROMPT, label="verifier", phase="verify", schema=SCHEMA_GATE),')
    a(f"{i3}lambda: agent(")
    a(f'{i4}REVIEW_PROMPT, label="test_author", phase="verify", schema=SCHEMA_REVIEW),')
    a(f"{_INDENT}{_INDENT}])")
    a(f"{_INDENT}{_INDENT}verdict = _merge_verdict(gate, review)")
    a(f'{_INDENT}{_INDENT}log(f"判卷：{{verdict}}")')
    a(f'{_INDENT}{_INDENT}if verdict["ok"]:')
    a(f"{_INDENT}{_INDENT}{_INDENT}break")
    a(f'{_INDENT}{_INDENT}if not verdict["changes_requested"]:')
    a(f"{_INDENT}{_INDENT}{_INDENT}attempt += 1  # gate 失败也计入重试（retries.exhausted → 回炉）")
    a("")
    a(f"{_INDENT}# ---- integrate：merge/release 均为机制动作（无 agent 持合并权）----")
    a(f'{_INDENT}phase("integrate")')
    a(f'{_INDENT}merged = verdict.get("ok", False)')
    a(f'{_INDENT}log(f"合并判定：{{merged}}（gate.pass AND (review.approve OR review.waived)）")')
    a("")
    a(f"{_INDENT}# ---- handoff：交接（test_author 在场——retro/memory-export）----")
    a(f'{_INDENT}phase("handoff")')
    a(f"{_INDENT}await agent(")
    a(f'{_INDENT}{_INDENT}"产 retrospective 与 memory_digest 导出（交接相位唯一在场座位）",')
    a(f'{_INDENT}{_INDENT}label="test_author:handoff", phase="handoff", schema=None,')
    a(f"{_INDENT})")
    a("")
    a(f'{_INDENT}return {{"cards": len(cards), "merged": merged}}')
    return "\n".join(L)


def _prompt_body(seat: str, steps: str) -> str:
    if steps:
        import re

        steps = re.sub(r"\s+", " ", steps)[:400]
    else:
        steps = "按卡目标与验收引用执行"
    return f"（seat={seat}）固定流程：{steps}"


def _sequential_body(graph: PhaseGraph, seats: dict[str, list[dict[str, Any]]]) -> str:
    """非波次团队骨架：相位顺序推进，在场座位逐相位一次结构化调用。"""
    L: list[str] = []
    a = L.append
    a("async def run(args):")
    a(f"{_INDENT}from swarmflow import agent, log, phase")
    a(f"{_INDENT}out = {{}}")
    for p in graph.phases:
        a(f'{_INDENT}phase("{p}")')
        seat_list = _DELIVERY_SEATS.get(p)
        members = seats.get(seat_list[0]) if seat_list else None
        if members:
            seat = seat_list[0]
            a(
                f'{_INDENT}out["{p}"] = await agent('
                f'"执行 {p} 相位职责（seat={seat}）", label="{seat}", phase="{p}", schema=None)'
            )
        else:
            a(f'{_INDENT}log("{p} 相位（机制动作/无在场实体座位）")')
    a(f"{_INDENT}return out")
    return "\n".join(L)


def compile_team_flow(snap: SpecSnapshot, team: Entity, graph: PhaseGraph) -> str:
    """编译单个团队为 SwarmFlow 脚本文本（确定性：同快照+图 → 字节级相同）。"""
    seats = _seat_members(team)
    if not seats:
        raise SpecError("shape", f"team:{team.id} 无座位成员")
    head = _head(team, snap, graph)
    meta = _meta(team, snap, graph)
    schemas = (
        # _dict_lit 自带花括号——外层再包 { } 会变成 set 字面量（运行时 unhashable）
        "SCHEMA_WAVE_PLAN = " + _dict_lit(_schema_cards(), 0) + "\n"
        "SCHEMA_GATE = " + _dict_lit(_schema_gate(), 0) + "\n"
        "SCHEMA_REVIEW = " + _dict_lit(_schema_review(), 0) + "\n"
    )
    is_wave = str(team.raw.get("topology") or "").startswith("leader-teammate")
    body = _delivery_body(snap, team, seats) if is_wave else _sequential_body(graph, seats)
    return head + "\n" + meta + "\n" + schemas + "\n" + body + "\n"


def _dict_lit(d: Any, depth: int) -> str:
    """dict → 字面量（确定性序列化，供 schema 常量嵌入）。"""
    pad = _INDENT * (depth + 1)
    if isinstance(d, dict):
        items = [f'{pad}"{k}": {_dict_lit(v, depth + 1)}' for k, v in d.items()]
        return "{\n" + ",\n".join(items) + "\n" + _INDENT * depth + "}"
    if isinstance(d, list):
        return "[" + ", ".join(_dict_lit(x, depth + 1) for x in d) + "]"
    if isinstance(d, bool):
        return "True" if d else "False"
    if d is None:
        return "None"
    if isinstance(d, (int, float)):
        return str(d)
    return '"' + str(d).replace("\\", "\\\\").replace('"', '\\"') + '"'


def flow_outputs(snap: SpecSnapshot, graph: PhaseGraph) -> dict[str, str]:
    """全部可渲染团队的编译产物：相对路径（swarmflow/<team>.py）→ 文本。"""
    outs: dict[str, str] = {}
    for tid, team in sorted(snap.teams.items()):
        if team.status not in TEAM_RENDERABLE:
            continue
        outs[f"swarmflow/{tid}.py"] = compile_team_flow(snap, team, graph)
    return outs
