"""TUI 仪表盘：投影视图 → ANSI 面板（纯标准库，零三方依赖）。

设计约束：
- 不依赖 curses（Windows/无 tty 环境不可用）——裸 ANSI 转义即可满足；
- NO_COLOR / --no-color 时退化为纯文本（输出可入日志/管道）；
- 渲染是 project() 的纯函数消费——TUI 永不自行推导状态。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"

CLEAR = "\033[2J\033[H"

# 状态着色：终态绿/暂停黄/冻结红/进行青
_STATE_COLOR = {
    "merged": GREEN,
    "archived": DIM,
    "aborted": RED,
    "paused": YELLOW,
    "verify": CYAN,
    "building": CYAN,
    "frozen": YELLOW,
}
_WIDTH = 78


def _c(code: str, text: str, enabled: bool) -> str:
    return f"{code}{text}{RESET}" if enabled else text


def render_dashboard(view: dict[str, Any], *, color: bool = True, now: float | None = None) -> str:
    """投影视图 → 仪表盘文本（单帧）。"""
    cfg = view.get("config") or {}
    bud = view["budget"]
    cards: dict[str, dict] = view["cards"]
    locks: dict[str, dict] = view["locks"]
    env = float(cfg.get("envelope_usd", 0))
    spent = float(bud["spent"])
    lines: list[str] = []

    # ── 头部：wave 状态 + 预算 ──
    if bud["frozen"]:
        wave = _c(BOLD + RED, "WAVE FROZEN", color)
    else:
        wave = _c(BOLD + GREEN, "WAVE ACTIVE", color)
    lines.append(f"┌─ {wave} " + "─" * max(0, _WIDTH - 14))
    pct = (spent / env * 100) if env else 0.0
    bar_len = 30
    filled = min(bar_len, int(pct / 100 * bar_len))
    bar = "█" * filled + "░" * (bar_len - filled)
    bar_color = GREEN if pct < 70 else (YELLOW if pct < 100 else RED)
    lines.append(
        f"│ envelope ${spent:.2f}/${env:.2f} {_c(bar_color, bar, color)} {pct:5.1f}%  "
        f"overhead ${bud['overhead_spent']:.2f}  denials {bud['denials']}"
    )
    if bud["frozen"]:
        reason = _c(RED, "冻结原因：" + str(bud["freeze_reason"]), color)
        lines.append(f"│ {reason}")
    lines.append("├" + "─" * _WIDTH)

    # ── 卡状态 ──
    lines.append(f"│ 卡（{len(cards)}）")
    if not cards:
        lines.append("│   " + _c(DIM, "（无 ratified 卡）", color))
    for cid, c in sorted(cards.items()):
        st = c["state"]
        col = _STATE_COLOR.get(st, "")
        lines.append(
            f"│   {cid:<24} {_c(col, st, color):<10} {c['change_class']:<8} "
            f"usd {c['card_usd']:.2f}/{c['budget_usd']:.2f}  {c['goal'][:28]}"
        )

    # ── 写锁 ──
    lines.append("├" + "─" * _WIDTH)
    lines.append(f"│ 写锁（{len(locks)}）")
    if not locks:
        lines.append("│   " + _c(DIM, "（无 active/frozen 锁）", color))
    for art, lk in sorted(locks.items()):
        col = YELLOW if lk["state"] == "frozen" else GREEN
        lines.append(f"│   {art:<32} {lk['holder']:<20} {_c(col, lk['state'], color)}")

    # ── 事件尾部 ──
    lines.append("├" + "─" * _WIDTH)
    lines.append(f"│ 最近事件（total {view['events_total']}）")
    for e in reversed(view["recent"][-8:]):
        ts = time.strftime("%H:%M:%S", time.localtime(e["ts"])) if e.get("ts") else "--:--:--"
        cid = e.get("card_id") or "-"
        lines.append(f"│   [{ts}] #{e['seq']:<4} {e['kind']:<22} {e['actor']:<22} {cid}")

    lines.append("└" + "─" * _WIDTH)
    head = (cfg.get("ledger_head") or "")[:12]
    ts_now = time.strftime("%Y-%m-%d %H:%M:%S")
    footer = f"  ledger head {head}…  events {view['events_total']}  ts {ts_now}"
    lines.append(_c(DIM, footer, color))
    return "\n".join(lines)


def run_tui(
    open_store,
    *,
    interval_s: float = 2.0,
    rounds: int | None = None,
    color: bool | None = None,
    stream=None,
) -> None:
    """循环重绘。open_store: () -> RuntimeStore（每轮重开——外部干预即时可见）。"""
    out = stream or sys.stdout
    use_color = (os.environ.get("NO_COLOR") is None) if color is None else color
    i = 0
    try:
        while rounds is None or i < rounds:
            store = open_store()
            frame = render_dashboard(store.snapshot(), color=use_color)
            if out is sys.stdout:
                print(CLEAR, end="", file=out)
            print(frame, file=out, flush=True)
            i += 1
            if rounds is None or i < rounds:
                time.sleep(interval_s)
    except KeyboardInterrupt:
        print(file=out)
