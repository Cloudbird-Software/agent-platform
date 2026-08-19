"""Registry 声明加载器：文件树 → 不可变 SpecSnapshot（带指纹）。

加载面契约（ADR-0025）：
- 实体按 id 索引；status != approved 的实体可加载（草稿是声明面合法状态），
  但被 approved 实体引用、或被请求进入渲染面时 fail-closed——与
  agent-registry validate.py 的"引用 status != approved 即拒绝"语义一致；
- 引用完整性：agent:*/tool:*/skill:*/team:*/schemas/*/identities/*/
  workflows/* 必须可解析到已加载实体；
- env 引用保持符号形式（工具中立）：值形如 "env:NAME" 原样保留，由
  render/bootstrap 阶段做环境注入；loader 只校验形状；
- 泄漏扫描：任何字符串标量命中密钥特征（ghp_/sk-/AKIA/xoxb/PRIVATE KEY/
  带凭据 DSN）即 SpecError(kind="leak")——对应 registry 硬规则
  "禁止出现任何明文密钥/连接串；一律 env: 引用"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agentplatform.spec.errors import SpecError, duplicate, missing, parse_error, reference, shape
from agentplatform.spec.fingerprint import content_digest, digest_of_files, file_digest, hash_file

ENV_REF = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")


def _norm_resource(ref: str) -> str:
    """真实 registry 的 prompt_ref/schema_ref/steps_ref 相对 registry/ 目录；
    也接受相对根的完整路径（两种写法归一）。"""
    if ref.startswith("registry/"):
        return ref
    return f"registry/{ref}"


# 可进入渲染/完整性校验的团队状态：approved（一次性波次队）+ active（常驻队，
# 如 stewardship——常驻是合法生命周期，不是草稿）。
TEAM_RENDERABLE = {"approved", "active"}

# 明文密钥特征（对抗测试逐条覆盖；误报保守优先——宁可 CI 红）。
LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credentialed-dsn", re.compile(r"[a-z+]+://[^/\s:@]+:[^/\s:@]+@[^\s]+")),
)


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise missing(str(path))
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise parse_error(str(path), str(e)) from e


def _scan_leaks(where: str, obj: Any) -> None:
    if isinstance(obj, str):
        for name, pat in LEAK_PATTERNS:
            if pat.search(obj):
                # env: 引用本身是符号形式，不是泄漏
                if ENV_REF.match(obj):
                    continue
                raise SpecError("leak", f"{where}: 疑似明文密钥（{name}）——registry 硬规则：一律 env: 引用")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _scan_leaks(f"{where}.{k}", v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_leaks(f"{where}[{i}]", v)


def _require_str(container: dict[str, Any], key: str, where: str) -> str:
    v = container.get(key)
    if not isinstance(v, str) or not v:
        raise shape(where, f"字段 {key} 必须是非空字符串")
    return v


@dataclass(frozen=True)
class Entity:
    """实体视图：raw 保留全量声明，digest 为路径绑定指纹。"""

    kind: str
    id: str
    status: str
    rel_path: str
    raw: dict[str, Any] = field(repr=False)
    digest: str


class SpecSnapshot:
    """加载结果的不可变视图。MappingProxyType 防意外改写（对抗测试验证）。"""

    def __init__(
        self,
        root: Path,
        agents: dict[str, Entity],
        teams: dict[str, Entity],
        tools: dict[str, Entity],
        models: dict[str, dict[str, Any]],
        profiles: dict[str, dict[str, Any]],
        resources: dict[str, str],  # 相对路径 → 资源哈希（identities/workflows/schemas/skills）
        files: dict[str, str],  # 相对路径 → 文件指纹（全部声明文件）
    ) -> None:
        self._root = root
        self._agents = MappingProxyType(agents)
        self._teams = MappingProxyType(teams)
        self._tools = MappingProxyType(tools)
        self._models = MappingProxyType(models)
        self._profiles = MappingProxyType(profiles)
        self._resources = MappingProxyType(resources)
        self._files = MappingProxyType(files)
        self._digest = digest_of_files(list(files.items()))

    # ---- 视图 ----
    @property
    def root(self) -> Path:
        return self._root

    @property
    def agents(self) -> MappingProxyType[str, Entity]:
        return self._agents

    @property
    def teams(self) -> MappingProxyType[str, Entity]:
        return self._teams

    @property
    def tools(self) -> MappingProxyType[str, Entity]:
        return self._tools

    @property
    def models(self) -> MappingProxyType[str, dict[str, Any]]:
        return self._models

    @property
    def profiles(self) -> MappingProxyType[str, dict[str, Any]]:
        return self._profiles

    @property
    def resources(self) -> MappingProxyType[str, str]:
        """非 YAML 资源（prompt/steps/schema/skill）的相对路径 → 哈希。"""
        return self._resources

    @property
    def files(self) -> MappingProxyType[str, str]:
        return self._files

    @property
    def digest(self) -> str:
        """快照摘要：声明面身份。render 输出指纹必须可追溯到它。"""
        return self._digest

    # ---- 解析 ----
    def resolve_agent(self, ref: str) -> Entity:
        aid = ref.removeprefix("agent:")
        ent = self._agents.get(aid)
        if ent is None:
            raise reference("快照", f"agent:{aid}", "agent")
        return ent

    def resolve_tool(self, ref: str) -> Entity:
        tid = ref.removeprefix("tool:")
        ent = self._tools.get(tid)
        if ent is None:
            raise reference("快照", f"tool:{tid}", "tool")
        return ent

    def approved_agent(self, ref: str, src: str) -> Entity:
        ent = self.resolve_agent(ref)
        if ent.status != "approved":
            raise reference(src, f"agent:{ent.id}(status={ent.status})", "approved agent")
        return ent

    def model_of(self, agent: Entity) -> dict[str, Any]:
        alias = agent.raw.get("model", {}).get("alias")
        m = self._models.get(alias)
        if m is None:
            raise reference(f"agent:{agent.id}", str(alias), "models.yaml alias")
        return m

    def require_resource(self, rel: str, src: str) -> str:
        if rel not in self._resources:
            raise reference(src, rel, "资源文件")
        return self._resources[rel]


class RegistryLoader:
    """从 registry checkout（agent-registry 仓）加载声明快照。"""

    def load(self, root: str | Path) -> SpecSnapshot:
        root = Path(root).resolve()
        reg = root / "registry"
        std = root / "standards"
        if not reg.is_dir():
            raise missing(str(reg), "registry/ 目录")

        files: dict[str, str] = {}

        # ---- agents ----
        agents: dict[str, Entity] = {}
        for p in sorted((reg / "agents").glob("*.yaml")):
            rel = p.relative_to(root).as_posix()
            raw = _load_yaml(p)
            if not isinstance(raw, dict):
                raise shape(rel, "根节点必须是映射")
            _scan_leaks(rel, raw)
            aid = _require_str(raw, "id", rel)
            if aid in agents:
                raise duplicate("agent", aid, agents[aid].rel_path, rel)
            status = _require_str(raw, "status", rel)
            agents[aid] = Entity("agent", aid, status, rel, raw, file_digest(rel, raw))
            files[rel] = agents[aid].digest

        # ---- teams ----
        teams: dict[str, Entity] = {}
        for p in sorted((reg / "teams").glob("*.yaml")):
            rel = p.relative_to(root).as_posix()
            raw = _load_yaml(p)
            if not isinstance(raw, dict):
                raise shape(rel, "根节点必须是映射")
            _scan_leaks(rel, raw)
            tid = _require_str(raw, "id", rel)
            if tid in teams:
                raise duplicate("team", tid, teams[tid].rel_path, rel)
            status = _require_str(raw, "status", rel)
            teams[tid] = Entity("team", tid, status, rel, raw, file_digest(rel, raw))
            files[rel] = teams[tid].digest

        # ---- tools ----
        tools: dict[str, Entity] = {}
        for p in sorted((reg / "tools").glob("*.yaml")):
            rel = p.relative_to(root).as_posix()
            raw = _load_yaml(p)
            if not isinstance(raw, dict):
                raise shape(rel, "根节点必须是映射")
            _scan_leaks(rel, raw)
            tident = _require_str(raw, "id", rel)
            if tident in tools:
                raise duplicate("tool", tident, tools[tident].rel_path, rel)
            status = _require_str(raw, "status", rel)
            tools[tident] = Entity("tool", tident, status, rel, raw, file_digest(rel, raw))
            files[rel] = tools[tident].digest

        # ---- models ----
        models_raw = _load_yaml(reg / "models.yaml")
        if not isinstance(models_raw, dict):
            raise shape("registry/models.yaml", "根节点必须是映射")
        _scan_leaks("registry/models.yaml", models_raw)
        models: dict[str, dict[str, Any]] = {}
        for m in models_raw.get("models", []) or []:
            alias = m.get("alias")
            if not isinstance(alias, str):
                raise shape("registry/models.yaml", "model 项缺 alias")
            if alias in models:
                raise duplicate("model", alias, "", "registry/models.yaml")
            models[alias] = m
        files["registry/models.yaml"] = file_digest("registry/models.yaml", models_raw)

        # ---- profiles（标准侧）----
        profiles_raw = _load_yaml(std / "archetype-profiles.yaml")
        if not isinstance(profiles_raw, dict):
            raise shape("standards/archetype-profiles.yaml", "根节点必须是映射")
        profiles = dict(profiles_raw.get("profiles") or {})
        files["standards/archetype-profiles.yaml"] = file_digest(
            "standards/archetype-profiles.yaml", profiles_raw
        )

        # ---- 资源（prompt/steps/schema/skill——字节哈希）----
        resources: dict[str, str] = {}
        for pattern, base in (
            ("**/*.md", reg / "identities"),
            ("**/*.steps.md", reg / "workflows"),
            ("*.json", reg / "schemas"),
            ("*/SKILL.md", reg / "skills"),
        ):
            base_dir = base
            if not base_dir.is_dir():
                continue
            for p in sorted(base_dir.glob(pattern)):
                rel = p.relative_to(root).as_posix()
                resources[rel] = hash_file(p)

        snap = SpecSnapshot(root, agents, teams, tools, models, profiles, resources, files)
        self._check_integrity(snap)
        return snap

    # ---- 引用完整性（加载后统一校验，报错可指明引用方）----
    def _check_integrity(self, snap: SpecSnapshot) -> None:
        for agent in snap.agents.values():
            src = f"agent:{agent.id}"
            # 状态门：进快照的 approved agent 才可被渲染；非 approved 允许存在
            if agent.status == "approved":
                self._check_agent_refs(agent, snap, src)
        for team in snap.teams.values():
            if team.status not in TEAM_RENDERABLE:
                continue
            src = f"team:{team.id}"
            members = team.raw.get("members") or []
            if not isinstance(members, list) or not members:
                raise shape(team.rel_path, "members 必须是非空列表")
            for m in members:
                if not isinstance(m, dict):
                    raise shape(team.rel_path, "member 项必须是映射")
                snap.approved_agent(m.get("agent", ""), src)
            # 注意：team 的 archetype（delivery_squad 等）是 team-collaboration.yaml
            # 里的组织级标签，不在 archetype-profiles（后者只定义 agent 原型）——
            # registry validate.py 亦不做此比对，此处不重复（声明的真源在 standards 侧）。

    def _check_agent_refs(self, agent: Entity, snap: SpecSnapshot, src: str) -> None:
        raw = agent.raw
        # 模型别名
        alias = (raw.get("model") or {}).get("alias")
        if not isinstance(alias, str) or alias not in snap.models:
            raise reference(src, str(alias), "models.yaml alias")
        # archetype
        arch = raw.get("archetype")
        if isinstance(arch, str) and arch not in snap.profiles:
            raise reference(src, arch, "archetype profile")
        # 身份提示词
        ident = raw.get("identity") or {}
        prompt_ref = ident.get("prompt_ref")
        if isinstance(prompt_ref, str) and prompt_ref:
            snap.require_resource(_norm_resource(prompt_ref), src)
        # io_contract schemas
        io = raw.get("io_contract") or {}
        for side in ("input", "output"):
            ref = (io.get(side) or {}).get("schema_ref")
            if isinstance(ref, str) and ref:
                snap.require_resource(_norm_resource(ref), src)
        # 工具/技能引用
        caps = raw.get("capabilities") or {}
        for ref in caps.get("tools") or []:
            snap.resolve_tool(str(ref))
        for ref in caps.get("skills") or []:
            rel = f"registry/skills/{str(ref).removeprefix('skill:')}/SKILL.md"
            snap.require_resource(rel, src)
        # 固定流程 steps_ref
        wf = raw.get("workflow") or {}
        if wf.get("mode") == "fixed":
            steps_ref = wf.get("steps_ref")
            if isinstance(steps_ref, str) and steps_ref:
                snap.require_resource(_norm_resource(steps_ref), src)

        # env 引用形状（软面：只校验已知 env 字段的形状）
        _check_env_shape(agent)


def _check_env_shape(agent: Entity) -> None:
    """workspace/storage 等字段若用 env: 引用，必须形如 env:UPPER_SNAKE。"""
    raw = agent.raw
    for key in ("workspace", "storage"):
        node = raw.get(key)
        if isinstance(node, dict):
            for sub in ("root", "ref", "dsn"):
                v = node.get(sub)
                if isinstance(v, str) and v.startswith("env:") and not ENV_REF.match(v):
                    raise shape(agent.rel_path, f"{key}.{sub}={v!r} 不是合法 env: 引用")


def snapshot_digest_of(snap: SpecSnapshot) -> str:
    """快照摘要（spec digest 的规范别名——render manifest 引用它）。"""
    return snap.digest


def entity_digest(ent: Entity) -> str:
    return content_digest(ent.raw)
