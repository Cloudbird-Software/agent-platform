"""通道 ACL + artifact 写锁。

声明依据（team-collaboration）：
- channels.pub_sub.acl（模式 → read/write 主体表——ACL 唯一真源）；
- seats.write_exclusion 不变式：任一时刻对同一 artifact 处于 active 态的
  座位数 <= 1（enforced_by mechanism:scheduler——运行时写锁）。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from agentplatform.governance.ledger import EventLedger


class WriteLockError(Exception):
    """写锁冲突——同 artifact 已有 active 持有者。"""


class AclError(Exception):
    """ACL 拒绝——主体对通道无 op 权。"""


@dataclass(frozen=True)
class AclRule:
    pattern: str
    read: tuple[str, ...]
    write: tuple[str, ...]

    def matches(self, channel: str) -> bool:
        return fnmatch.fnmatchcase(channel, self.pattern)


class AclTable:
    """声明 acl 表的可执行形态。first-match 语义（声明表序即优先序）。"""

    def __init__(self, rules: list[AclRule]) -> None:
        self._rules = tuple(rules)

    @classmethod
    def from_channels(cls, channels_acl: dict) -> AclTable:
        """从声明 channels.pub_sub.acl 形态加载：{pattern: {read: [...], write: [...]}}。"""
        rules = [
            AclRule(
                pattern=str(pat),
                read=tuple(str(x) for x in (node.get("read") or [])),
                write=tuple(str(x) for x in (node.get("write") or [])),
            )
            for pat, node in channels_acl.items()
            if isinstance(node, dict)
        ]
        return cls(rules)

    def check(self, subject: str, channel: str, op: str) -> bool:
        """op ∈ {read, write}。无匹配规则=默认拒绝（fail-closed）。"""
        for r in self._rules:
            if r.matches(channel):
                allowed = r.read if op == "read" else r.write
                return subject in allowed
        return False

    def require(self, subject: str, channel: str, op: str) -> None:
        if not self.check(subject, channel, op):
            raise AclError(f"{subject} 对 {channel} 无 {op} 权（ACL 拒绝）")


class WriteLock:
    """artifact 写锁：同 artifact 同时至多一个 active 持有者。

    frozen 态持有者（如 amendment 期间 builder frozen）不算冲突——
    声明语义是 active 态互斥，不是持有记录互斥。
    """

    def __init__(self, ledger: EventLedger | None = None) -> None:
        self._holders: dict[str, tuple[str, str]] = {}  # artifact → (holder, state)
        self._ledger = ledger

    def acquire(self, seat: str, artifact: str, *, ts: float | None = None) -> None:
        cur = self._holders.get(artifact)
        if cur and cur[1] == "active":
            raise WriteLockError(f"artifact {artifact} 已被 {cur[0]} 持有（active）——写锁不变式")
        self._holders[artifact] = (seat, "active")
        if self._ledger:
            self._ledger.append("writelock.acquired", "mechanism:scheduler", {"artifact": artifact}, ts=ts)

    def freeze_holder(self, artifact: str) -> None:
        """持有者转 frozen（amendment 期间——不释放锁但不再互斥计数）。"""
        cur = self._holders.get(artifact)
        if cur:
            self._holders[artifact] = (cur[0], "frozen")

    def release(self, seat: str, artifact: str, *, ts: float | None = None) -> None:
        cur = self._holders.get(artifact)
        if cur and cur[0] == seat:
            del self._holders[artifact]
            if self._ledger:
                self._ledger.append(
                    "writelock.released", "mechanism:scheduler", {"artifact": artifact}, ts=ts
                )

    def holder(self, artifact: str) -> str | None:
        cur = self._holders.get(artifact)
        return cur[0] if cur else None
