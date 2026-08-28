from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from manga_color_lib import MangaColorPipeline, PipelineError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manga-color")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Create a task and prepare or run line-art cleanup")
    start.add_argument("--source", type=Path, required=True)
    start.add_argument("--reference", type=Path, action="append", default=[])
    start.add_argument("--output-root", type=Path, default=Path.cwd() / "manga-coloring")
    start.add_argument("--task-name", default="manga")
    start.add_argument("--character-hint", default="")
    start.add_argument("--palette-notes", default="")
    start.add_argument("--allow-inferred-palette", action="store_true")
    start.add_argument("--provider", choices=["native", "openai"], default="native")
    start.add_argument("--profile", choices=["desktop-full", "web-light"], default="desktop-full")
    start.add_argument("--model", default=None)

    for name in ["approve-lineart", "status"]:
        command = commands.add_parser(name)
        command.add_argument("--task", type=Path, required=True)

    submit = commands.add_parser("submit-result")
    submit.add_argument("--task", type=Path, required=True)
    submit.add_argument("--stage", choices=["clean", "color"], required=True)
    submit.add_argument("--image", type=Path, required=True)
    submit.add_argument("--actual-model", default=None)

    for name in ["retry-clean", "retry-color"]:
        command = commands.add_parser(name)
        command.add_argument("--task", type=Path, required=True)
        command.add_argument("--feedback", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--task", type=Path, required=True)
    finalize.add_argument("--comparison-layout", choices=["none", "side_by_side", "top_bottom"], default="none")
    finalize.add_argument("--human-approved", action="store_true")

    export = commands.add_parser("export-task")
    export.add_argument("--task", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)

    import_command = commands.add_parser("import-task")
    import_command.add_argument("--bundle", type=Path, required=True)
    import_command.add_argument("--output-root", type=Path, required=True)
    import_command.add_argument("--profile", choices=["desktop-full", "web-light"], required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = MangaColorPipeline()
    if args.command == "start":
        return pipeline.start(
            source=args.source,
            references=args.reference,
            output_root=args.output_root,
            task_name=args.task_name,
            character_hint=args.character_hint,
            palette_notes=args.palette_notes,
            allow_inferred_palette=args.allow_inferred_palette,
            provider_name=args.provider,
            profile=args.profile,
            model=args.model,
        )
    if args.command == "submit-result":
        return pipeline.submit_result(args.task, args.stage, args.image, args.actual_model)
    if args.command == "approve-lineart":
        return pipeline.approve_lineart(args.task)
    if args.command == "retry-clean":
        return pipeline.retry_clean(args.task, args.feedback)
    if args.command == "retry-color":
        return pipeline.retry_color(args.task, args.feedback)
    if args.command == "finalize":
        return pipeline.finalize(args.task, args.comparison_layout, args.human_approved)
    if args.command == "export-task":
        return pipeline.export_task(args.task, args.output)
    if args.command == "import-task":
        return pipeline.import_task(args.bundle, args.output_root, args.profile)
    if args.command == "status":
        return pipeline.status(args.task)
    raise PipelineError(f"Unsupported command: {args.command}", code="unsupported_command")


def main() -> int:
    try:
        result = run(build_parser().parse_args())
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok", False) else 2
    except PipelineError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False))
        return 2 if exc.status == "NEEDS_INPUT" else 3
    except Exception as exc:
        payload = {"ok": False, "status": "FAILED", "task_dir": None, "error": {"code": exc.__class__.__name__, "message": str(exc)}, "next_action": "inspect_error"}
        print(json.dumps(payload, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
