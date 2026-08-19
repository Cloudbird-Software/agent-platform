"""PhaseGraph：声明相位状态机的不可变 IR + 图验证。

声明的真源：standards/team-collaboration.yaml#flow.phases
（graph + phase_order + deadlock_check）与 #flow.event_producers。

验证规则（registry deadlock_check 声明的可执行化——simulate-wave.py 侧
的图逻辑在此复检，渲染面不依赖 registry 脚本）：
- G2 边相位存在：from/to ∈ phase_order（"any" 通配合法）
- G3 专属出边：非终态相位必有 from==<phase> 的出边（any 边不计入——
  恒真的通配边不能消除死锁，见声明的 deadlock_check 注）
- G5 可达性：从首相位沿边（含 any）可达全部相位
- G6 悬空事件：when 条件里的事件 token 必须在 event_producers 有生产者
  （状态表达式 <x>.state/<x>.clock 与函数调用 name(...) 不是事件）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml

from agentplatform.spec.errors import SpecError
from agentplatform.spec.fingerprint import sha256_hex

WILDCARD = "any"

# when 表达式里的非事件形态：seat/mechanism 状态（planner.state）、时钟（budget.clock）
_STATE_SUFFIXES = (".state", ".clock")
# 提取点分 token（cards.ratified / planner.state）与裸词（reverted）
_TOKEN = re.compile(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*")


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    when: str

    @property
    def wildcard(self) -> bool:
        return self.src == WILDCARD


@dataclass(frozen=True)
class ValidationIssue:
    rule: str
    message: str


class PhaseGraph:
    """不可变相位图。producers: 事件 → 生产者（悬空事件检查的对照面）。"""

    def __init__(
        self,
        phases: tuple[str, ...],
        edges: tuple[Edge, ...],
        producers: dict[str, str],
        source_rel: str,
        source_digest: str,
    ) -> None:
        self._phases = phases
        self._edges = edges
        self._producers = MappingProxyType(producers)
        self._source_rel = source_rel
        self._source_digest = source_digest
        self._digest = sha256_hex(
            "\n".join(f"{p}:{e.src}>{e.dst}:{e.when}" for p, e in enumerate(edges))
            + "|"
            + ",".join(phases)
            + "|"
            + ",".join(sorted(producers))
        )

    @property
    def phases(self) -> tuple[str, ...]:
        return self._phases

    @property
    def edges(self) -> tuple[Edge, ...]:
        return self._edges

    @property
    def producers(self) -> MappingProxyType[str, str]:
        return self._producers

    @property
    def source_rel(self) -> str:
        return self._source_rel

    @property
    def source_digest(self) -> str:
        return self._source_digest

    @property
    def digest(self) -> str:
        """图身份：编译产物嵌入此摘要，漂移检测可追溯到声明面。"""
        return self._digest

    def terminal(self) -> str:
        return self._phases[-1]

    def own_out_edges(self, phase: str) -> tuple[Edge, ...]:
        return tuple(e for e in self._edges if e.src == phase)

    def outgoing(self, phase: str) -> tuple[Edge, ...]:
        return tuple(e for e in self._edges if e.src in (phase, WILDCARD))

    def reachable(self) -> set[str]:
        seen: set[str] = set()
        frontier = [self._phases[0]]
        while frontier:
            p = frontier.pop()
            if p in seen:
                continue
            seen.add(p)
            for e in self.outgoing(p):
                if e.dst not in seen:
                    frontier.append(e.dst)
        return seen


def events_in_when(when: str) -> list[str]:
    """提取 when 表达式里的事件引用（悬空检测用）。

    排除：函数调用及其全部实参（no_release_face(wave)）、比较右侧裸词
    （planner.state == exited 的 exited 是状态值不是事件）、
    <x>.state / <x>.clock 状态表达式。
    """
    cleaned = re.sub(r"[a-z_][a-z0-9_]*\([^)]*\)", " ", when)  # 函数调用整体剥离
    cleaned = re.sub(r"([=!<>]=|[<>])\s*[A-Za-z_][A-Za-z0-9_]*", " ", cleaned)  # 比较右侧值
    events: list[str] = []
    for m in _TOKEN.finditer(cleaned):
        tok = m.group(0)
        if tok.endswith(_STATE_SUFFIXES):
            continue
        events.append(tok)
    return events


def load_phase_graph(snap_root: str | Path) -> PhaseGraph:
    """从 registry checkout 加载相位图。结构问题 fail-closed（SpecError）。"""
    root = Path(snap_root)
    path = root / "standards" / "team-collaboration.yaml"
    if not path.is_file():
        raise SpecError("missing", f"相位图真源缺失：{path}")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SpecError("parse", f"{path}: {e}") from e
    if not isinstance(doc, dict):
        raise SpecError("shape", f"{path}: 根节点必须是映射")

    flow = doc.get("flow")
    if not isinstance(flow, dict):
        raise SpecError("shape", f"{path}: 缺 flow 节")
    phases_node = flow.get("phases")
    if not isinstance(phases_node, dict):
        raise SpecError("shape", f"{path}: 缺 flow.phases 节")

    order = phases_node.get("phase_order")
    if not isinstance(order, list) or not order or not all(isinstance(p, str) for p in order):
        raise SpecError("shape", f"{path}: flow.phases.phase_order 必须是非空字符串列表")
    phases = tuple(order)
    if len(set(phases)) != len(phases):
        raise SpecError("shape", f"{path}: phase_order 存在重复相位")

    edges: list[Edge] = []
    graph = phases_node.get("graph")
    if not isinstance(graph, list):
        raise SpecError("shape", f"{path}: 缺 flow.phases.graph 边表")
    for i, e in enumerate(graph):
        if not isinstance(e, dict):
            raise SpecError("shape", f"{path}: graph[{i}] 必须是映射")
        src, dst, when = e.get("from"), e.get("to"), e.get("when")
        if not (isinstance(src, str) and isinstance(dst, str) and isinstance(when, str) and when):
            raise SpecError("shape", f"{path}: graph[{i}] 缺 from/to/when")
        edges.append(Edge(src, dst, when))

    producers_node = flow.get("event_producers")
    if not isinstance(producers_node, dict) or not producers_node:
        raise SpecError("shape", f"{path}: 缺 flow.event_producers（悬空事件检测的对照面）")
    producers = {str(k): str(v) for k, v in producers_node.items()}

    source_digest = sha256_hex(path.read_text(encoding="utf-8"))
    return PhaseGraph(phases, tuple(edges), producers, "standards/team-collaboration.yaml", source_digest)


def validate_graph(g: PhaseGraph) -> list[ValidationIssue]:
    """图验证：返回问题列表（空=通过）。不抛异常——报告面。"""
    issues: list[ValidationIssue] = []
    known = set(g.phases)
    terminal = g.terminal()

    # G2：边相位存在
    for e in g.edges:
        if e.src != WILDCARD and e.src not in known:
            issues.append(ValidationIssue("G2", f"边 from={e.src} 不在 phase_order"))
        if e.dst not in known:
            issues.append(ValidationIssue("G2", f"边 to={e.dst} 不在 phase_order"))

    # G3：非终态相位必有专属出边（any 不计）
    for p in g.phases:
        if p == terminal:
            continue
        if not g.own_out_edges(p):
            issues.append(
                ValidationIssue("G3", f"相位 {p} 无专属出边（any 通配边不计——恒真边不能消除死锁）")
            )

    # G5：可达性
    reached = g.reachable()
    for p in g.phases:
        if p not in reached:
            issues.append(ValidationIssue("G5", f"相位 {p} 从首相位不可达"))

    # G6：悬空事件
    for e in g.edges:
        for tok in events_in_when(e.when):
            if tok not in g.producers:
                issues.append(
                    ValidationIssue("G6", f"边 {e.src}→{e.dst} 的 when 引用事件 {tok!r} 无生产者")
                )
    return issues
