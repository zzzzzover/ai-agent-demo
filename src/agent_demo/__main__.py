"""命令行入口。

    uv run agent-demo --root ./examples --task "把 notes 目录整理成一份带出处的报告"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_BASE_URL, DEFAULT_MODEL, Config, load_env_file
from .llm import DeepSeekLLM, LLMError
from .loop import AgentLoop
from .safety import CommandAllowlist, PathSandbox, SandboxError
from .tools import ToolContext, build_registry
from .trace import Tracer

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-demo",
        description="手写的工具调用型 CLI Agent：自主探查目录、读取资料、写出带出处的报告。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=".", help="工作目录，agent 只能访问它内部的路径（默认 .）")
    parser.add_argument("--task", required=True, help="自然语言任务描述")
    parser.add_argument("--out-dir", default="agent_out", help="产出目录，必须位于 --root 内（默认 agent_out）")
    parser.add_argument("--max-steps", type=int, default=12, help="最多几轮 LLM 交互（默认 12）")
    parser.add_argument("--model", default=None, help=f"模型名（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--base-url", default=None, help=f"接口地址（默认 {DEFAULT_BASE_URL}）")
    parser.add_argument(
        "--allow-cmd",
        action="append",
        default=[],
        help="放行的命令名，可重复或用逗号分隔；不传则完全禁用命令执行",
    )
    parser.add_argument("--env-file", default=".env", help="从该文件读取环境变量（默认 .env）")
    parser.add_argument("--dry-run", action="store_true", help="只演练：不真正写文件、不真正执行命令")
    parser.add_argument("--quiet", action="store_true", help="只打印最终结论，不逐条打印 trace")
    return parser


def _split_commands(values: list[str]) -> tuple[str, ...]:
    names: list[str] = []
    for value in values:
        names.extend(part.strip() for part in value.split(","))
    return tuple(name for name in names if name)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file(Path(args.env_file))

    cfg = Config.from_env(
        root=args.root,
        task=args.task,
        out_dir=args.out_dir,
        max_steps=args.max_steps,
        model=args.model or DEFAULT_MODEL,
        base_url=args.base_url or DEFAULT_BASE_URL,
        allow_cmds=_split_commands(args.allow_cmd),
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )

    # ---- 前置校验：宁可现在就报错，也不要跑到一半才发现 ----
    try:
        sandbox = PathSandbox(cfg.root, cfg.out_dir)
    except SandboxError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    if not cfg.api_key:
        print(
            "[配置错误] 缺少 DEEPSEEK_API_KEY。\n"
            "  export DEEPSEEK_API_KEY=sk-...\n"
            "或复制 .env.example 为 .env 并填入。",
            file=sys.stderr,
        )
        return 2

    ctx = ToolContext(sandbox=sandbox, allowlist=CommandAllowlist(cfg.allow_cmds), dry_run=cfg.dry_run)
    registry = build_registry(ctx)

    try:
        llm = DeepSeekLLM(
            cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            timeout=cfg.timeout,
        )
    except LLMError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    with Tracer(cfg.trace_path, verbose=cfg.verbose) as tracer:
        if cfg.verbose:
            print(f"run       : {tracer.run_id}")
            print(f"root      : {sandbox.root}")
            print(f"out-dir   : {sandbox.rel(sandbox.out_dir)}/")
            print(f"model     : {cfg.model}")
            print(f"tools     : {', '.join(registry.names())}")
            print(f"commands  : {', '.join(cfg.allow_cmds) or '(禁用)'}")
            print(f"max-steps : {cfg.max_steps}{'  [dry-run]' if cfg.dry_run else ''}")
            print(f"task      : {cfg.task}")
            print("-" * 64)

        tracer.event(
            "run_start",
            root=str(sandbox.root),
            out_dir=sandbox.rel(sandbox.out_dir),
            model=cfg.model,
            task=cfg.task,
            max_steps=cfg.max_steps,
            allow_cmds=list(cfg.allow_cmds),
            dry_run=cfg.dry_run,
        )

        result = AgentLoop(
            llm,
            registry,
            tracer,
            max_steps=cfg.max_steps,
        ).run(cfg.task)

        print("-" * 64)
        print(f"状态      : {result.status}")
        print(f"轮次      : {result.steps}")
        print(
            f"token     : prompt={tracer.prompt_tokens} "
            f"completion={tracer.completion_tokens} 合计={tracer.total_tokens}"
        )
        print(f"trace     : {sandbox.rel(cfg.trace_path)}  (run={tracer.run_id})")
        print()
        print(result.summary)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
