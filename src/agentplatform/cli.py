"""ap 命令行入口（骨架——子命令随后续 PR 逐步就位）。"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ap", description="agent-platform 控制台")
    sub = parser.add_subparsers(dest="command")

    p_version = sub.add_parser("version", help="显示版本")
    p_version.set_defaults(
        func=lambda _args: print(f"agentplatform {__import__('agentplatform').__version__}")
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
