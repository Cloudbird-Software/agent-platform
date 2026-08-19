"""渲染目标构造——纯函数：声明实体 → jiuwenswarm 配置节点。

映射表（声明 → jiuwenswarm，逐条可审计；不可映射字段进 manifest.notes）：
- team → modes.team.<id> 模板
    lifecycle: ephemeral→temporary / 常驻(standing)→persistent
    members: seat=planner → leader persona；其余 → predefined_members
    count>1 → member_name 加序号展开（builder×2 → builder-1/builder-2）
    as_tool=true 的座位照常渲染为 teammate（运行侧由 swarmflow 单次往返调用）
- agent → predefined_members 项
    desc = role（公开一句话）；prompt = identity 文件内容整体内联（自包含）
    model_name = model.alias（别名直传——解析只发生在 LLM Gateway，ADR-0002）
- permissions.overrides [{tools,pattern,action}] → permissions.rules
    （tiered_policy 的 action allow|ask|deny 与声明 action 同构，直映射；
    rule id 加 agent 前缀防跨 agent 撞 id）
- workspace.root / storage.ref 的 env:X → ${X}
- models.default ← gateway endpoint/key（env 符号）+ leader alias
- guardrails.must_run / io_contract / capabilities.allow 等无 jiuwenswarm
  落点 → 治理执行面（governance/flow 层消费），manifest.notes 记录去向
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentplatform.spec import SpecSnapshot
from agentplatform.spec.errors import SpecError
from agentplatform.spec.loader import TEAM_RENDERABLE

ENV_VALUE = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")

# 这些声明字段在 jiuwenswarm 无原生落点，由本仓其他层执行——登记防"静默丢失"。
HANDLED_ELSEWHERE = {
    "guardrails": "governance/（card-gate/must_run 门禁）",
    "io_contract": "flow/（结构化 IO 编译进 SwarmFlow 步骤）",
    "capabilities.allow": "adapter/ rails + governance/ 写锁执行",
    "independence": "render 已保模型族混排（model_name=alias 直传）",
    "memory": "context-assembly 声明为准；jiuwenswarm 内置记忆关闭（engine: none）",
    "budget": "governance/ 预算熔断（usd/wall_clock env 符号透传）",
}


def env_symbolic(value: Any) -> Any:
    """env:X → ${X}；其他原样（jiuwenswarm 原生 ${VAR} 展开）。"""
    if isinstance(value, str):
        m = ENV_VALUE.match(value)
        if m:
            return f"${{{m.group(1)}}}"
    return value


def collect_env_vars(node: Any, out: set[str]) -> None:
    """收集渲染结果里全部 ${VAR} 引用（manifest.env_refs / .env 生成）。"""
    if isinstance(node, dict):
        for v in node.values():
            collect_env_vars(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_env_vars(v, out)
    elif isinstance(node, str):
        for m in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*)(?::-[^}]*)?\}", node):
            out.add(m.group(1))


def lifecycle_of(team_raw: dict[str, Any]) -> str:
    lt = (team_raw.get("lifecycle") or {}).get("type")
    if lt == "ephemeral":
        return "temporary"
    # 常驻（stewardship 无 lifecycle.type 或 standing 语义）→ persistent
    return "persistent"


def build_team_template(
    team_id: str, snap: SpecSnapshot, registry_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (modes.team.<id> 节点, 该模板的 notes)。"""
    team = snap.teams[team_id]
    raw = team.raw
    notes: dict[str, Any] = {"unrendered": {}}

    template: dict[str, Any] = {
        "team_name": team_id,
        "display_name": team_id,
        "lifecycle": lifecycle_of(raw),
        "teammate_mode": "build_mode",
        "spawn_mode": "inprocess",
        "swarmflow_budget": 64,
    }

    members = raw.get("members") or []
    leader_persona = "机制调度者（SwarmFlow 确定性编排；leader 为旁观者——ADR-0025 flow/ 层）"
    predefined: list[dict[str, Any]] = []
    multi_seat_counts: dict[str, int] = {}
    for m in members:
        s = str(m.get("seat") or "")
        c = m.get("count") if isinstance(m.get("count"), int) and m.get("count", 0) > 0 else 1
        if s:
            multi_seat_counts[s] = max(multi_seat_counts.get(s, 1), c)

    for m in members:
        ref = m.get("agent", "")
        agent = snap.approved_agent(ref, f"team:{team_id}")
        seat = m.get("seat") or agent.id
        count = m.get("count") if isinstance(m.get("count"), int) and m.get("count", 0) > 0 else 1
        # count>1 的座位：全部实例带序号（builder-1/builder-2），单实例用裸座位名
        numbered = multi_seat_counts.get(str(seat), 1) > 1

        desc = agent.raw.get("role") or agent.id
        prompt = _identity_prompt(agent, snap, registry_root)
        alias = (agent.raw.get("model") or {}).get("alias") or ""

        for i in range(1, count + 1):
            if numbered:
                name = f"{seat}-{i}"
            else:
                name = str(seat) or agent.id
            if seat == "planner" and i == 1:
                leader_persona = f"{desc}（模型 {alias}；planner 座位映射为 leader）"
                continue
            # planner 多实例的其余副本作为普通成员
            predefined.append(
                {
                    "member_name": name,
                    "display_name": f"{agent.id}（{seat}）",
                    "desc": desc,
                    "prompt": prompt,
                    "model_name": alias,
                    "role_type": "teammate",
                }
            )

    template["leader"] = {
        "member_name": "team_leader",
        "display_name": "scheduler",
        "persona": leader_persona,
    }
    if predefined:
        template["predefined_members"] = predefined

    # workspace / storage
    ws = raw.get("workspace") or {}
    shared = ws.get("shared") or {}
    template["workspace"] = {
        "enabled": True,
        "root_path": env_symbolic(shared.get("root"))
        or f"${{{team_id.upper().replace('-', '_')}_WORKSPACE_ROOT}}",
        "version_control": bool(shared.get("version_control", False)),
    }
    storage = raw.get("storage") or {}
    if storage.get("type"):
        params = {"connection_string": env_symbolic(storage.get("ref"))}
        template["storage"] = {"type": storage.get("type"), "params": params}

    # 无落点字段的去向登记（防静默丢失）
    for field in ("guardrails", "budget", "verification"):
        if field in raw:
            notes["unrendered"][field] = HANDLED_ELSEWHERE.get(
                "guardrails" if field == "guardrails" else "budget" if field == "budget" else "io_contract"
            )
    return template, notes


def _identity_prompt(agent, snap: SpecSnapshot, registry_root: Path) -> str:
    prompt_ref = (agent.raw.get("identity") or {}).get("prompt_ref") or ""
    if not prompt_ref:
        return agent.raw.get("role") or agent.id
    rel = prompt_ref if prompt_ref.startswith("registry/") else f"registry/{prompt_ref}"
    path = registry_root / rel
    if not path.is_file():
        raise SpecError("missing", f"identity 提示词缺失: {rel}（agent:{agent.id}）")
    return path.read_text(encoding="utf-8")


def build_permissions(snap: SpecSnapshot) -> dict[str, Any]:
    """全部 approved agent 的 permissions.overrides 并集 → tiered_policy rules。

    声明的 default-deny（pattern 命中即 deny）映射为 tiered_policy 的显式
    action 规则（不随 permission_mode 漂移——上游模板注释明确该语义）。
    """
    rules: list[dict[str, Any]] = []
    for aid in sorted(snap.agents):
        agent = snap.agents[aid]
        if agent.status != "approved":
            continue
        perms = (agent.raw.get("permissions") or {}).get("overrides") or []
        for i, r in enumerate(perms):
            if not isinstance(r, dict):
                continue
            action = r.get("action")
            if action not in ("allow", "ask", "deny"):
                action = {"approve": "allow", "block": "deny"}.get(str(action), "ask")
            rules.append(
                {
                    "id": f"ag_{aid}_{i}",
                    "tools": [t.removeprefix("tool:") for t in (r.get("tools") or [])],
                    "pattern": str(r.get("pattern", "*")),
                    "action": action,
                }
            )
    return {
        "enabled": True,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "ask"},
        "tools": {},
        "rules": rules,
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
            "paths": [
                {"path": "**/.ssh/**", "match": "glob", "read": "deny", "write": "deny", "exec": "deny"},
                {"path": "**/.env*", "match": "glob", "read": "ask", "write": "deny", "exec": "ask"},
            ],
        },
    }


def build_models(snap: SpecSnapshot, default_alias: str | None) -> dict[str, Any]:
    """models.default ← LLM Gateway（ADR-0002/0003：别名解析只在网关侧）。"""
    gateway = {"api_base": "${LLM_GATEWAY_ENDPOINT}", "api_key": "${LLM_GATEWAY_KEY}"}
    alias = default_alias
    if alias is None:
        aliases = sorted(snap.models)
        alias = aliases[0] if aliases else ""
    return {
        "default": {
            "model_client_config": {
                **gateway,
                "model_name": alias,
                "client_provider": "openai",
                "timeout": 1800,
                "verify_ssl": False,
                "custom_headers": {},
            },
            "model_config_obj": {"temperature": 0.2},
        }
    }


def build_models_registry(snap: SpecSnapshot, default_alias: str | None) -> dict[str, Any]:
    """机器可读模型注册表（workspace models.json）——执行面 resolver 的真源。

    config.yaml 的 models 节是 jiuwenswarm 目标形状（${VAR} 占位）；
    runner/agentctl 需要的是 alias 注册表——本文件补齐该缝：
      gateway  网关 env 符号（base_url/api_key）
      aliases  models.yaml 全量 alias（worker model hint 可解析）
      default  无 hint 时的默认 alias（leader 模型）
    """
    aliases = sorted(snap.models)
    if default_alias and default_alias not in aliases:
        aliases = sorted({*aliases, default_alias})
    return {
        "gateway": {"base_url": "env:LLM_GATEWAY_ENDPOINT", "api_key": "env:LLM_GATEWAY_KEY"},
        "aliases": aliases,
        "default": default_alias or (aliases[0] if aliases else ""),
    }


def build_config(snap: SpecSnapshot, registry_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """完整 AgentServer config 树 + notes。"""
    team_ids = [tid for tid in sorted(snap.teams) if snap.teams[tid].status in TEAM_RENDERABLE]
    templates: dict[str, Any] = {}
    notes: dict[str, Any] = {"teams": {}}
    default_alias: str | None = None
    for tid in team_ids:
        template, tnotes = build_team_template(tid, snap, registry_root)
        templates[tid] = template
        notes["teams"][tid] = tnotes
        if default_alias is None and template.get("leader"):
            # leader 模型的别名记录进 models.default（实际成员各自带 alias）
            first = (template.get("predefined_members") or [{}])[0]
            default_alias = first.get("model_name") or None
    notes["default_model_alias"] = default_alias

    config: dict[str, Any] = {
        "preferred_language": "zh",
        "logging": {
            "level": "INFO",
            "console_level": "INFO",
            "gateway": "INFO",
            "channel": "INFO",
            "agent_server": "INFO",
            "full": "INFO",
        },
        "memory": {"engine": "none", "mode": "local"},
        "telemetry": {
            "enabled": "${OTEL_ENABLED:-false}",
            "exporter": "otlp",
            "endpoint": "${OTEL_ENDPOINT:-http://127.0.0.1:4317}",
            "protocol": "grpc",
            "log_messages": True,
            "service_name": "agentplatform",
        },
        "models": build_models(snap, default_alias),
        "permissions": build_permissions(snap),
        "tools": ["todo", "skill"],
        "mcp": {"servers": []},
        "channels": {"web": {"send_file_allowed": True}},
        "updater": {"enabled": False},
        "modes": {"team": templates},
    }
    notes["memory"] = HANDLED_ELSEWHERE["memory"]
    return config, notes
