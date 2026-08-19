"""RuntimeStore：workspace/state/ 文件面——治理对象的持久化与恢复。

布局：
    state/
      ledger.jsonl   事件哈希链（唯一事实源——治理对象皆是其投影）
      meta.json      预算配置（envelope/overhead/wall_clock）——重放参数

生命周期：create（新建空账本）或 open（加载+重放恢复）。每次干预动词执行后
调 flush() 落盘；open 时若账本被篡改（链断）直接拒绝加载（fail-closed）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentplatform.governance.budget import BudgetGovernor
from agentplatform.governance.cardgate import CardGate
from agentplatform.governance.ledger import EventLedger, LedgerIntegrityError
from agentplatform.governance.writelock import WriteLock
from agentplatform.observe.projection import project

LEDGER_NAME = "ledger.jsonl"
META_NAME = "meta.json"
META_SCHEMA = 1


class StoreError(Exception):
    """store 面错误（目录非空/缺文件/链断/meta 损坏）。"""


class RuntimeStore:
    """事件溯源运行时：ledger + CardGate + BudgetGovernor + WriteLock 共账本。"""

    def __init__(self, state_dir: Path, ledger: EventLedger, meta: dict) -> None:
        self.dir = state_dir
        self.ledger = ledger
        self.meta = meta
        self.cardgate = CardGate.from_events(ledger)
        self.budget = BudgetGovernor.from_events(
            ledger,
            envelope_usd=float(meta["envelope_usd"]),
            overhead_usd=float(meta["overhead_usd"]),
            wall_clock_cap_s=meta.get("wall_clock_cap_s"),
        )
        self.writelock = WriteLock.from_events(ledger)

    # ---- 建/开 ----
    @classmethod
    def create(
        cls,
        state_dir: str | Path,
        *,
        envelope_usd: float,
        overhead_usd: float = 0.0,
        wall_clock_cap_s: float | None = None,
    ) -> RuntimeStore:
        d = Path(state_dir)
        if (d / LEDGER_NAME).exists():
            raise StoreError(f"{d} 已有 {LEDGER_NAME}——用 open() 打开而非 create()")
        if envelope_usd <= 0:
            raise StoreError("envelope_usd 必须 > 0")
        meta = {
            "schema": META_SCHEMA,
            "envelope_usd": float(envelope_usd),
            "overhead_usd": float(overhead_usd),
            "wall_clock_cap_s": wall_clock_cap_s,
            "created_at": time.time(),
        }
        ledger = EventLedger()
        ledger.append("store.created", "mechanism:runtime", {"envelope_usd": float(envelope_usd)})
        store = cls(d, ledger, meta)
        store.flush()
        return store

    @classmethod
    def open(cls, state_dir: str | Path) -> RuntimeStore:
        d = Path(state_dir)
        ledger_path = d / LEDGER_NAME
        meta_path = d / META_NAME
        if not ledger_path.is_file():
            raise StoreError(f"{ledger_path} 不存在（未 create？）")
        try:
            ledger = EventLedger.load(ledger_path)
            ledger.verify()  # fail-closed：账本断链拒绝服务
        except LedgerIntegrityError as e:
            raise StoreError(f"账本完整性失败，拒绝加载：{e}") from e
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise StoreError(f"{meta_path} 损坏或缺失：{e}") from e
        if meta.get("schema") != META_SCHEMA:
            raise StoreError(f"meta schema {meta.get('schema')} != {META_SCHEMA}")
        return cls(d, ledger, meta)

    # ---- 落盘 ----
    def flush(self) -> Path:
        self.ledger.save(self.dir / LEDGER_NAME)
        meta = dict(self.meta)
        meta["ledger_head"] = self.ledger.head()
        (self.dir / META_NAME).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self.dir / LEDGER_NAME

    # ---- 视图 ----
    def snapshot(self) -> dict:
        """投影视图（TUI/agentctl status 共用）+ 配置面。"""
        view = project(self.ledger)
        view["config"] = {
            "envelope_usd": float(self.meta["envelope_usd"]),
            "overhead_usd": float(self.meta["overhead_usd"]),
            "wall_clock_cap_s": self.meta.get("wall_clock_cap_s"),
            "ledger_head": self.ledger.head(),
        }
        return view

    def verify(self) -> None:
        """全链重算（agentctl ledger.verify 的执行体）。"""
        self.ledger.verify()
