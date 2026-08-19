"""三层预算 + 升级通道永不冻结不变式。

声明依据（team-collaboration budget）：
- layers：team_envelope（usd+wall_clock 硬熔断，超即冻结全队+escalate owner）/
  per_card（tokens/wall_clock/retries，耗尽→freeze+amendment_or_escalate）/
  overhead_pool（researcher/judge/evidence-pack/escalation——让检索与升级对 agent 免费）；
- invariants（优先级高于一切池规则）：escalation 与 judge 调用永不受任何
  预算约束；预算耗尽本身即 escalation 事件。

时钟中立：wall_clock 由调用方传入 elapsed（不内置计时器——可测/可重放）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentplatform.governance.ledger import EventLedger

# 永不受预算约束的支出类目（不变式）
EXEMPT_CATEGORIES = frozenset({"escalation", "judge"})


@dataclass(frozen=True)
class BudgetResult:
    allowed: bool
    frozen: bool
    reason: str = ""


@dataclass
class _CardAccount:
    usd: float = 0.0
    tokens: int = 0
    retries_used: int = 0
    wall_clock: float = 0.0


@dataclass
class BudgetGovernor:
    envelope_usd: float
    overhead_usd: float
    ledger: EventLedger
    wall_clock_cap_s: float | None = None
    _spent: float = field(default=0.0, init=False)
    _overhead_spent: float = field(default=0.0, init=False)
    _elapsed: float = field(default=0.0, init=False)
    _cards: dict[str, _CardAccount] = field(default_factory=dict, init=False)
    _frozen: bool = field(default=False, init=False)

    # ---- 账面 ----
    @property
    def frozen(self) -> bool:
        return self._frozen

    def card_account(self, card_id: str) -> dict:
        a = self._cards.setdefault(card_id, _CardAccount())
        return {"usd": a.usd, "tokens": a.tokens, "retries_used": a.retries_used, "wall_clock": a.wall_clock}

    # ---- 计时（外部推进）----
    def tick(self, elapsed_s: float) -> None:
        self._elapsed += elapsed_s
        if self.wall_clock_cap_s is not None and self._elapsed >= self.wall_clock_cap_s and not self._frozen:
            self._freeze("wall_clock 耗尽（team_envelope 硬熔断）")

    # ---- 支出 ----
    def spend(
        self,
        amount_usd: float,
        *,
        category: str = "card",
        card_id: str | None = None,
        tokens: int = 0,
        ts: float | None = None,
    ) -> BudgetResult:
        """记账+闸门。EXEMPT 类目无条件放行（invariants 高于一切池规则）。"""
        if amount_usd < 0:
            return BudgetResult(False, self._frozen, "负数支出非法")

        if category in EXEMPT_CATEGORIES:
            # 记账到 overhead 面但永不拒绝——即使 overhead 超支也放行
            self._overhead_spent += amount_usd
            self.ledger.append(
                "budget.spent",
                "mechanism:scheduler",
                {"amount": amount_usd, "category": category, "exempt": True},
                card_id=card_id,
                ts=ts,
            )
            return BudgetResult(True, self._frozen)

        if self._frozen:
            self.ledger.append(
                "budget.denied", "mechanism:scheduler", {"reason": "team frozen"}, card_id=card_id, ts=ts
            )
            return BudgetResult(False, True, "团队已冻结（wave.frozen）——仅 escalation/judge 类支出放行")

        self._spent += amount_usd
        if card_id is not None:
            a = self._cards.setdefault(card_id, _CardAccount())
            a.usd += amount_usd
            a.tokens += tokens
        self.ledger.append(
            "budget.spent",
            "mechanism:scheduler",
            {"amount": amount_usd, "category": category, "tokens": tokens, "total_spent": self._spent},
            card_id=card_id,
            ts=ts,
        )

        if self._spent > self.envelope_usd:
            self._freeze(f"envelope 超限：{self._spent:.2f} > {self.envelope_usd:.2f}")
            return BudgetResult(False, True, "team_envelope 熔断——冻结全队+escalate owner")
        return BudgetResult(True, False)

    # ---- per_card retries（budget.enforcement 的相位载体）----
    def retry(self, card_id: str, *, max_retries: int, ts: float | None = None) -> BudgetResult:
        a = self._cards.setdefault(card_id, _CardAccount())
        a.retries_used += 1
        if a.retries_used >= max_retries:
            self.ledger.append(
                "retries.exhausted", "mechanism:scheduler", {"used": a.retries_used}, card_id=card_id, ts=ts
            )
            return BudgetResult(
                False, self._frozen, f"卡 {card_id} retries 耗尽（{a.retries_used}）——回炉重规划"
            )
        return BudgetResult(True, self._frozen)

    # ---- 内部 ----
    def _freeze(self, reason: str) -> None:
        if not self._frozen:
            self._frozen = True
            self.ledger.append("wave.frozen", "mechanism:scheduler", {"reason": reason})

    # ---- 重放恢复（事件溯源）----
    @classmethod
    def from_events(
        cls,
        ledger: EventLedger,
        *,
        envelope_usd: float,
        overhead_usd: float,
        wall_clock_cap_s: float | None = None,
    ) -> BudgetGovernor:
        """从账本事件重建账面（spent/overhead/elapsed/frozen/卡账户）。

        注意：重放只恢复账面数字，不重复触发熔断——冻结是历史事实（wave.frozen
        事件已存在），恢复后 frozen 态延续。
        """
        gov = cls(
            envelope_usd=envelope_usd,
            overhead_usd=overhead_usd,
            ledger=ledger,
            wall_clock_cap_s=wall_clock_cap_s,
        )
        for e in ledger.events():
            if e.kind == "budget.spent":
                amt = float(e.payload.get("amount", 0))
                if e.payload.get("exempt"):
                    gov._overhead_spent += amt
                else:
                    gov._spent += amt
                    cid = e.card_id
                    if cid is not None:
                        a = gov._cards.setdefault(cid, _CardAccount())
                        a.usd += amt
                        a.tokens += int(e.payload.get("tokens", 0))
            elif e.kind == "wave.frozen":
                gov._frozen = True
        return gov
