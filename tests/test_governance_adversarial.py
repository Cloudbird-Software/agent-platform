"""governance/ 对抗测试：篡改账本/绕闸/越权。"""

from __future__ import annotations

import json

import pytest

from agentplatform.governance import (
    AclTable,
    BudgetGovernor,
    CardGate,
    CardStateError,
    EventLedger,
    LedgerIntegrityError,
    WriteLock,
    WriteLockError,
)
from agentplatform.governance.cardgate import review_dispatch
from agentplatform.governance.writelock import AclError


def _card(**over) -> dict:
    base = {
        "id": "C-1",
        "wave_id": "W-1",
        "goal": "实现 X",
        "acceptance_refs": ["acc-1"],
        "capability_tags": ["py"],
        "depends_on": [],
        "risk_class": "low",
        "change_class": "logic",
        "budget": {"tokens": 1000},
    }
    base.update(over)
    return base


def _chain(n: int = 4) -> EventLedger:
    led = EventLedger()
    for i in range(n):
        led.append(f"evt.{i}", "tester", {"i": i}, ts=float(i))
    return led


# ---- A1 账本篡改 ----


def test_a1_payload_tamper_detected() -> None:
    led = _chain()
    events = led.events()
    # 直接构造被篡改的账本（绕过 append）
    tampered = list(events)
    d = json.loads(
        json.dumps(
            {
                "seq": tampered[1].seq,
                "kind": tampered[1].kind,
                "actor": tampered[1].actor,
                "hash": tampered[1].hash,
                "prev_hash": tampered[1].prev_hash,
                "ts": tampered[1].ts,
                "card_id": tampered[1].card_id,
                "payload": {"i": 999},
            }
        )
    )
    from agentplatform.governance.ledger import GovernanceEvent

    bad = EventLedger([tampered[0], GovernanceEvent(**d), tampered[2], tampered[3]])
    with pytest.raises(LedgerIntegrityError) as ei:
        bad.verify()
    assert ei.value.seq == 1  # 报首个断点


def test_a1_event_removal_detected() -> None:
    events = _chain().events()
    holed = EventLedger([events[0], events[2], events[3]])  # 挖掉 seq=1
    with pytest.raises(LedgerIntegrityError):
        holed.verify()


def test_a1_event_reorder_detected() -> None:
    events = _chain().events()
    swapped = EventLedger([events[0], events[2], events[1], events[3]])
    with pytest.raises(LedgerIntegrityError):
        swapped.verify()


def test_a1_corrupt_jsonl_line(tmp_path) -> None:
    p = _chain().save(tmp_path / "t.jsonl")
    text = p.read_text(encoding="utf-8").splitlines()
    text[1] = "{broken json"
    p.write_text("\n".join(text) + "\n", encoding="utf-8")
    with pytest.raises(LedgerIntegrityError):
        EventLedger.load(p)


def test_a1_genesis_forgery_detected() -> None:
    led = EventLedger()
    led.append("evt.0", "t", {"i": 0}, ts=0.0)
    # 伪造首事件（prev_hash 不指向 GENESIS）
    from agentplatform.governance.ledger import GovernanceEvent, _event_hash

    h = _event_hash("f" * 64, 0, "evt.0", "t", None, {"i": 0}, 0.0)
    with pytest.raises(LedgerIntegrityError):
        EventLedger([GovernanceEvent(0, "evt.0", "t", h, "f" * 64, 0.0)]).verify()


# ---- A2 卡门绕闸 ----


def test_a2_missing_fields_rejected() -> None:
    gate = CardGate(EventLedger())
    for f in ("id", "goal", "acceptance_refs", "change_class", "budget"):
        card = _card()
        card.pop(f)
        with pytest.raises(CardStateError, match=f):
            gate.ratify(card)


def test_a2_empty_acceptance_refs_rejected() -> None:
    gate = CardGate(EventLedger())
    with pytest.raises(CardStateError, match="验收引用"):
        gate.ratify(_card(acceptance_refs=[]))


def test_a2_double_ratify_rejected() -> None:
    gate = CardGate(EventLedger())
    gate.ratify(_card())
    with pytest.raises(CardStateError, match="重复"):
        gate.ratify(_card())


def test_a2_illegal_transition_rejected() -> None:
    gate = CardGate(EventLedger())
    gate.ratify(_card())
    with pytest.raises(CardStateError, match="不是合法转移"):
        gate.transition("C-1", "merged", actor="mechanism:scheduler")  # ratified → merged 跳相位


def test_a2_abort_without_reason_rejected() -> None:
    gate = CardGate(EventLedger())
    gate.ratify(_card())
    with pytest.raises(CardStateError, match="reason"):
        gate.abort("C-1", "")


def test_a2_unknown_change_class_rejected() -> None:
    gate = CardGate(EventLedger())
    with pytest.raises(CardStateError, match="change_class"):
        gate.ratify(_card(change_class="mega"))
    with pytest.raises(CardStateError):
        review_dispatch("mega")


def test_a2_unratified_card_cannot_transition() -> None:
    gate = CardGate(EventLedger())
    with pytest.raises(CardStateError, match="未入闸"):
        gate.transition("C-404", "building", actor="mechanism:scheduler")


# ---- A3 预算绕闸 ----


def test_a3_negative_spend_rejected() -> None:
    gov = BudgetGovernor(envelope_usd=10.0, overhead_usd=5.0, ledger=EventLedger())
    r = gov.spend(-5.0, card_id="C-1")
    assert not r.allowed  # 负支出=提款攻击


def test_a3_frozen_denies_normal_allows_escalation() -> None:
    led = EventLedger()
    gov = BudgetGovernor(envelope_usd=1.0, overhead_usd=1.0, ledger=led)
    gov.spend(2.0, card_id="C-1")  # 熔断
    assert gov.frozen
    denied = [r for r in (gov.spend(0.1, card_id="C-1"),) if not r.allowed]
    assert denied
    assert gov.spend(0.1, category="judge").allowed  # 不变式优先于熔断
    assert led.kinds().get("budget.denied") == 1


def test_a3_exempt_category_not_counted_as_envelope() -> None:
    gov = BudgetGovernor(envelope_usd=10.0, overhead_usd=100.0, ledger=EventLedger())
    for _ in range(50):
        gov.spend(1.0, category="escalation")
    assert not gov.frozen  # escalation 不吃 envelope


# ---- A4 越权 ----


def test_a4_acl_default_deny_unmatched() -> None:
    acl = AclTable.from_channels({"cards.*": {"read": ["scheduler"], "write": ["scheduler"]}})
    with pytest.raises(AclError):
        acl.require("scheduler", "events.evt-1", "write")  # 未声明通道=拒绝


def test_a4_read_does_not_grant_write() -> None:
    acl = AclTable.from_channels({"cards.*": {"read": ["team_members"], "write": ["scheduler"]}})
    assert acl.check("team_members", "cards.C-1", "read")
    with pytest.raises(AclError):
        acl.require("team_members", "cards.C-1", "write")


def test_a4_writelock_wrong_holder_release_noop() -> None:
    lock = WriteLock(EventLedger())
    lock.acquire("planner", "contracts/W-1.yaml")
    lock.release("builder", "contracts/W-1.yaml")  # 非持有者 release 无效
    with pytest.raises(WriteLockError):
        lock.acquire("builder", "contracts/W-1.yaml")  # planner 仍持有


def test_a4_two_holders_same_artifact_conflict() -> None:
    lock = WriteLock(EventLedger())
    lock.acquire("test_author", "tests/C-1/")
    with pytest.raises(WriteLockError):
        lock.acquire("builder", "tests/C-1/")
