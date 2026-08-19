"""agentctl：干预动词表——对内部运作的直接控制面（ADR-0025）。

契约（供外部 agent 程序化调用）：
- 一切输出 JSON（stdout）；ok=false 时退出码 1，用法错误退出码 2；
- 每个动词 = open store → 执行 → flush（有写时）→ JSON 结果；
- 与 TUI 共享同一事件流：干预后 TUI 下一帧即反映（观测=干预的投影）。

动词族：
  观测    status / cards / card-show / budget / locks / events / verify
  卡      card-ratify / card-advance / card-pause / card-resume / card-abort
  预算    budget-spend / budget-tick
  锁      lock-acquire / lock-release / lock-freeze
  账本    ledger-export
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agentplatform.governance.cardgate import CardStateError
from agentplatform.governance.ledger import LedgerIntegrityError
from agentplatform.governance.writelock import WriteLockError
from agentplatform.observe.store import RuntimeStore, StoreError


def _ev(e: Any) -> dict:
    return {
        "seq": e.seq,
        "kind": e.kind,
        "actor": e.actor,
        "card_id": e.card_id,
        "ts": e.ts,
        "payload": e.payload,
    }


def _ok(data: dict[str, Any] | None = None) -> int:
    print(json.dumps({"ok": True, **(data or {})}, ensure_ascii=False, sort_keys=False))
    return 0


def _fail(message: str, code: int = 1, **extra: Any) -> int:
    print(json.dumps({"ok": False, "error": message, **extra}, ensure_ascii=False), file=sys.stdout)
    return code


# ── 观测 ──────────────────────────────────────────────────────────────


def _status(store: RuntimeStore, _a: argparse.Namespace) -> int:
    return _ok({"view": store.snapshot()})


def _cards(store: RuntimeStore, _a: argparse.Namespace) -> int:
    return _ok({"cards": store.snapshot()["cards"]})


def _card_show(store: RuntimeStore, a: argparse.Namespace) -> int:
    view = store.snapshot()["cards"].get(a.id)
    if view is None:
        return _fail(f"卡 {a.id} 不存在（未 ratified？）")
    return _ok({"card": view, "events": [_ev(e) for e in store.ledger.by_card(a.id)]})


def _budget(store: RuntimeStore, _a: argparse.Namespace) -> int:
    view = store.snapshot()
    return _ok(
        {
            "budget": view["budget"],
            "config": view["config"],
            "card_accounts": {cid: store.budget.card_account(cid) for cid in view["cards"]},
        }
    )


def _locks(store: RuntimeStore, _a: argparse.Namespace) -> int:
    return _ok({"locks": store.snapshot()["locks"]})


def _events(store: RuntimeStore, a: argparse.Namespace) -> int:
    evs = list(store.ledger.events())
    if a.kind:
        evs = [e for e in evs if e.kind == a.kind]
    if a.card:
        evs = [e for e in evs if e.card_id == a.card]
    evs = evs[-a.tail :] if a.tail > 0 else evs
    return _ok({"events": [_ev(e) for e in evs], "count": len(evs)})


def _verify(store: RuntimeStore, _a: argparse.Namespace) -> int:
    try:
        store.verify()
    except LedgerIntegrityError as e:
        return _fail(f"账本链断：{e}")
    return _ok({"ledger_head": store.ledger.head(), "events": len(store.ledger.events())})


# ── 卡干预 ────────────────────────────────────────────────────────────


def _card_ratify(store: RuntimeStore, a: argparse.Namespace) -> int:
    try:
        if a.card_file:
            card = json.loads(Path(a.card_file).read_text(encoding="utf-8"))
        elif a.card:
            card = json.loads(a.card)
        else:
            return _fail("需要 --card-file 或 --card", 2)
    except (json.JSONDecodeError, OSError) as e:
        return _fail(f"卡 JSON 无效：{e}", 2)
    try:
        cid = store.cardgate.ratify(card, actor=a.actor)
    except CardStateError as e:
        return _fail(f"card-gate 拒绝：{e}")
    store.flush()
    return _ok({"card_id": cid, "state": store.cardgate.state(cid)})


def _card_advance(store: RuntimeStore, a: argparse.Namespace) -> int:
    try:
        to = store.cardgate.transition(a.id, a.to, actor=a.actor)
    except CardStateError as e:
        return _fail(f"非法转移：{e}")
    store.flush()
    return _ok({"card_id": a.id, "state": to})


def _card_pause(store: RuntimeStore, a: argparse.Namespace) -> int:
    try:
        store.cardgate.pause(a.id, actor=a.actor)
    except CardStateError as e:
        return _fail(f"pause 拒绝：{e}")
    store.flush()
    return _ok({"card_id": a.id, "state": "paused", "note": a.reason or ""})


def _card_resume(store: RuntimeStore, a: argparse.Namespace) -> int:
    try:
        to = store.cardgate.resume(a.id, actor=a.actor)
    except CardStateError as e:
        return _fail(f"resume 拒绝：{e}")
    store.flush()
    return _ok({"card_id": a.id, "state": to})


def _card_abort(store: RuntimeStore, a: argparse.Namespace) -> int:
    if not a.reason:
        return _fail("card-abort 需要 --reason（reason_routing 决定善后）", 2)
    try:
        store.cardgate.abort(a.id, a.reason, actor=a.actor)
    except CardStateError as e:
        return _fail(f"abort 拒绝：{e}")
    store.flush()
    return _ok({"card_id": a.id, "state": "aborted", "reason": a.reason})


# ── 预算干预 ──────────────────────────────────────────────────────────


def _budget_spend(store: RuntimeStore, a: argparse.Namespace) -> int:
    r = store.budget.spend(a.amount, category=a.category, card_id=a.card, tokens=a.tokens)
    store.flush()
    if not r.allowed:
        return _fail(r.reason or "预算拒绝", allowed=False, frozen=r.frozen)
    return _ok(
        {
            "allowed": True,
            "frozen": r.frozen,
            "reason": r.reason,
            "total_spent": round(store.budget._spent, 2),
        }
    )


def _budget_tick(store: RuntimeStore, a: argparse.Namespace) -> int:
    store.budget.tick(a.seconds)
    store.flush()
    return _ok({"frozen": store.budget.frozen, "note": "时钟推进（wall_clock 由外部推进）"})


# ── 锁干预 ────────────────────────────────────────────────────────────


def _lock_acquire(store: RuntimeStore, a: argparse.Namespace) -> int:
    try:
        store.writelock.acquire(a.seat, a.artifact)
    except WriteLockError as e:
        return _fail(f"写锁冲突：{e}", **{"holder": store.writelock.holder(a.artifact)})
    store.flush()
    return _ok({"artifact": a.artifact, "holder": a.seat, "state": "active"})


def _lock_release(store: RuntimeStore, a: argparse.Namespace) -> int:
    store.writelock.release(a.seat, a.artifact)
    store.flush()
    return _ok({"artifact": a.artifact, "released_by": a.seat})


def _lock_freeze(store: RuntimeStore, a: argparse.Namespace) -> int:
    store.writelock.freeze_holder(a.artifact)
    store.flush()
    return _ok({"artifact": a.artifact, "state": "frozen"})


# ── 账本 ──────────────────────────────────────────────────────────────


def _ledger_export(store: RuntimeStore, a: argparse.Namespace) -> int:
    text = store.ledger.to_jsonl()
    if a.path:
        p = Path(a.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
        return _ok({"exported": str(p), "events": len(store.ledger.events())})
    return _ok({"jsonl": text, "events": len(store.ledger.events())})


# ── 分发 ──────────────────────────────────────────────────────────────

# verb → (handler, argparse flags)；flags 元素 = (name, kwargs)
_HANDLERS: dict[str, tuple[Any, list[tuple[str, dict]]]] = {
    "status": (_status, []),
    "cards": (_cards, []),
    "card-show": (_card_show, [("id", {"help": "卡 id"})]),
    "budget": (_budget, []),
    "locks": (_locks, []),
    "events": (
        _events,
        [
            ("--tail", {"type": int, "default": 20, "help": "取末尾 N 条（0=全部）"}),
            ("--kind", {"default": None, "help": "按事件类型过滤"}),
            ("--card", {"default": None, "help": "按卡过滤"}),
        ],
    ),
    "verify": (_verify, []),
    "card-ratify": (
        _card_ratify,
        [
            ("--card-file", {"dest": "card_file", "default": None, "help": "卡 JSON 文件"}),
            ("--card", {"default": None, "help": "卡 JSON 内联字符串"}),
            ("--actor", {"default": "owner"}),
        ],
    ),
    "card-advance": (
        _card_advance,
        [
            ("id", {"help": "卡 id"}),
            ("--to", {"required": True, "help": "目标状态"}),
            ("--actor", {"default": "owner"}),
        ],
    ),
    "card-pause": (
        _card_pause,
        [("id", {"help": "卡 id"}), ("--reason", {"default": ""}), ("--actor", {"default": "owner"})],
    ),
    "card-resume": (_card_resume, [("id", {"help": "卡 id"}), ("--actor", {"default": "owner"})]),
    "card-abort": (
        _card_abort,
        [("id", {"help": "卡 id"}), ("--reason", {"required": True}), ("--actor", {"default": "owner"})],
    ),
    "budget-spend": (
        _budget_spend,
        [
            ("amount", {"type": float, "help": "金额 usd"}),
            ("--card", {"default": None}),
            ("--category", {"default": "card"}),
            ("--tokens", {"type": int, "default": 0}),
        ],
    ),
    "budget-tick": (_budget_tick, [("seconds", {"type": float, "help": "推进秒数"})]),
    "lock-acquire": (
        _lock_acquire,
        [("artifact", {"help": "制品路径"}), ("--seat", {"required": True, "help": "座位名"})],
    ),
    "lock-release": (
        _lock_release,
        [("artifact", {"help": "制品路径"}), ("--seat", {"required": True})],
    ),
    "lock-freeze": (_lock_freeze, [("artifact", {"help": "制品路径"})]),
    "ledger-export": (_ledger_export, [("--path", {"dest": "path", "default": None})]),
}


def dispatch(argv: list[str], state_dir: str | Path) -> int:
    """agentctl 入口：ap ctl <verb> [args...] --state <dir>。"""
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(json.dumps({"ok": True, "verbs": sorted(_HANDLERS)}, ensure_ascii=False))
        return 0
    verb, rest = argv[0], argv[1:]
    entry = _HANDLERS.get(verb)
    if entry is None:
        return _fail(f"未知动词：{verb}（合法：{sorted(_HANDLERS)}）", 2)
    handler, flags = entry
    p = argparse.ArgumentParser(prog=f"ap ctl {verb}")
    for name, kwargs in flags:
        p.add_argument(name, **kwargs)
    p.add_argument("--state", default=str(state_dir), help="state 目录")
    try:
        args = p.parse_args(rest)
    except SystemExit as e:
        return int(e.code or 2)
    try:
        store = RuntimeStore.open(args.state)
    except StoreError as e:
        return _fail(f"store 打开失败：{e}", 2)
    return handler(store, args)
