"""adapter/ 测试：轨道/闸/观察桥/模型解析零上游直测；runner 装配用 mock 上游。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from agentplatform.adapter.gate import BudgetAdmission, BudgetFrozenError
from agentplatform.adapter.modelresolver import GatewayModelResolver, ModelResolutionError
from agentplatform.adapter.observer import LedgerObserver
from agentplatform.adapter.rails import ToolRails
from agentplatform.observe import RuntimeStore


@pytest.fixture()
def store(tmp_path: Path) -> RuntimeStore:
    return RuntimeStore.create(tmp_path / "state", envelope_usd=50.0)


# ── ToolRails ────────────────────────────────────────────────────────


class TestToolRails:
    def test_pattern_allow_and_fail_closed(self) -> None:
        rails = ToolRails(["file.*", "git.pr"])
        assert rails.check("file.read") is True
        assert rails.check("git.pr") is True
        assert rails.check("shell.exec") is False  # 无匹配即拒绝
        assert rails.filter(["file.read", "shell.exec", "git.pr"]) == ["file.read", "git.pr"]

    def test_empty_patterns_denies_all(self) -> None:
        rails = ToolRails([])
        assert rails.check("file.read") is False  # 声明缺位不是放行理由

    def test_audit_split(self) -> None:
        rails = ToolRails(["web.*"])
        a = rails.audit(["web.search", "file.write"])
        assert a == {"allowed": ["web.search"], "denied": ["file.write"]}


# ── BudgetAdmission（AgentAdmission Protocol 鸠类型）────────────────


class TestBudgetAdmission:
    async def test_admit_and_ledger_events(self, store: RuntimeStore) -> None:
        gate = BudgetAdmission(store, card_id="C-1")
        async with gate.acquire(label="builder-1"):
            pass
        assert gate.admitted == 1
        kinds = [e.kind for e in store.ledger.events()]
        assert "agent.admitted" in kinds and "agent.released" in kinds

    async def test_frozen_rejects(self, store: RuntimeStore) -> None:
        store.budget.spend(999.0)  # 熔断
        gate = BudgetAdmission(store)
        with pytest.raises(BudgetFrozenError, match="冻结"):
            async with gate.acquire():
                pass
        assert gate.rejected == 1

    async def test_protocol_shape(self, store: RuntimeStore) -> None:
        """上游 AgentAdmission 是结构化 Protocol：acquire 必须是 asynccontextmanager。"""

        gate = BudgetAdmission(store)
        assert hasattr(gate, "acquire")
        # asynccontextmanager 装饰的函数调用返回 async CM
        cm = gate.acquire()
        assert hasattr(cm, "__aenter__") and hasattr(cm, "__aexit__")
        await cm.__aenter__()
        await cm.__aexit__(None, None, None)


# ── LedgerObserver ───────────────────────────────────────────────────


class _FakeEvent:
    """上游 WorkflowProgressEvent 的最小鸠类型（不 import 上游）。"""

    def __init__(self, kind: str, **kw) -> None:
        self.kind = kind
        for k, v in kw.items():
            setattr(self, k, v)


class TestLedgerObserver:
    def test_event_mapping_to_ledger(self, store: RuntimeStore) -> None:
        obs = LedgerObserver(store, card_id="C-1")
        obs.emit(_FakeEvent("workflow_started", name="delivery-wave", phases=["plan", "build"]))
        obs.emit(_FakeEvent("phase", phase="build"))
        obs.emit(_FakeEvent("agent_started", phase="build", label="builder-1", model="gpt-x"))
        obs.emit(_FakeEvent("agent_completed", phase="build", label="builder-1", outcome="done"))
        obs.emit(_FakeEvent("agent_failed", phase="build", label="builder-2", message="boom"))
        obs.emit(_FakeEvent("workflow_completed", name="delivery-wave"))
        kinds = [e.kind for e in store.ledger.events()]
        assert kinds == [
            "store.created",
            "wave.run_started",
            "wave.phase",
            "agent.started",
            "agent.completed",
            "agent.failed",
            "wave.run_ended",
        ]

    def test_human_events_not_in_ledger(self, store: RuntimeStore) -> None:
        obs = LedgerObserver(store)
        obs.emit(_FakeEvent("human_prompt", label="owner", prompt="?"))
        obs.emit(_FakeEvent("log", message="noise"))
        assert [e.kind for e in store.ledger.events()] == ["store.created"]

    def test_payload_attribution(self, store: RuntimeStore) -> None:
        obs = LedgerObserver(store)
        obs.emit(_FakeEvent("agent_started", phase="build", label="builder-1", model="alias-a"))
        ev = store.ledger.events()[-1]
        assert ev.payload == {"phase": "build", "label": "builder-1", "model": "alias-a"}

    def test_run_summary(self, store: RuntimeStore) -> None:
        obs = LedgerObserver(store)
        obs.emit(_FakeEvent("phase", phase="build"))
        obs.emit(_FakeEvent("phase", phase="verify"))
        assert obs.run == {"events": 2, "by_kind": {"phase": 2}}


# ── GatewayModelResolver ─────────────────────────────────────────────


_MODELS = {
    "fast": {
        "provider": "OpenAI",
        "model": "gpt-4o-mini",
        "base_url": "https://gw.internal/v1",
        "api_key": "sk-literal",
    },
    "deep": {
        "provider": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "env:GW_BASE",
        "api_key": "env:GW_KEY",
    },
}


class TestGatewayModelResolver:
    models = _MODELS

    def test_resolve_literal(self) -> None:
        r = GatewayModelResolver(self.models).resolve("fast")
        assert r.model == "gpt-4o-mini"
        assert r.api_base == "https://gw.internal/v1"
        assert r.api_key == "sk-literal"

    def test_resolve_env(self, monkeypatch) -> None:
        monkeypatch.setenv("GW_KEY", "sk-test")
        monkeypatch.setenv("GW_BASE", "https://gw2/v1")
        r = GatewayModelResolver(self.models).resolve("deep")
        assert r.api_key == "sk-test" and r.api_base == "https://gw2/v1"

    def test_unknown_alias_fails_closed(self) -> None:
        with pytest.raises(ModelResolutionError, match="未登记"):
            GatewayModelResolver(self.models).resolve("nope")

    def test_missing_env_fails_closed(self) -> None:
        import os

        os.environ.pop("GW_KEY", None)
        os.environ.pop("GW_BASE", None)
        with pytest.raises(ModelResolutionError, match=r"GW_(KEY|BASE)"):
            GatewayModelResolver(self.models).resolve("deep")

    def test_callable_contract(self, monkeypatch) -> None:
        monkeypatch.setenv("GW_KEY", "sk-x")
        monkeypatch.setenv("GW_BASE", "https://gw2/v1")
        d = GatewayModelResolver(self.models).as_callable()("deep")
        assert d["model_client_config"]["client_provider"] == "DeepSeek"
        assert d["model_client_config"]["api_key"] == "sk-x"
        assert d["model_request_config"]["model"] == "deepseek-chat"

    def test_callable_sets_verify_ssl_false(self, monkeypatch) -> None:
        """上游 verify_ssl 默认 True 且要求 ssl_cert——http 内网网关开箱即崩（回归）。"""
        monkeypatch.setenv("GW_KEY", "sk-x")
        monkeypatch.setenv("GW_BASE", "http://gw:4000/v1")
        d = GatewayModelResolver(self.models).as_callable()("deep")
        assert d["model_client_config"]["verify_ssl"] is False


# ── runner（mock 上游——装配逻辑不依赖真实 openjiuwen）───────────────


class TestRunner:
    @pytest.fixture()
    def fake_upstream(self, monkeypatch):
        """注入假 openjiuwen 模块：记录 run_swarmflow 收到的挂点。"""
        calls: dict = {}

        async def run_swarmflow(script_path, **kw):
            calls["script"] = script_path
            calls.update(kw)
            return "RESULT"

        mod_root = types.ModuleType("openjiuwen")
        mod_wf = types.ModuleType("openjiuwen.agent_teams")
        mod_wfr = types.ModuleType("openjiuwen.agent_teams.workflow")
        mod_run = types.ModuleType("openjiuwen.agent_teams.workflow.runner")
        mod_run.run_swarmflow = run_swarmflow
        mod_schema = types.ModuleType("openjiuwen.agent_teams.workflow.schema")
        mod_schema.TeamModelConfig = object
        monkeypatch.setitem(sys.modules, "openjiuwen", mod_root)
        monkeypatch.setitem(sys.modules, "openjiuwen.agent_teams", mod_wf)
        monkeypatch.setitem(sys.modules, "openjiuwen.agent_teams.workflow", mod_wfr)
        monkeypatch.setitem(sys.modules, "openjiuwen.agent_teams.workflow.runner", mod_run)
        monkeypatch.setitem(sys.modules, "openjiuwen.agent_teams.workflow.schema", mod_schema)
        return calls

    @pytest.fixture()
    def workspace(self, tmp_path: Path) -> Path:
        """最小渲染产物：models.json + swarmflow/team.py + manifest。"""
        import json

        from agentplatform.render.manifest import RenderManifest
        from agentplatform.spec.fingerprint import sha256_hex

        ws = tmp_path / "ws"
        (ws / "swarmflow").mkdir(parents=True)
        models = json.dumps(
            {
                "gateway": {"base_url": "https://gw/v1", "api_key": "env:GW_KEY"},
                "aliases": ["fast"],
                "default": "fast",
            }
        )
        (ws / "models.json").write_text(models, encoding="utf-8")
        script = "META = {'name': 't', 'phases': ['build']}\nasync def run(args):\n    return 1\n"
        (ws / "swarmflow" / "team.py").write_text(script, encoding="utf-8")
        m = RenderManifest(
            spec_digest="d" * 64,
            renderer_version="test",
            files={
                "models.json": sha256_hex(models),
                "swarmflow/team.py": sha256_hex(script),
            },
            env_refs=("GW_KEY",),
        )
        m.write(ws)
        return ws

    async def test_assembly_wires_all_hooks(self, tmp_path: Path, workspace, fake_upstream) -> None:
        from agentplatform.adapter.runner import run_team_flow

        store_dir = tmp_path / "state"
        RuntimeStore.create(store_dir, envelope_usd=10.0)
        result = await run_team_flow(
            workspace, store_dir, "team", model=object(), args={"x": 1}, card_id="C-1"
        )
        assert result == "RESULT"
        assert fake_upstream["script"].endswith("swarmflow/team.py")
        assert fake_upstream["team_name"] == "team"
        # 三挂点类型正确接线
        assert isinstance(fake_upstream["agent_gate"], BudgetAdmission)
        assert hasattr(fake_upstream["observer"], "emit")
        assert callable(fake_upstream["model_resolver"])
        # 账本收到 runner 边界事件
        store = RuntimeStore.open(store_dir)
        kinds = [e.kind for e in store.ledger.events()]
        assert "wave.flow_started" in kinds and "wave.flow_finished" in kinds

    async def test_missing_script_fails(self, tmp_path: Path, workspace, fake_upstream) -> None:
        from agentplatform.adapter.runner import RunnerError, run_team_flow

        store_dir = tmp_path / "s2"
        RuntimeStore.create(store_dir, envelope_usd=10.0)
        with pytest.raises(RunnerError, match="不存在"):
            await run_team_flow(workspace, store_dir, "no-team", model=object())

    async def test_model_resolver_end_to_end(
        self, tmp_path: Path, workspace, fake_upstream, monkeypatch
    ) -> None:
        monkeypatch.setenv("GW_KEY", "sk-1")
        from agentplatform.adapter.runner import run_team_flow

        store_dir = tmp_path / "s3"
        RuntimeStore.create(store_dir, envelope_usd=10.0)
        await run_team_flow(workspace, store_dir, "team", model=object())
        # model_resolver 契约（无 TeamModelConfig 构造路径时返回 dict——上游侧再转）
        assert callable(fake_upstream["model_resolver"])
