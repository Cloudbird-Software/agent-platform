"""agentctl 独立入口（console script）：agentctl <verb> [args] --state <dir>。

与 `ap ctl` 同一分发面——本模块只做 argv → dispatch 的接线。
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    from agentplatform.observe.agentctl import dispatch

    parser = argparse.ArgumentParser(prog="agentctl", description="内部运作干预命令面（JSON 输出）")
    parser.add_argument("verb_args", nargs=argparse.REMAINDER, help="动词及参数（help 列出全部）")
    parser.add_argument("--state", default="workspace/state", help="state 目录")
    args = parser.parse_args(argv)
    return dispatch(args.verb_args, args.state)


if __name__ == "__main__":
    sys.exit(main())
