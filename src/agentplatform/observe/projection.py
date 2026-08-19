"""事件投影：账本事件流 → 只读状态视图（TUI 与查询命令共用的唯一推导）。"""

from __future__ import annotations

from typing import Any

from agentplatform.governance.ledger import EventLedger

RECENT_WINDOW = 20


def project(ledger: EventLedger) -> dict[str, Any]:
    """重放事件流，推导：卡状态/预算账面/写锁/事件统计/最近事件。

    纯函数（不动账本）——与 governance 对象的重放恢复（from_events）共享同一
    事件语义；两处推导不一致 = 观测面失真，由 test_observe 对账测试钉死。
    """
    cards: dict[str, dict[str, Any]] = {}
    spent = 0.0
    overhead_spent = 0.0
    frozen = False
    freeze_reason = ""
    locks: dict[str, dict[str, str]] = {}
    kinds: dict[str, int] = {}
    recent: list[dict[str, Any]] = []
    denials = 0
    total = 0

    for e in ledger.events():
        total += 1
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
        recent.append({"seq": e.seq, "kind": e.kind, "actor": e.actor, "card_id": e.card_id, "ts": e.ts})
        if len(recent) > RECENT_WINDOW:
            recent.pop(0)

        if e.kind == "cards.ratified" and e.card_id:
            card = e.payload.get("card") or {}
            cards[e.card_id] = {
                "state": "ratified",
                "change_class": str(card.get("change_class", "?")),
                "goal": str(card.get("goal", "")),
                "wave_id": str(card.get("wave_id", "")),
                "budget_usd": float((card.get("budget") or {}).get("usd", 0) or 0),
                "card_usd": 0.0,
            }
        elif e.kind.startswith("card.") and e.card_id:
            card = cards.setdefault(
                e.card_id,
                {
                    "state": "?",
                    "change_class": "?",
                    "goal": "",
                    "wave_id": "",
                    "budget_usd": 0.0,
                    "card_usd": 0.0,
                },
            )
            new_state = e.kind.removeprefix("card.")
            if new_state == "resumed":
                card["state"] = e.payload.get("to", "building")
            else:
                card["state"] = new_state
        elif e.kind == "budget.spent":
            amt = float(e.payload.get("amount", 0))
            if e.payload.get("exempt"):
                overhead_spent += amt
            else:
                spent += amt
                if e.card_id and e.card_id in cards:
                    cards[e.card_id]["card_usd"] = round(cards[e.card_id]["card_usd"] + amt, 2)
        elif e.kind == "budget.denied":
            denials += 1
        elif e.kind == "wave.frozen":
            frozen = True
            freeze_reason = e.payload.get("reason", "")
        elif e.kind == "writelock.acquired":
            art = e.payload.get("artifact", "?")
            locks[art] = {"holder": e.payload.get("seat", e.actor), "state": "active"}
        elif e.kind == "writelock.frozen":
            art = e.payload.get("artifact", "?")
            if art in locks:
                locks[art]["state"] = "frozen"
        elif e.kind == "writelock.released":
            locks.pop(e.payload.get("artifact", "?"), None)

    return {
        "events_total": total,
        "kinds": kinds,
        "cards": cards,
        "budget": {
            "spent": round(spent, 2),
            "overhead_spent": round(overhead_spent, 2),
            "frozen": frozen,
            "freeze_reason": freeze_reason,
            "denials": denials,
        },
        "locks": locks,
        "recent": recent,
    }
