"""卡门（card-gate）：卡批准闸 + review dispatch 表 + 卡生命周期状态机。

声明依据（team-collaboration artifacts.card + verdict_layers.review）：
- fields_required：[id, wave_id, goal, acceptance_refs, capability_tags,
  depends_on, risk_class, change_class, budget]——无验收引用的卡不允许存在；
- review dispatch（机器可判定）：doc/trivial→waived；logic→test_author；
  spike→curator；dep/schema→test_author+owner_ratify 第三钥；
  prod→test_author+owner_per_action；
- lifecycle states：drafting→testability_review→ratified→building→verify
  →merged/archived/aborted（paused 可从 building/verify 进入）；
- owner 控制动词：card.paused/card.resumed/card.aborted（reason 必填）。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentplatform.governance.ledger import EventLedger

CARD_FIELDS_REQUIRED: tuple[str, ...] = (
    "id",
    "wave_id",
    "goal",
    "acceptance_refs",
    "capability_tags",
    "depends_on",
    "risk_class",
    "change_class",
    "budget",
)

CHANGE_CLASSES = frozenset({"doc", "trivial", "logic", "spike", "dep", "schema", "prod"})

# review dispatch 表（声明 verdict_layers.review.dispatch 的机器形态）
_DISPATCH: dict[str, dict] = {
    "doc": {"mode": "waived"},
    "trivial": {"mode": "waived"},
    "logic": {"mode": "seat", "seat": "test_author"},
    "spike": {"mode": "seat", "seat": "curator"},
    "dep": {"mode": "seat", "seat": "test_author", "extra_keys": ["owner_ratify"]},
    "schema": {"mode": "seat", "seat": "test_author", "extra_keys": ["owner_ratify"]},
    "prod": {"mode": "seat", "seat": "test_author", "extra_keys": ["owner_per_action"]},
}

# lifecycle 状态转移表（无 owner 干预的主路径 + owner 控制动词由方法显式表达）
_TRANSITIONS: dict[str, frozenset[str]] = {
    "drafting": frozenset({"testability_review", "aborted"}),
    "testability_review": frozenset({"ratified", "drafting", "aborted"}),
    "ratified": frozenset({"building", "aborted"}),
    "building": frozenset({"verify", "paused", "aborted"}),
    "verify": frozenset({"building", "integrate", "paused", "aborted"}),
    "integrate": frozenset({"merged", "aborted"}),
    "paused": frozenset({"building", "verify", "aborted"}),  # resume 回原相位
    "merged": frozenset({"archived"}),
    "archived": frozenset(),
    "aborted": frozenset(),
}


class CardStateError(Exception):
    """非法转移/非法卡结构——card_gate 拒绝的理由。"""


@dataclass(frozen=True)
class ReviewDispatch:
    change_class: str
    mode: str
    seat: str | None = None
    extra_keys: tuple[str, ...] = ()


def review_dispatch(change_class: str) -> ReviewDispatch:
    d = _DISPATCH.get(change_class)
    if d is None:
        raise CardStateError(f"未知 change_class：{change_class}（合法：{sorted(CHANGE_CLASSES)}）")
    return ReviewDispatch(
        change_class=change_class,
        mode=d["mode"],
        seat=d.get("seat"),
        extra_keys=tuple(d.get("extra_keys") or ()),
    )


class CardGate:
    """ratify=入闸（校验+事件）；transition=生命周期推进；owner 控制显式方法。"""

    def __init__(self, ledger: EventLedger) -> None:
        self._ledger = ledger
        self._states: dict[str, str] = {}
        self._cards: dict[str, dict] = {}

    # ---- 批准闸 ----
    def ratify(self, card: dict, *, actor: str = "mechanism:card-gate", ts: float | None = None) -> str:
        card_id = card.get("id")
        if not isinstance(card_id, str) or not card_id:
            raise CardStateError("卡缺 id")
        if card_id in self._cards:
            raise CardStateError(f"卡 {card_id} 已 ratified（重复批准）")
        # 缺失=None/未填/空串；空列表合法（depends_on 无依赖、acceptance_refs
        # 另有非空专项检查——"无验收引用的卡不允许存在"）
        missing = [f for f in CARD_FIELDS_REQUIRED if f not in card or card[f] is None or card[f] == ""]
        if missing:
            raise CardStateError(f"卡 {card_id} 缺必填字段：{missing}")
        refs = card.get("acceptance_refs")
        if not isinstance(refs, list) or not refs:
            raise CardStateError("acceptance_refs 必须非空——无验收引用的卡不允许存在")
        cc = card.get("change_class")
        if cc not in CHANGE_CLASSES:
            raise CardStateError(f"卡 {card_id} change_class={cc!r} 不在 {sorted(CHANGE_CLASSES)}")
        if not isinstance(card.get("budget"), dict):
            raise CardStateError(f"卡 {card_id} budget 必须是映射（tokens/wall_clock/retries/usd）")

        self._cards[card_id] = dict(card)
        self._states[card_id] = "ratified"
        self._ledger.append("cards.ratified", actor, {"fields": sorted(card)}, card_id=card_id, ts=ts)
        return card_id

    # ---- 生命周期 ----
    def state(self, card_id: str) -> str:
        return self._states.get(card_id, "unknown")

    def transition(self, card_id: str, to_state: str, *, actor: str, ts: float | None = None) -> str:
        cur = self.state(card_id)
        if cur == "unknown":
            raise CardStateError(f"卡 {card_id} 未入闸（先 ratify）")
        if to_state not in _TRANSITIONS.get(cur, frozenset()):
            raise CardStateError(f"卡 {card_id}：{cur} → {to_state} 不是合法转移")
        self._states[card_id] = to_state
        self._ledger.append(f"card.{to_state}", actor, {"from": cur}, card_id=card_id, ts=ts)
        return to_state

    # ---- owner 控制动词（flows#owner_control——经平台通道落事件）----
    def pause(self, card_id: str, *, actor: str = "owner", ts: float | None = None) -> str:
        return self.transition(card_id, "paused", actor=actor, ts=ts)

    def resume(self, card_id: str, *, actor: str = "owner", ts: float | None = None) -> str:
        if self.state(card_id) != "paused":
            raise CardStateError(f"卡 {card_id} 不在 paused 态（resume 前置失败）")
        # resume 回原相位：取 card.paused 事件的 from 字段
        origin = "building"
        for e in reversed(self._ledger.by_card(card_id)):
            if e.kind == "card.paused":
                origin = e.payload.get("from", "building")
                break
        self._states[card_id] = origin
        self._ledger.append("card.resumed", actor, {"to": origin}, card_id=card_id, ts=ts)
        return origin

    def abort(self, card_id: str, reason: str, *, actor: str = "owner", ts: float | None = None) -> str:
        if not reason:
            raise CardStateError("card.aborted reason 必填（reason_routing 决定善后）")
        return self.transition(card_id, "aborted", actor=actor, ts=ts)

    def _require_transition(self, card_id: str, to_state: str) -> None:
        cur = self.state(card_id)
        if to_state not in _TRANSITIONS.get(cur, frozenset()):
            raise CardStateError(f"卡 {card_id}：{cur} → {to_state} 不是合法转移")

    # ---- 读 ----
    def card(self, card_id: str) -> dict:
        return dict(self._cards[card_id])
