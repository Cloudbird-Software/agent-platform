"""spec 加载器功能测试（mini fixture = 真实 registry 结构的最小子集）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentplatform.spec import RegistryLoader

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"


@pytest.fixture()
def reg(tmp_path: Path) -> Path:
    """每个用例拿到独立副本（对抗用例会改文件）。"""
    dst = tmp_path / "reg"
    shutil.copytree(FIXTURE, dst)
    return dst


def test_load_mini_registry_and_digest_stability() -> None:
    snap = RegistryLoader().load(FIXTURE)
    assert set(snap.agents) == {"mini-builder", "mini-draft"}
    assert set(snap.teams) == {"mini-wave"}
    assert set(snap.tools) == {"mini-read"}
    assert "coder-fast" in snap.models and "reviewer" in snap.models
    # 幂等：同一目录两次加载摘要一致
    again = RegistryLoader().load(FIXTURE)
    assert snap.digest == again.digest


def test_entity_digest_is_path_bound() -> None:
    snap = RegistryLoader().load(FIXTURE)
    a = snap.agents["mini-builder"]
    assert a.digest.startswith("sha") is False  # 十六进制
    assert len(a.digest) == 64
    # 路径绑定：同内容不同路径 → 指纹不同
    from agentplatform.spec.fingerprint import file_digest

    assert file_digest("x/y.yaml", {"a": 1}) != file_digest("y.yaml", {"a": 1})


def test_yaml_semantic_equivalence_same_digest(reg: Path) -> None:
    """注释/缩进/键序变化不改变指纹（canonical_json 责任）。"""
    base = RegistryLoader().load(reg)
    p = reg / "registry" / "agents" / "mini-builder.yaml"
    p.write_text(
        "# 重组过的同语义文件\n"
        + "workspace:\n"
        + "    root: env:BUILDER_WORKSPACE_ROOT\n"
        + "    scope: private\n"
        + "\n"
        + "id: mini-builder\n"
        + "status: approved\n"
        + "archetype: builder\n"
        + "version: 1.0.0\n"
        + "model:\n"
        + "    alias: coder-fast\n"
        + "    temperature: 0.2\n"
        + "role: 迷你夹具：builder\n"
        + "capabilities:\n"
        + "    tools: [tool:mini-read]\n"
        + "    skills: [skill:mini-skill]\n"
        + "    allow: [fs_read, fs_write_sandbox]\n"
        + "identity:\n"
        + "    prompt_ref: registry/identities/mini-builder.md\n"
        + "io_contract:\n"
        + "    input: {schema_ref: registry/schemas/task-in.json}\n"
        + "    output: {schema_ref: registry/schemas/findings.json}\n"
        + "isolation: private\n"
        + "approval: auto\n"
        + "trust_zone: trusted_control\n",
        encoding="utf-8",
    )
    other = RegistryLoader().load(reg)
    assert base.agents["mini-builder"].digest == other.agents["mini-builder"].digest


def test_env_refs_stay_symbolic() -> None:
    snap = RegistryLoader().load(FIXTURE)
    ws = snap.agents["mini-builder"].raw["workspace"]
    assert ws["root"] == "env:BUILDER_WORKSPACE_ROOT"  # 不做环境解析（工具中立）
    assert snap.models is not None


def test_draft_agent_present_but_not_renderable(reg: Path) -> None:
    snap = RegistryLoader().load(reg)
    assert snap.agents["mini-draft"].status == "draft"
    with pytest.raises(Exception) as ei:
        snap.approved_agent("agent:mini-draft", "test")
    assert ei.value.kind == "reference"


def test_snapshot_views_immutable() -> None:
    snap = RegistryLoader().load(FIXTURE)
    with pytest.raises((TypeError, AttributeError)):
        snap.agents["x"] = None  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        snap.models["new"] = 1  # type: ignore[index]


def test_cli_spec_digest_and_show(reg: Path) -> None:
    from agentplatform.cli import main

    assert main(["spec", "digest", "--registry", str(reg)]) == 0
    assert main(["spec", "show", "--registry", str(reg), "agent:mini-builder"]) == 0
    assert main(["spec", "show", "--registry", str(reg), "agent:missing"]) == 2
