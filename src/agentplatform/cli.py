"""ap 命令行入口（子命令随后续 PR 逐步就位）。"""

from __future__ import annotations

import argparse
import json
import sys


def _cmd_spec_digest(args: argparse.Namespace) -> int:
    from agentplatform.spec import RegistryLoader

    snap = RegistryLoader().load(args.registry)
    print(snap.digest)
    return 0


def _cmd_spec_show(args: argparse.Namespace) -> int:
    from agentplatform.spec import RegistryLoader

    snap = RegistryLoader().load(args.registry)
    if args.entity:
        kind, _, ident = args.entity.partition(":")
        view = {"agent": snap.agents, "team": snap.teams, "tool": snap.tools}.get(kind)
        if view is None:
            print(f"未知实体类型: {kind}（agent:|team:|tool:）", file=sys.stderr)
            return 2
        ent = view.get(ident.removeprefix(""))
        if ent is None:
            print(f"未找到实体: {args.entity}", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "id": ent.id,
                    "status": ent.status,
                    "path": ent.rel_path,
                    "digest": ent.digest,
                    "raw": ent.raw,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    summary = {
        "digest": snap.digest,
        "agents": {a.id: a.status for a in snap.agents.values()},
        "teams": {t.id: t.status for t in snap.teams.values()},
        "tools": {t.id: t.status for t in snap.tools.values()},
        "models": sorted(snap.models),
        "resources": len(snap.resources),
        "files": len(snap.files),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    from agentplatform.render import Renderer
    from agentplatform.spec import RegistryLoader

    snap = RegistryLoader().load(args.registry)
    manifest = Renderer().render(snap, args.out)
    print(manifest.to_json())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ap", description="agent-platform 控制台")
    sub = parser.add_subparsers(dest="command")

    p_version = sub.add_parser("version", help="显示版本")
    p_version.set_defaults(
        func=lambda _args: print(f"agentplatform {__import__('agentplatform').__version__}")
    )

    p_spec = sub.add_parser("spec", help="声明快照（加载/指纹）")
    spec_sub = p_spec.add_subparsers(dest="spec_command", required=True)
    p_dig = spec_sub.add_parser("digest", help="快照摘要（渲染/漂移的声明面身份）")
    p_dig.add_argument("--registry", required=True, help="agent-registry checkout 路径")
    p_dig.set_defaults(func=_cmd_spec_digest)
    p_show = spec_sub.add_parser("show", help="快照概览或单实体详情")
    p_show.add_argument("--registry", required=True, help="agent-registry checkout 路径")
    p_show.add_argument("entity", nargs="?", help="实体引用，如 agent:reviewer")
    p_show.set_defaults(func=_cmd_spec_show)

    p_render = sub.add_parser("render", help="渲染声明 → jiuwenswarm workspace")
    p_render.add_argument("--registry", required=True, help="agent-registry checkout 路径")
    p_render.add_argument("--out", required=True, help="输出目录（如 ~/.agentplatform/workspace）")
    p_render.set_defaults(func=_cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    result = args.func(args)
    return 0 if result is None else result


if __name__ == "__main__":
    sys.exit(main())
