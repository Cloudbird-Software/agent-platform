"""observe/ 测试：RuntimeStore 往返、投影对账、篡改拒绝、TUI、agentctl 全动词。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentplatform.governance.ledger import LedgerIntegrityError
from agentplatform.observe import RuntimeStore, project
from agentplatform.observe.agentctl import dispatch
from agentplatform.observe.store import StoreError
from agentplatform.observe.tui import render_dashboard, run_tui


def _card(cid: str = "C-1", change_class: str = "logic") -> dict:
    return {
        "id": cid,
        "wave_id": "W-1",
        "goal": "实现 X",
        "acceptance_refs": ["tests/test_x.py"],
        "capability_tags": ["python"],
        "depends_on": [],
        "risk_class": "low",
        "change_class": change_class,
        "budget": {"tokens": 1000, "wall_clock": 600, "retries": 2, "usd": 5.0},
    }


@pytest.fixture()
def store(tmp_path: Path) -> RuntimeStore:
    return RuntimeStore.create(tmp_path / "state", envelope_usd=100.0, overhead_usd=20.0)


# ── RuntimeStore：建/开/恢复 ─────────────────────────────────────────


class TestRuntimeStore:
    def test_create_writes_state_files(self, store: RuntimeStore) -> None:
        assert (store.dir / "ledger.jsonl").is_file()
        assert (store.dir / "meta.json").is_file()
        assert store.ledger.events()[0].kind == "store.created"

    def test_create_rejects_existing(self, store: RuntimeStore) -> None:
        with pytest.raises(StoreError, match="open"):
            RuntimeStore.create(store.dir, envelope_usd=10)

    def test_open_replays_full_state(self, store: RuntimeStore, tmp_path: Path) -> None:
        store.cardgate.ratify(_card())
        store.cardgate.transition("C-1", "building", actor="mechanism:scheduler")
        store.budget.spend(3.5, card_id="C-1", tokens=500)
        store.budget.spend(1.0, category="escalation")  # exempt
        store.writelock.acquire("seat:builder-1", "contracts/api.md")
        store.writelock.freeze_holder("contracts/api.md")
        store.flush()

        reopened = RuntimeStore.open(store.dir)
        assert reopened.cardgate.state("C-1") == "building"
        assert reopened.cardgate.card("C-1")["change_class"] == "logic"
        assert reopened.budget.card_account("C-1") == {
            "usd": 3.5,
            "tokens": 500,
            "retries_used": 0,
            "wall_clock": 0.0,
        }
        assert reopened.budget._overhead_spent == 1.0
        lock = reopened.writelock.holders()["contracts/api.md"]
        assert lock == {"holder": "seat:builder-1", "state": "frozen"}

    def test_open_rejects_tampered_ledger(self, store: RuntimeStore) -> None:
        store.cardgate.ratify(_card())
        store.flush()
        p = store.dir / "ledger.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines()
        # 篡改历史事件（重写第 2 行的 payload）
        d = json.loads(lines[1])
        d["payload"]["card"]["goal"] = "恶意目标"
        lines[1] = json.dumps(d, ensure_ascii=False, sort_keys=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(StoreError, match="完整性失败"):
            RuntimeStore.open(store.dir)

    def test_open_rejects_missing(self, tmp_path: Path) -> None:
        with pytest.raises(StoreError, match="不存在"):
            RuntimeStore.open(tmp_path / "nope")

    def test_incremental_append_after_reopen(self, store: RuntimeStore) -> None:
        store.cardgate.ratify(_card())
        store.flush()
        reopened = RuntimeStore.open(store.dir)
        reopened.cardgate.transition("C-1", "building", actor="mechanism:scheduler")
        reopened.flush()
        again = RuntimeStore.open(store.dir)
        assert again.cardgate.state("C-1") == "building"
        again.ledger.verify()  # 接链完整


# ── 投影 vs 治理对象：观测面=治理面（对账）────────────────────────────


class TestProjectionConsistency:
    def test_projection_matches_governance_objects(self, store: RuntimeStore) -> None:
        store.cardgate.ratify(_card("C-1"))
        store.cardgate.ratify(_card("C-2", change_class="prod"))
        store.cardgate.transition("C-1", "building", actor="mechanism:scheduler")
        store.cardgate.pause("C-1")
        store.budget.spend(2.0, card_id="C-1")
        store.budget.spend(0.5, category="judge")  # exempt → overhead
        store.writelock.acquire("seat:b1", "contracts/api.md")

        view = project(store.ledger)
        assert view["cards"]["C-1"]["state"] == "paused"
        assert view["cards"]["C-2"]["state"] == "ratified"
        assert view["cards"]["C-1"]["card_usd"] == 2.0
        assert view["budget"]["spent"] == 2.0
        assert view["budget"]["overhead_spent"] == 0.5
        assert view["locks"]["contracts/api.md"]["holder"] == "seat:b1"
        # 与治理对象对账
        assert view["cards"]["C-1"]["state"] == store.cardgate.state("C-1")
        assert view["budget"]["spent"] == round(store.budget._spent, 2)
        assert view["locks"] == store.writelock.holders()

    def test_projection_tracks_freeze(self, store: RuntimeStore) -> None:
        store.budget.spend(999.0)  # 熔断
        view = project(store.ledger)
        assert view["budget"]["frozen"] is True
        assert "envelope" in view["budget"]["freeze_reason"]

    def test_projection_card_lifecycle(self, store: RuntimeStore) -> None:
        store.cardgate.ratify(_card())
        for st in ("building", "verify", "integrate", "merged"):
            store.cardgate.transition("C-1", st, actor="mechanism:scheduler")
        assert project(store.ledger)["cards"]["C-1"]["state"] == "merged"


# ── TUI ──────────────────────────────────────────────────────────────


class TestTui:
    def test_dashboard_contains_sections(self, store: RuntimeStore) -> None:
        store.cardgate.ratify(_card())
        store.writelock.acquire("seat:b1", "contracts/api.md")
        store.budget.spend(10.0, card_id="C-1")
        frame = render_dashboard(store.snapshot(), color=False)
        assert "C-1" in frame  # 卡行（ratified）
        assert "seat:b1" in frame  # 锁持有者
        assert "contracts/api.md" in frame
        assert "envelope" in frame
        assert "WAVE ACTIVE" in frame

    def test_dashboard_frozen_highlight(self, store: RuntimeStore) -> None:
        store.budget.spend(999.0)
        frame = render_dashboard(store.snapshot(), color=False)
        assert "WAVE FROZEN" in frame

    def test_dashboard_color_vs_plain(self, store: RuntimeStore) -> None:
        store.cardgate.ratify(_card())
        colored = render_dashboard(store.snapshot(), color=True)
        plain = render_dashboard(store.snapshot(), color=False)
        assert "\033[" in colored
        assert "\033[" not in plain

    def test_run_tui_renders_frames(self, store: RuntimeStore) -> None:
        import io

        store.cardgate.ratify(_card())
        store.flush()
        buf = io.StringIO()
        run_tui(
            lambda: RuntimeStore.open(store.dir),
            interval_s=0,
            rounds=2,
            color=False,
            stream=buf,
        )
        out = buf.getvalue()
        assert out.count("卡（1）") == 2  # 两帧
        assert "C-1" in out


# ── agentctl：干预动词 ────────────────────────────────────────────────


class TestAgentCtl:
    def _card_json(self, tmp_path: Path) -> str:
        p = tmp_path / "card.json"
        p.write_text(json.dumps(_card(), ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_verb_listing(self, tmp_path: Path, capsys) -> None:
        rc = dispatch(["help"], tmp_path / "s")
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and "card-pause" in out["verbs"] and "budget-spend" in out["verbs"]

    def test_unknown_verb(self, tmp_path: Path, capsys) -> None:
        rc = dispatch(["explode"], tmp_path / "s")
        body = json.loads(capsys.readouterr().out)
        assert rc == 2 and body["ok"] is False

    def test_store_missing_fails_closed(self, tmp_path: Path, capsys) -> None:
        rc = dispatch(["status"], tmp_path / "nope")
        body = json.loads(capsys.readouterr().out)
        assert rc == 2 and "store" in body["error"]

    def test_full_card_lifecycle_via_ctl(self, store: RuntimeStore, tmp_path: Path, capsys) -> None:
        d = str(store.dir)
        assert dispatch(["card-ratify", "--card-file", self._card_json(tmp_path), "--state", d], d) == 0
        assert dispatch(["card-advance", "C-1", "--to", "building", "--state", d], d) == 0
        assert dispatch(["card-pause", "C-1", "--reason", "等 owner", "--state", d], d) == 0
        assert dispatch(["card-resume", "C-1", "--state", d], d) == 0
        capsys.readouterr()
        rc = dispatch(["cards", "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert rc == 0 and body["cards"]["C-1"]["state"] == "building"

    def test_illegal_transition_rejected(self, store: RuntimeStore, tmp_path: Path, capsys) -> None:
        d = str(store.dir)
        dispatch(["card-ratify", "--card-file", self._card_json(tmp_path), "--state", d], d)
        capsys.readouterr()
        rc = dispatch(["card-advance", "C-1", "--to", "merged", "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert rc == 1 and "非法转移" in body["error"]

    def test_abort_requires_reason(self, store: RuntimeStore, tmp_path: Path) -> None:
        d = str(store.dir)
        assert dispatch(["card-abort", "C-1", "--state", d], d) == 2

    def test_budget_spend_and_freeze(self, store: RuntimeStore, capsys) -> None:
        d = str(store.dir)
        rc = dispatch(["budget-spend", "90.0", "--card", "X", "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert rc == 0 and body["allowed"] is True and body["total_spent"] == 90.0
        rc = dispatch(["budget-spend", "50.0", "--card", "X", "--state", d], d)  # 熔断
        body = json.loads(capsys.readouterr().out)
        assert rc == 1 and body["ok"] is False and body["frozen"] is True
        # 升级通道豁免：冻结后 escalation 仍放行
        rc = dispatch(["budget-spend", "5.0", "--category", "escalation", "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert rc == 0 and body["allowed"] is True

    def test_lock_conflict_reports_holder(self, store: RuntimeStore, capsys) -> None:
        d = str(store.dir)
        dispatch(["lock-acquire", "a.md", "--seat", "seat:b1", "--state", d], d)
        capsys.readouterr()
        rc = dispatch(["lock-acquire", "a.md", "--seat", "seat:b2", "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert rc == 1 and body.get("holder") == "seat:b1"
        # freeze 后不再互斥（amendment 语义）
        dispatch(["lock-freeze", "a.md", "--state", d], d)
        dispatch(["lock-acquire", "a.md", "--seat", "seat:b2", "--state", d], d)
        capsys.readouterr()
        rc = dispatch(["locks", "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert body["locks"]["a.md"]["holder"] == "seat:b2"

    def test_events_filter_and_verify_export(self, store: RuntimeStore, tmp_path: Path, capsys) -> None:
        d = str(store.dir)
        dispatch(["card-ratify", "--card-file", self._card_json(tmp_path), "--state", d], d)
        capsys.readouterr()
        rc = dispatch(["events", "--kind", "cards.ratified", "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert rc == 0 and body["count"] == 1 and body["events"][0]["kind"] == "cards.ratified"
        dispatch(["verify", "--state", d], d)
        capsys.readouterr()
        export_path = tmp_path / "export.jsonl"
        rc = dispatch(["ledger-export", "--path", str(export_path), "--state", d], d)
        body = json.loads(capsys.readouterr().out)
        assert rc == 0 and export_path.is_file() and body["events"] >= 2

    def test_verify_detects_tamper(self, store: RuntimeStore, capsys) -> None:
        store.flush()
        p = store.dir / "ledger.jsonl"
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # 追加空行无害
        dispatch(["verify", "--state", str(store.dir)], str(store.dir))
        capsys.readouterr()
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        lines[0] = lines[0].replace("store.created", "store.deleted")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # open 阶段 fail-closed——verify 命令拿不到 store
        rc = dispatch(["verify", "--state", str(store.dir)], str(store.dir))
        body = json.loads(capsys.readouterr().out)
        assert rc == 2 and "store" in body["error"]

    def test_budget_tick(self, store: RuntimeStore, capsys) -> None:
        rc = dispatch(["budget-tick", "120.0", "--state", str(store.dir)], str(store.dir))
        assert json.loads(capsys.readouterr().out)["ok"] is True
        assert rc == 0


# ── from_events 与 governance 单元（重放语义钉死）────────────────────


class TestReplaySemantics:
    def test_ledger_integrity_still_enforced(self, store: RuntimeStore) -> None:
        with pytest.raises(LedgerIntegrityError):
            _tamper_and_verify(store)

    def test_cardgate_from_events_card_data(self, store: RuntimeStore) -> None:
        store.cardgate.ratify(_card("C-9", change_class="prod"))
        gate = type(store.cardgate).from_events(store.ledger)
        assert gate.card("C-9")["budget"]["usd"] == 5.0
        assert gate.state("C-9") == "ratified"


def _tamper_and_verify(store: RuntimeStore) -> None:
    from agentplatform.governance.ledger import EventLedger, GovernanceEvent

    evs = list(store.ledger.events())
    bad = GovernanceEvent(
        seq=evs[0].seq,
        kind=evs[0].kind,
        actor="hacker",
        hash=evs[0].hash,
        prev_hash=evs[0].prev_hash,
        ts=evs[0].ts,
        card_id=evs[0].card_id,
        payload=evs[0].payload,
    )
    EventLedger([bad, *list(evs[1:])]).verify()
