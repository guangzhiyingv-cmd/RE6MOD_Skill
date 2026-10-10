#!/usr/bin/env python3
"""Run a project command with compact output and persistent run state."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    argv = sys.argv[1:]
    if "--" not in argv:
        raise SystemExit("Expected '--' before the command to run.")
    split_index = argv.index("--")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--next-stage")
    parser.add_argument("--next-action")
    parser.add_argument("--tail-lines", type=int, default=30)
    args = parser.parse_args(argv[:split_index])
    command = argv[split_index + 1 :]
    if not command:
        raise SystemExit("No command was provided after '--'.")
    return args, command


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_path(value: Path | None, base: Path) -> Path | None:
    if value is None:
        return None
    return value if value.is_absolute() else (base / value).resolve()


def main() -> int:
    args, command = parse_args()
    project_path = args.project.resolve()
    manifest = load_json(project_path)
    project_root = Path(manifest["paths"]["project_root"]).resolve()
    execution_mode = str(
        manifest.get("settings", {}).get("execution_mode", "normal")
    )

    log_path = resolve_path(args.log, project_root) or (
        project_root / "reports" / "logs" / f"{args.stage}.log"
    )
    state_path = resolve_path(args.state, project_root) or (
        project_root / "reports" / "run_state.json"
    )
    input_path = resolve_path(args.input, project_root)
    output_path = resolve_path(args.output, project_root)
    report_path = resolve_path(args.report, project_root)
    command_cwd = args.cwd.resolve() if args.cwd else Path.cwd()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    tail: deque[str] = deque(maxlen=max(0, args.tail_lines))
    with log_path.open("w", encoding="utf-8", newline="") as log_stream:
        process = subprocess.Popen(
            command,
            cwd=str(command_cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            log_stream.write(line)
            if tail.maxlen:
                tail.append(line.rstrip("\r\n"))
        returncode = process.wait()

    validation_errors = []
    if output_path is not None and not output_path.is_file():
        validation_errors.append(f"Expected output was not created: {output_path}")
    if report_path is not None and not report_path.is_file():
        validation_errors.append(f"Expected report was not created: {report_path}")
    status = (
        "passed"
        if returncode == 0 and not validation_errors
        else "failed"
    )
    result = {
        "stage": args.stage,
        "status": status,
        "returncode": returncode,
        "execution_mode": execution_mode,
        "cwd": str(command_cwd),
        "log": str(log_path),
        "input": str(input_path) if input_path else None,
        "output": str(output_path) if output_path else None,
        "report": str(report_path) if report_path else None,
        "next_stage": args.next_stage,
        "next_action": args.next_action,
        "validation_errors": validation_errors,
        "error_tail": list(tail) if status == "failed" else [],
    }

    state = load_json(state_path) if state_path.is_file() else {}
    state.update(
        {
            "execution_mode": execution_mode,
            "cwd": str(command_cwd),
            "current_stage": args.stage,
            "status": status,
            "input_blend": str(input_path) if input_path else None,
            "output_blend": str(output_path) if output_path else None,
            "report": str(report_path) if report_path else None,
            "log": str(log_path),
            "next_stage": args.next_stage,
            "next_action": args.next_action,
            "blockers": (
                validation_errors + list(tail)
                if status == "failed"
                else []
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_json(state_path, state)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
