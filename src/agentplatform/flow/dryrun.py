"""dry-run：编译产物的静态校验（不执行 agent、不依赖上游运行时）。

校验面（ast 级——与 jiuwenswarm 的 loader/linter 同型检查，但零依赖）：
- D1 语法合法（ast.parse）
- D2 import 白名单：仅 `from swarmflow import ...`，算子 ∈ 引擎 facade 集合
- D3 危险调用面：禁 os/sys/subprocess/eval/exec/open/__import__（编译器
  被投喂恶意声明时的最后防线——产物只应做编排）
- D4 结构：META 存在（name 非空 str、phases 非空 list）、async def run(args)
- D5 phase() 标题 ∈ META["phases"]（进度组与声明相位图一致）
- D6 label 唯一（观测面可按 label 归因——重复即观测盲区）
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentplatform.flow.codegen import flow_outputs
from agentplatform.flow.graph import PhaseGraph, load_phase_graph, validate_graph
from agentplatform.spec import RegistryLoader

ALLOWED_OPERATORS = frozenset(
    {
        "agent",
        "agent_session",
        "human",
        "human_session",
        "parallel",
        "pipeline",
        "map_parallel",
        "pmap",
        "phase",
        "log",
        "compact",
        "flatten_filter",
        "workflow",
        "budget",
    }
)

DANGEROUS_NAMES = frozenset(
    {"eval", "exec", "open", "__import__", "compile", "globals", "locals", "vars"}
)
DANGEROUS_MODULES = frozenset(
    {"os", "sys", "subprocess", "shutil", "pathlib", "socket", "requests", "urllib", "http", "importlib"}
)


def lint_workflow_source(source: str) -> list[str]:
    """返回问题列表（空=通过）。"""
    issues: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"D1 语法错误：{e}"]

    meta: dict | None = None
    has_run = False
    labels: list[str] = []
    phase_titles: list[str] = []

    # import 检查覆盖全树（编译产物的算子 import 在 run() 体内）
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "swarmflow":
                for alias in node.names:
                    if alias.name not in ALLOWED_OPERATORS:
                        issues.append(f"D2 非白名单算子：swarmflow.{alias.name}")
            elif node.module is not None:
                root = node.module.split(".")[0]
                if root in DANGEROUS_MODULES:
                    issues.append(f"D3 危险 import：{node.module}")
                else:
                    issues.append(f"D2 只允许 from swarmflow import（发现 {node.module}）")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in DANGEROUS_MODULES:
                    issues.append(f"D3 危险 import：{alias.name}")
                else:
                    issues.append(f"D2 只允许 from swarmflow import（发现 import {alias.name}）")

    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id == "META":
                try:
                    meta = ast.literal_eval(node.value)  # type: ignore[arg-type]
                except (ValueError, SyntaxError):
                    issues.append("D4 META 必须是字面量（编译器产物不含动态构造）")
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            has_run = True
            labels.extend(_kw_labels(node))
            phase_titles.extend(_phase_titles(node))
        elif isinstance(node, ast.FunctionDef):
            labels.extend(_kw_labels(node))
            phase_titles.extend(_phase_titles(node))

    # D3 危险调用（遍历全部）
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in DANGEROUS_NAMES
        ):
            issues.append(f"D3 危险调用：{node.func.id}()")
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in DANGEROUS_MODULES
        ):
            issues.append(f"D3 危险属性访问：{node.value.id}.{node.attr}")

    if not isinstance(meta, dict):
        issues.append("D4 缺 META（或 META 非映射）")
    else:
        if not (isinstance(meta.get("name"), str) and meta["name"]):
            issues.append("D4 META.name 必须是非空字符串")
        if not (isinstance(meta.get("phases"), list) and meta["phases"]):
            issues.append("D4 META.phases 必须是非空列表")
    if not has_run:
        issues.append("D4 缺 async def run(args)")

    if isinstance(meta, dict) and isinstance(meta.get("phases"), list):
        for t in phase_titles:
            if t not in meta["phases"]:
                issues.append(f"D5 phase() 标题 {t!r} 不在 META.phases")

    dupes = {x for x in labels if labels.count(x) > 1}
    for x in sorted(dupes):
        issues.append(f"D6 label 重复：{x}")
    return issues


def _kw_labels(fn: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "label" and isinstance(kw.value, ast.Constant):
                    out.append(str(kw.value.value))
    return out


def _phase_titles(fn: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "phase"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            out.append(str(node.args[0].value))
    return out


def dryrun_registry(registry_root: str | Path) -> dict[str, object]:
    """全链路 dry-run：图验证 + 全团队编译 + 产物 lint。返回汇总报告。"""
    snap = RegistryLoader().load(registry_root)
    graph: PhaseGraph = load_phase_graph(registry_root)

    report: dict[str, object] = {
        "spec_digest": snap.digest,
        "phase_graph_digest": graph.digest,
        "graph_issues": [f"{i.rule}: {i.message}" for i in validate_graph(graph)],
        "teams": {},
    }
    teams: dict[str, object] = {}
    outputs = flow_outputs(snap, graph)
    for rel, source in sorted(outputs.items()):
        teams[rel] = lint_workflow_source(source)
    report["teams"] = teams
    return report
