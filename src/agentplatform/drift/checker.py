"""漂移检查：manifest（输出面基准）↔ 磁盘 ↔ 声明面三方对账。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from agentplatform.render.manifest import MANIFEST_NAME, load_manifest
from agentplatform.spec import RegistryLoader
from agentplatform.spec.fingerprint import sha256_hex

# 工作区合法的自有文件（非渲染产物——运行时状态，不参与对账）
RUNTIME_OWNED = frozenset({".ap", "logs", "state", "trace.jsonl", ".env", ".env.example"})


@dataclass(frozen=True)
class DriftIssue:
    kind: str  # spec | file | orphan | manifest
    detail: str


@dataclass
class DriftReport:
    ok: bool
    spec_digest_declared: str  # 当前声明面
    spec_digest_rendered: str  # manifest 记录
    issues: list[DriftIssue] = field(default_factory=list)
    checked_files: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "spec_digest_declared": self.spec_digest_declared,
            "spec_digest_rendered": self.spec_digest_rendered,
            "checked_files": self.checked_files,
            "issues": [{"kind": i.kind, "detail": i.detail} for i in self.issues],
        }


def check_workspace(
    registry_root: str | Path,
    workspace: str | Path,
    *,
    skip_spec: bool = False,
) -> DriftReport:
    """对账。skip_spec：只对账文件面（声明仓不可用时的降级模式）。"""
    ws = Path(workspace)
    issues: list[DriftIssue] = []

    try:
        manifest = load_manifest(ws)
    except FileNotFoundError:
        return DriftReport(False, "-", "-", [DriftIssue("manifest", f"{ws} 无 {MANIFEST_NAME}（未渲染？）")])
    except ValueError as e:
        return DriftReport(False, "-", "-", [DriftIssue("manifest", str(e))])

    declared = "-"
    if not skip_spec:
        snap = RegistryLoader().load(registry_root)
        declared = snap.digest
        if declared != manifest.spec_digest:
            issues.append(
                DriftIssue(
                    "spec",
                    f"声明面已变更未重渲染：declared={declared[:12]}… rendered={manifest.spec_digest[:12]}…",
                )
            )

    # 文件面：manifest 记录的每个文件
    checked = 0
    for rel, expect_hash in sorted(manifest.files.items()):
        p = ws / rel
        if not p.is_file():
            issues.append(DriftIssue("file", f"缺失：{rel}"))
            continue
        actual = sha256_hex(p.read_text(encoding="utf-8"))
        checked += 1
        if actual != expect_hash:
            issues.append(
                DriftIssue("file", f"被修改：{rel}（磁盘 {actual[:12]}… != 记录 {expect_hash[:12]}…）")
            )

    # 孤儿：磁盘有、manifest 无（渲染产物之外只允许 runtime 自有路径）
    recorded = set(manifest.files) | {MANIFEST_NAME}
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ws).as_posix()
        if rel in recorded:
            continue
        top = rel.split("/", 1)[0]
        if top in RUNTIME_OWNED:
            continue
        issues.append(DriftIssue("orphan", f"未记录文件：{rel}"))

    return DriftReport(not issues, declared, manifest.spec_digest, issues, checked)


def watch(
    registry_root: str | Path,
    workspace: str | Path,
    *,
    interval_s: float = 30.0,
    max_rounds: int | None = None,
    sleeper=time.sleep,
    clock=time.time,
    emit=None,
) -> list[dict]:
    """周期对账。漂移即事件（JSONL 行 dict）；emit 回调可接 TUI/文件。

    每轮输出一条 round 事件（ok 与否都记——低事件密度下"在监控"本身是信息）。
    """
    sink = emit if emit is not None else (lambda _x: None)
    events: list[dict] = []
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        report = check_workspace(registry_root, workspace)
        ev = {
            "type": "drift.round",
            "ts": clock(),
            "round": rounds,
            "ok": report.ok,
            "issues": [i.detail for i in report.issues],
        }
        events.append(ev)
        sink(ev)
        rounds += 1
        if max_rounds is None or rounds < max_rounds:
            sleeper(interval_s)
    return events
