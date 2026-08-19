"""事件哈希链：治理事件流的防篡改账本。

声明依据（team-collaboration）：trace-archive/事件流不随队销毁；事件必有
生产者（event_producers）；A7 留痕完整。落地形态：
- append-only：每事件 hash = sha256(prev_hash + canonical(seq,kind,actor,...))；
- 任何历史事件被篡改/删除/重排 → verify() 在首个断点报错；
- JSONL 持久化（文件追加）；ts 由调用方注入（确定性测试）或取实时钟。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agentplatform.spec.fingerprint import canonical_json, sha256_hex

GENESIS = "0" * 64


@dataclass(frozen=True)
class GovernanceEvent:
    seq: int
    kind: str
    actor: str
    hash: str
    prev_hash: str
    ts: float
    card_id: str | None = None
    payload: dict = field(default_factory=dict)


class LedgerIntegrityError(Exception):
    """链校验失败——报首个断点（seq + 原因）。"""

    def __init__(self, seq: int, reason: str) -> None:
        self.seq = seq
        self.reason = reason
        super().__init__(f"事件链断点 seq={seq}: {reason}")


def _event_hash(
    prev: str, seq: int, kind: str, actor: str, card_id: str | None, payload: dict, ts: float
) -> str:
    body = canonical_json(
        {"seq": seq, "kind": kind, "actor": actor, "card_id": card_id, "payload": payload, "ts": ts}
    )
    return sha256_hex(prev + "|" + body)


class EventLedger:
    """内存链 + JSONL 持久化。append 时自动接链；verify 重算全链。"""

    def __init__(self, events: list[GovernanceEvent] | None = None) -> None:
        self._events: list[GovernanceEvent] = list(events or [])
        self._validate_init()

    def _validate_init(self) -> None:
        prev = GENESIS
        for i, e in enumerate(self._events):
            if e.seq != i:
                raise LedgerIntegrityError(e.seq, f"序号断裂（期望 {i}）")
            if e.prev_hash != prev:
                raise LedgerIntegrityError(e.seq, "prev_hash 不接链")
            prev = e.hash

    # ---- 写 ----
    def append(
        self,
        kind: str,
        actor: str,
        payload: dict | None = None,
        *,
        card_id: str | None = None,
        ts: float | None = None,
    ) -> GovernanceEvent:
        prev = self._events[-1].hash if self._events else GENESIS
        ts_v = time.time() if ts is None else ts
        h = _event_hash(prev, len(self._events), kind, actor, card_id, payload or {}, ts_v)
        ev = GovernanceEvent(len(self._events), kind, actor, h, prev, ts_v, card_id, payload or {})
        self._events.append(ev)
        return ev

    # ---- 读 ----
    def events(self) -> tuple[GovernanceEvent, ...]:
        return tuple(self._events)

    def head(self) -> str:
        return self._events[-1].hash if self._events else GENESIS

    def by_card(self, card_id: str) -> tuple[GovernanceEvent, ...]:
        return tuple(e for e in self._events if e.card_id == card_id)

    def kinds(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self._events:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    # ---- 校验 ----
    def verify(self) -> None:
        """重算全链；断点抛 LedgerIntegrityError（含 tampered/removed/reordered）。"""
        prev = GENESIS
        for i, e in enumerate(self._events):
            if e.seq != i:
                raise LedgerIntegrityError(e.seq, f"序号断裂（期望 {i}——删除或重排）")
            if e.prev_hash != prev:
                raise LedgerIntegrityError(e.seq, "prev_hash 不接链（删除前序事件）")
            expect = _event_hash(prev, e.seq, e.kind, e.actor, e.card_id, e.payload, e.ts)
            if e.hash != expect:
                raise LedgerIntegrityError(e.seq, "事件体被篡改（hash 不匹配）")
            prev = e.hash

    # ---- 持久化 ----
    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(asdict(e), ensure_ascii=False, sort_keys=True) for e in self._events)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_jsonl() + ("\n" if self._events else ""), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> EventLedger:
        p = Path(path)
        events: list[GovernanceEvent] = []
        for line_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                events.append(GovernanceEvent(**d))
            except (json.JSONDecodeError, TypeError) as e:
                raise LedgerIntegrityError(line_no, f"JSONL 行损坏：{e}") from e
        return cls(events)
