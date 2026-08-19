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


def _cmd_flow_check(args: argparse.Namespace) -> int:
    from agentplatform.flow import load_phase_graph, validate_graph

    graph = load_phase_graph(args.registry)
    issues = validate_graph(graph)
    print(
        json.dumps(
            {
                "phase_graph_digest": graph.digest,
                "phases": list(graph.phases),
                "edges": len(graph.edges),
                "producers": len(graph.producers),
                "issues": [f"{i.rule}: {i.message}" for i in issues],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if issues else 0


def _cmd_flow_compile(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agentplatform.flow import flow_outputs, load_phase_graph
    from agentplatform.spec import RegistryLoader

    snap = RegistryLoader().load(args.registry)
    graph = load_phase_graph(args.registry)
    outs = flow_outputs(snap, graph)
    out = Path(args.out)
    for rel, text in sorted(outs.items()):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(json.dumps({"compiled": sorted(outs), "spec_digest": snap.digest}, ensure_ascii=False, indent=2))
    return 0


def _cmd_flow_dryrun(args: argparse.Namespace) -> int:
    from agentplatform.flow import dryrun_registry

    report = dryrun_registry(args.registry)
    ok = not report["graph_issues"] and not any(report["teams"].values())  # type: ignore[arg-type]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _cmd_drift_check(args: argparse.Namespace) -> int:
    from agentplatform.drift import check_workspace

    report = check_workspace(args.registry, args.workspace, skip_spec=args.skip_spec)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def _cmd_drift_watch(args: argparse.Namespace) -> int:
    import sys as _sys

    from agentplatform.drift.checker import watch

    def emit(ev: dict) -> None:
        print(json.dumps(ev, ensure_ascii=False), file=_sys.stdout, flush=True)

    watch(
        args.registry,
        args.workspace,
        interval_s=args.interval,
        max_rounds=args.rounds,
        emit=emit,
    )
    return 0


def _cmd_tui(args: argparse.Namespace) -> int:
    from agentplatform.observe import RuntimeStore
    from agentplatform.observe.tui import run_tui

    run_tui(
        lambda: RuntimeStore.open(args.state),
        interval_s=args.interval,
        rounds=args.rounds,
        color=None if args.color else False,
    )
    return 0


def _cmd_ctl(args: argparse.Namespace) -> int:
    from agentplatform.observe.agentctl import dispatch

    return dispatch(args.verb_args, args.state)


def _cmd_init(args: argparse.Namespace) -> int:
    from agentplatform.bootstrap import init_workspace

    summary = init_workspace(args.registry, args.out, envelope_usd=args.envelope, overhead_usd=args.overhead)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from agentplatform.bootstrap import run_doctor

    result = run_doctor(registry=args.registry, workspace=args.workspace, human=not args.json)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _cmd_envfile(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agentplatform.bootstrap import render_envfile

    text = render_envfile(args.workspace)
    if args.write:
        p = Path(args.workspace) / ".env.example"
        p.write_text(text, encoding="utf-8")
        print(json.dumps({"written": str(p)}, ensure_ascii=False))
    else:
        print(text)
    return 0


def _cmd_up(args: argparse.Namespace) -> int:
    from agentplatform.bootstrap import UpError, run_up

    try:
        summary = run_up(
            args.workspace,
            args.team,
            state=args.state,
            dry_run=args.dry_run,
            model=args.model,
            args_json=args.args_json,
        )
    except UpError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary.get("ok", True) else 1


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

    p_flow = sub.add_parser("flow", help="相位图校验/SwarmFlow 编译/dry-run")
    flow_sub = p_flow.add_subparsers(dest="flow_command", required=True)
    p_chk = flow_sub.add_parser("check", help="声明相位图验证（死锁/可达/悬空事件）")
    p_chk.add_argument("--registry", required=True)
    p_chk.set_defaults(func=_cmd_flow_check)
    p_cmp = flow_sub.add_parser("compile", help="编译声明 → SwarmFlow 脚本")
    p_cmp.add_argument("--registry", required=True)
    p_cmp.add_argument("--out", required=True, help="输出目录（swarmflow/<team>.py）")
    p_cmp.set_defaults(func=_cmd_flow_compile)
    p_dry = flow_sub.add_parser("dryrun", help="全链路静态演练（图+编译+lint）")
    p_dry.add_argument("--registry", required=True)
    p_dry.set_defaults(func=_cmd_flow_dryrun)

    p_drift = sub.add_parser("drift", help="声明↔渲染↔磁盘 三方对账")
    drift_sub = p_drift.add_subparsers(dest="drift_command", required=True)
    p_dchk = drift_sub.add_parser("check", help="一次对账（漂移即退出码 1）")
    p_dchk.add_argument("--registry", required=True)
    p_dchk.add_argument("--workspace", required=True)
    p_dchk.add_argument("--skip-spec", action="store_true", help="只对账文件面（声明仓不可用降级）")
    p_dchk.set_defaults(func=_cmd_drift_check)
    p_dwatch = drift_sub.add_parser("watch", help="周期对账（JSONL 事件流）")
    p_dwatch.add_argument("--registry", required=True)
    p_dwatch.add_argument("--workspace", required=True)
    p_dwatch.add_argument("--interval", type=float, default=30.0)
    p_dwatch.add_argument("--rounds", type=int, default=None)
    p_dwatch.set_defaults(func=_cmd_drift_watch)

    p_tui = sub.add_parser("tui", help="内部运作仪表盘（事件流投影，实时重绘）")
    p_tui.add_argument("--state", required=True, help="workspace/state 目录（RuntimeStore）")
    p_tui.add_argument("--interval", type=float, default=2.0, help="刷新间隔秒")
    p_tui.add_argument("--rounds", type=int, default=None, help="帧数（默认无限，Ctrl-C 退出）")
    p_tui.add_argument("--color", action="store_true", help="强制颜色（默认 NO_COLOR 环境变量决定）")
    p_tui.set_defaults(func=_cmd_tui)

    p_ctl = sub.add_parser("ctl", help="干预动词（JSON 输出——供外部 agent 调用）")
    p_ctl.add_argument("--state", default="workspace/state", help="state 目录")
    p_ctl.add_argument("verb_args", nargs=argparse.REMAINDER, help="动词及参数（ap ctl help 列出全部）")
    p_ctl.set_defaults(func=_cmd_ctl)

    p_init = sub.add_parser("init", help="workspace 初始化（渲染+state+env 模板，幂等）")
    p_init.add_argument(
        "--registry", default=None, help="agent-registry 路径（默认 vendor/agent-registry 快照）"
    )
    p_init.add_argument("--out", required=True, help="workspace 输出目录")
    p_init.add_argument("--envelope", type=float, default=100.0, help="team_envelope usd")
    p_init.add_argument("--overhead", type=float, default=20.0, help="overhead_pool usd")
    p_init.set_defaults(func=_cmd_init)

    p_doc = sub.add_parser("doctor", help="开箱自检（env/渲染/漂移/账本/runtime）")
    p_doc.add_argument("--registry", default=None, help="agent-registry 路径（drift 面需要）")
    p_doc.add_argument("--workspace", default=None, help="workspace 目录")
    p_doc.add_argument("--json", action="store_true", help="JSON 输出（默认人读表格）")
    p_doc.set_defaults(func=_cmd_doctor)

    p_env = sub.add_parser("envfile", help="生成 .env.example（manifest.env_refs 驱动）")
    p_env.add_argument("--workspace", required=True, help="workspace 目录")
    p_env.add_argument("--write", action="store_true", help="写入 <workspace>/.env.example（默认打印）")
    p_env.set_defaults(func=_cmd_envfile)

    p_up = sub.add_parser("up", help="执行团队 SwarmFlow（live 全挂点；--dry-run 零调用预检）")
    p_up.add_argument("--workspace", required=True, help="workspace 目录")
    p_up.add_argument("--team", required=True, help="团队 id（swarmflow/<team>.py）")
    p_up.add_argument("--state", default=None, help="state 目录（默认 <workspace>/state）")
    p_up.add_argument("--dry-run", action="store_true", help="预检：渲染/脚本/凭据，不发起 LLM 调用")
    p_up.add_argument("--model", default=None, help="默认模型 alias（缺省取渲染配置首个）")
    p_up.add_argument("--args-json", default=None, help="flow run(args) 入参（JSON 字符串）")
    p_up.set_defaults(func=_cmd_up)

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
