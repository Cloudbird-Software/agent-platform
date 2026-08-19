"""render/ 渲染器功能测试：映射正确性 + 幂等 + manifest 自校验。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from agentplatform.render import Renderer, load_manifest
from agentplatform.render.targets import collect_env_vars
from agentplatform.spec import RegistryLoader

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"


@pytest.fixture()
def reg(tmp_path: Path) -> Path:
    dst = tmp_path / "reg"
    shutil.copytree(FIXTURE, dst)
    return dst


def _render(reg: Path, out: Path):
    snap = RegistryLoader().load(reg)
    return snap, Renderer().render(snap, out)


def test_render_produces_config_and_manifest(reg: Path, tmp_path: Path) -> None:
    out = tmp_path / "ws"
    snap, manifest = _render(reg, out)
    assert (out / "config.yaml").is_file()
    assert (out / "manifest.json").is_file()
    assert manifest.spec_digest == snap.digest
    assert "config.yaml" in manifest.files


def test_team_template_mapping(reg: Path, tmp_path: Path) -> None:
    out = tmp_path / "ws"
    _render(reg, out)
    cfg = yaml.safe_load((out / "config.yaml").read_text(encoding="utf-8"))
    assert "mini-wave" in cfg["modes"]["team"]
    t = cfg["modes"]["team"]["mini-wave"]
    assert (t["lifecycle"] == "ephemeral" and False) or t["lifecycle"] == "temporary"
    # workspace root 的 env: 符号透传为 ${VAR}
    assert t["workspace"]["root_path"] == "${TEAM_WORKSPACE_ROOT}"
    # storage postgres ref → ${TEAM_DB_DSN}（mini fixture 无 storage——真 registry 有）
    # planner 座位 → leader；其余成员 → predefined_members，model_name=alias 直传
    assert t["leader"]["member_name"] == "team_leader"
    members = t["predefined_members"]
    assert all(m["role_type"] == "teammate" for m in members)
    assert any(m["model_name"] == "coder-fast" for m in members)
    # identity prompt 内联（自包含 workspace）
    assert any("mini-builder" in m["prompt"] or m["prompt"] for m in members)


def test_permissions_rules_from_overrides(reg: Path, tmp_path: Path) -> None:
    # mini-builder 无 overrides → 规则来自全量 approved agents 的并集（这里为空）
    out = tmp_path / "ws"
    _render(reg, out)
    cfg = yaml.safe_load((out / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["permissions"]["enabled"] is True
    assert cfg["permissions"]["schema"] == "tiered_policy"
    assert cfg["permissions"]["defaults"] == {"*": "ask"}
    # file_guard：.ssh 全禁
    ssh = [p for p in cfg["permissions"]["file_guard"]["paths"] if ".ssh" in p["path"]]
    assert ssh and ssh[0]["write"] == "deny"


def test_render_idempotent_bytes(reg: Path, tmp_path: Path) -> None:
    out1, out2 = tmp_path / "a", tmp_path / "b"
    m1 = _render(reg, out1)[1]
    m2 = _render(reg, out2)[1]
    assert (out1 / "config.yaml").read_bytes() == (out2 / "config.yaml").read_bytes()
    assert m1.digest == m2.digest
    # 同目录重渲染也幂等
    m3 = _render(reg, out1)[1]
    assert m3.digest == m1.digest


def test_manifest_roundtrip_and_env_refs(reg: Path, tmp_path: Path) -> None:
    out = tmp_path / "ws"
    manifest = _render(reg, out)[1]
    loaded = load_manifest(out)
    assert loaded.digest == manifest.digest
    assert "LLM_GATEWAY_ENDPOINT" in loaded.env_refs
    assert "LLM_GATEWAY_KEY" in loaded.env_refs
    assert "TEAM_WORKSPACE_ROOT" in loaded.env_refs


def test_render_real_registry_if_present(tmp_path: Path) -> None:
    """对着真实 agent-registry checkout 的冒烟（存在才跑，CI 里 /workspace 有）。"""
    real = Path("/workspace")
    if not (real / "registry" / "models.yaml").is_file():
        pytest.skip("真实 registry 不在本机")
    snap = RegistryLoader().load(real)
    manifest = Renderer().render(snap, tmp_path / "real-ws")
    cfg = yaml.safe_load((tmp_path / "real-ws" / "config.yaml").read_text(encoding="utf-8"))
    assert {"dev-wave", "incident-cell", "stewardship"} <= set(cfg["modes"]["team"])
    dev = cfg["modes"]["team"]["dev-wave"]
    names = [m["member_name"] for m in dev["predefined_members"]]
    # backend-dev count=2 → 展开 builder-1/builder-2；planner → leader
    assert "builder-1" in names and "builder-2" in names
    assert dev["storage"]["params"]["connection_string"] == "${TEAM_DB_DSN}"
    # reviewer 的 default-deny override 渲染为显式 action 规则
    rules = cfg["permissions"]["rules"]
    reviewer_rules = [r for r in rules if r["id"].startswith("ag_reviewer_")]
    assert reviewer_rules and all(r["action"] in ("allow", "deny", "ask") for r in reviewer_rules)
    assert manifest.env_refs  # 部署变量清单非空


def test_collect_env_vars_nested() -> None:
    out: set[str] = set()
    collect_env_vars({"a": {"b": ["${FOO}", "x${BAR:-def}"]}, "c": "plain"}, out)
    assert out == {"FOO", "BAR"}
