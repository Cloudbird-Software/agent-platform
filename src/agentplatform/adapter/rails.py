"""ToolRails：capabilities.allow 声明 → 工具白名单轨道（fail-closed）。

声明依据（archetype-profiles / agent 声明 capabilities.allow）：
worker 可用的工具集由声明收窄——运行时（openjiuwen tool_permissions 是
静态 leader/member 分类）没有 per-seat 白名单，本轨道在 adapter 层补齐。

语义：
- patterns 为 fnmatch 模式列表（如 ["file.*", "git.pr", "web.search"]）；
- check(tool)：任一模式匹配 → allow；无匹配 → deny（fail-closed）；
- 空 patterns = 全拒（声明缺位不是放行理由）；
- filter(tools)：批量过滤（组装 worker 工具集时用）。
"""

from __future__ import annotations

import fnmatch


class ToolRails:
    def __init__(self, patterns: list[str] | tuple[str, ...]) -> None:
        self._patterns = tuple(patterns)

    @property
    def patterns(self) -> tuple[str, ...]:
        return self._patterns

    def check(self, tool: str) -> bool:
        """fail-closed：无匹配即拒绝。"""
        return any(fnmatch.fnmatchcase(tool, p) for p in self._patterns)

    def filter(self, tools: list[str]) -> list[str]:
        return [t for t in tools if self.check(t)]

    def audit(self, tools: list[str]) -> dict:
        """对账视图：allowed/denied 分列（渲染 notes 与 TUI 共用）。"""
        allowed = self.filter(tools)
        return {"allowed": allowed, "denied": [t for t in tools if t not in allowed]}
