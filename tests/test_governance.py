"""governance/ 功能测试：账本/卡门/预算/写锁的声明语义落地。"""

from __future__ import annotations

import pytest

from agentplatform.governance import (
    AclTable,
    BudgetGovernor,
    CardGate,
    EventLedger,
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
        "budget": {"tokens": 1000, "retries": 3},
    }
    base.update(over)
    return base


# ---- ledger ----


def test_ledger_chains_and_roundtrip(tmp_path) -> None:
    led = EventLedger()
    led.append("intent.received", "mechanism:interface-gateway", {"text": "hi"}, ts=1.0)
    led.append("cards.ratified", "mechanism:card-gate", {"fields": ["id"]}, card_id="C-1", ts=2.0)
    led.append("gate.pass", "mechanism:verifier", {}, card_id="C-1", ts=3.0)
    led.verify()
    assert led.head() != "0" * 64
    assert led.kinds() == {"intent.received": 1, "cards.ratified": 1, "gate.pass": 1}
    assert len(led.by_card("C-1")) == 2

    p = led.save(tmp_path / "sub" / "trace.jsonl")
    loaded = EventLedger.load(p)
    assert loaded.to_jsonl() == led.to_jsonl()
    loaded.verify()


# ---- cardgate ----


def test_cardgate_ratify_and_dispatch() -> None:
    led = EventLedger()
    gate = CardGate(led)
    gate.ratify(_card(), ts=1.0)
    assert gate.state("C-1") == "ratified"
    assert led.kinds()["cards.ratified"] == 1

    d = review_dispatch("logic")
    assert (d.mode, d.seat) == ("seat", "test_author")
    assert review_dispatch("trivial").mode == "waived"
    assert review_dispatch("spike").seat == "curator"
    assert "owner_ratify" in review_dispatch("dep").extra_keys
    assert "owner_per_action" in review_dispatch("prod").extra_keys


def test_cardgate_lifecycle_main_path() -> None:
    led = EventLedger()
    gate = CardGate(led)
    gate.ratify(_card(), ts=1.0)
    for to in ("building", "verify", "integrate", "merged", "archived"):
        gate.transition("C-1", to, actor="mechanism:scheduler", ts=2.0)
    assert gate.state("C-1") == "archived"


def test_cardgate_pause_resume_returns_to_origin() -> None:
    led = EventLedger()
    gate = CardGate(led)
    gate.ratify(_card(), ts=1.0)
    gate.transition("C-1", "building", actor="mechanism:scheduler", ts=2.0)
    gate.pause("C-1", ts=3.0)
    assert gate.state("C-1") == "paused"
    assert gate.resume("C-1", ts=4.0) == "building"


# ---- budget ----


def test_budget_envelope_and_freeze() -> None:
    led = EventLedger()
    gov = BudgetGovernor(envelope_usd=100.0, overhead_usd=20.0, ledger=led)
    assert gov.spend(60.0, card_id="C-1").allowed
    assert gov.spend(60.0, card_id="C-1").allowed is False  # 120 > 100
    assert gov.frozen
    assert led.kinds().get("wave.frozen") == 1
    # 冻结后：普通支出拒，escalation 放行（不变式）
    assert gov.spend(1.0, card_id="C-1").allowed is False
    assert gov.spend(1.0, category="escalation").allowed
    assert gov.spend(1.0, category="judge").allowed


def test_budget_exempt_survives_overhead_exhaustion() -> None:
    led = EventLedger()
    gov = BudgetGovernor(envelope_usd=10.0, overhead_usd=1.0, ledger=led)
    for _ in range(5):
        assert gov.spend(1.0, category="escalation").allowed  # overhead 超限也放行


def test_budget_retries_exhausted_event() -> None:
    led = EventLedger()
    gov = BudgetGovernor(envelope_usd=100.0, overhead_usd=10.0, ledger=led)
    gov.spend(1.0, card_id="C-1")
    assert gov.retry("C-1", max_retries=3).allowed
    assert gov.retry("C-1", max_retries=3).allowed
    r = gov.retry("C-1", max_retries=3)
    assert not r.allowed
    assert led.kinds().get("retries.exhausted") == 1


def test_budget_wall_clock_freeze() -> None:
    led = EventLedger()
    gov = BudgetGovernor(envelope_usd=100.0, overhead_usd=10.0, ledger=led, wall_clock_cap_s=3600.0)
    gov.tick(1800.0)
    assert not gov.frozen
    gov.tick(1800.0)
    assert gov.frozen
    assert led.kinds().get("wave.frozen") == 1


# ---- writelock ----

_ACL = {
    "cards.*": {"read": ["team_members", "scheduler"], "write": ["scheduler"]},
    "findings.*": {"read": ["owner", "curator", "adversary"], "write": ["adversary", "mechanism:verifier"]},
    "backlog.*": {"read": ["planner", "curator", "owner"], "write": ["curator"]},
}


def test_acl_first_match_and_default_deny() -> None:
    acl = AclTable.from_channels(_ACL)
    assert acl.check("scheduler", "cards.C-1", "write")
    assert acl.check("team_members", "cards.C-1", "read")
    assert not acl.check("team_members", "cards.C-1", "write")
    assert acl.check("curator", "backlog.proposals.9", "write")
    assert not acl.check("adversary", "backlog.proposals.9", "write")  # 不直写提案
    assert not acl.check("owner", "unknown.channel", "read")  # 无匹配=默认拒绝
    with pytest.raises(AclError):
        acl.require("builder", "findings.F-1", "write")  # builder 无 findings 写权


def test_writelock_exclusion() -> None:
    led = EventLedger()
    lock = WriteLock(led)
    lock.acquire("planner", "contracts/W-1.yaml", ts=1.0)
    with pytest.raises(WriteLockError):
        lock.acquire("builder", "contracts/W-1.yaml", ts=2.0)
    # frozen 持有者不互斥（amendment：builder frozen）
    lock.freeze_holder("contracts/W-1.yaml")
    lock.acquire("builder", "contracts/W-1.yaml", ts=3.0)
    assert lock.holder("contracts/W-1.yaml") == "builder"
    lock.release("builder", "contracts/W-1.yaml", ts=4.0)
    assert lock.holder("contracts/W-1.yaml") is None


def test_writelock_events_in_ledger() -> None:
    led = EventLedger()
    lock = WriteLock(led)
    lock.acquire("planner", "contracts/W-1.yaml", ts=1.0)
    lock.release("planner", "contracts/W-1.yaml", ts=2.0)
    assert led.kinds().get("writelock.acquired") == 1
    assert led.kinds().get("writelock.released") == 1
