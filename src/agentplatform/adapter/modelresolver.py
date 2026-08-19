"""GatewayModelResolver：model alias → 网关模型配置（ADR-0002 LLM Gateway）。

上游契约：model_resolver: Callable[[str], TeamModelConfig | None]——
    TeamModelConfig(model_client_config=ModelClientConfig(
        client_provider, api_key, api_base),
        model_request_config=ModelRequestConfig(model="<name>"))

声明面（registry/models.yaml 渲染进 workspace config 的 models 节）：
    alias → {provider, model, base_url(env), api_key(env)}

本模块不 import 上游 schema（鸠类型 dict 即可被 tiny_agent 消费前的
构造方转换）；真实上游对象构造在 runner（lazy import 处）完成——
保持本模块可在零上游环境测试。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class ModelResolutionError(Exception):
    """alias 未登记或 env 缺失——fail-closed（不静默回退默认模型）。"""


def load_models_registry(workspace: str | Path) -> dict[str, dict]:
    """workspace/models.json → resolver 注册表（渲染面与执行面的唯一缝）。

    返回 {alias: {provider, model, base_url, api_key}}（env: 符号原样保留，
    解析时才查 os.environ）。
    """
    p = Path(workspace) / "models.json"
    if not p.is_file():
        raise ModelResolutionError(f"{p} 不存在（渲染产物缺执行面注册表——重新 ap init）")
    data = json.loads(p.read_text(encoding="utf-8"))
    gw = data.get("gateway") or {}
    base_url, api_key = gw.get("base_url", ""), gw.get("api_key", "")
    registry: dict[str, dict] = {}
    for alias in data.get("aliases", []):
        registry[str(alias)] = {
            "provider": "openai",
            "model": str(alias),
            "base_url": base_url,
            "api_key": api_key,
        }
    default = str(data.get("default") or "")
    if default:
        registry["default"] = dict(registry.get(default) or {"provider": "openai", "model": default})
        registry["default"].update({"base_url": base_url, "api_key": api_key, "model": default})
    if not registry:
        raise ModelResolutionError("models.json 空（声明面 models.yaml 无 alias？）")
    return registry


@dataclass(frozen=True)
class ResolvedModel:
    alias: str
    provider: str
    model: str
    api_base: str
    api_key: str  # 已解析值（仅内存传递——绝不入账本/日志/manifest）


class GatewayModelResolver:
    """alias 注册表（渲染配置 models 节）+ env 解析。"""

    def __init__(self, models: dict[str, dict]) -> None:
        """models：{alias: {provider, model, base_url(env:X|url), api_key(env:X)}}"""
        self._models = dict(models)

    def resolve(self, alias: str) -> ResolvedModel:
        entry = self._models.get(alias)
        if entry is None:
            raise ModelResolutionError(f"模型 alias 未登记：{alias}（合法：{sorted(self._models)}）")
        return ResolvedModel(
            alias=alias,
            provider=str(entry.get("provider", "OpenAI")),
            model=str(entry["model"]),
            api_base=self._env(entry.get("base_url", "")),
            api_key=self._env(entry.get("api_key", "")),
        )

    @staticmethod
    def _env(value: str) -> str:
        """env:X → os.environ[X]（缺失即 raise——不静默空串）。"""
        if value.startswith("env:"):
            name = value[4:]
            v = os.environ.get(name)
            if not v:
                raise ModelResolutionError(f"环境变量 {name} 未设置（模型网关凭据）")
            return v
        return value

    def as_callable(self):
        """适配上游 model_resolver 契约：str → {client/request 配置 dict}。

        runner 侧将其转换为真实 TeamModelConfig（lazy import 处）。
        """

        def _resolve(alias: str):
            r = self.resolve(alias)
            return {
                "model_client_config": {
                    "client_provider": r.provider,
                    "api_key": r.api_key,
                    "api_base": r.api_base,
                    # 网关按可信网络对待（与 config.yaml models.default 渲染的
                    # verify_ssl: False 同姿态）。上游语义 verify_ssl=True 必须
                    # 配 ssl_cert——内网/本机网关（http://host:4000）不适用，
                    # 不给则开箱即崩（openjiuwen base_model_client 校验）。
                    "verify_ssl": False,
                },
                "model_request_config": {"model": r.model},
            }

        return _resolve
