#!/usr/bin/env python3
"""
Harness Log Viewer — logs/ 디렉토리를 사람이 읽기 쉬운 형태로 출력한다.

Usage:
    python3 scripts/show_logs.py           # 전체 phase 요약
    python3 scripts/show_logs.py <phase>   # 특정 phase 상세
"""

import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"

STATUS_ICON = {
    "completed": "✓",
    "completed_with_violations": "⚠",
    "error": "✗",
}

STATUS_COLOR = {
    "completed": "\033[32m",       # green
    "completed_with_violations": "\033[33m",  # yellow
    "error": "\033[31m",           # red
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def _color(text: str, code: str) -> str:
    return f"{code}{text}{RESET}"


def _load_logs(phase: Optional[str] = None) -> list[dict]:
    if not LOGS_DIR.exists():
        return []
    pattern = f"{phase}/step*.json" if phase else "*/step*.json"
    entries = []
    for f in sorted(LOGS_DIR.glob(pattern)):
        try:
            entries.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return entries


def _truncate(text: str, width: int) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_summary(entries: list[dict]):
    if not entries:
        print("로그가 없습니다. execute.py를 실행하면 자동으로 생성됩니다.")
        return

    phases: dict[str, list[dict]] = {}
    for e in entries:
        phases.setdefault(e.get("phase", "?"), []).append(e)

    print(f"\n{BOLD}{'PHASE':<20} {'STEPS':>5}  {'✓':>3} {'⚠':>3} {'✗':>3}  LAST ACTIVITY{RESET}")
    print("─" * 70)

    for phase, steps in sorted(phases.items()):
        ok = sum(1 for s in steps if s.get("status") == "completed")
        warn = sum(1 for s in steps if s.get("status") == "completed_with_violations")
        err = sum(1 for s in steps if s.get("status") == "error")
        last_ts = max((s.get("timestamp", "") for s in steps), default="")[:16].replace("T", " ")

        ok_str = _color(str(ok), "\033[32m") if ok else _color("0", DIM)
        warn_str = _color(str(warn), "\033[33m") if warn else _color("0", DIM)
        err_str = _color(str(err), "\033[31m") if err else _color("0", DIM)

        print(f"  {phase:<18} {len(steps):>5}  {ok_str:>3} {warn_str:>3} {err_str:>3}  {_color(last_ts, DIM)}")

    total = len(entries)
    v_count = sum(1 for e in entries if e.get("violations"))
    e_count = sum(1 for e in entries if e.get("status") == "error")
    print("─" * 70)
    print(f"  {'합계':<18} {total:>5}  ", end="")
    print(f"{_color(str(total - v_count - e_count), chr(27)+'[32m'):>3} ", end="")
    print(f"{_color(str(v_count), chr(27)+'[33m'):>3} ", end="")
    print(f"{_color(str(e_count), chr(27)+'[31m'):>3}\n")

    if v_count or e_count:
        hint = f"python3 scripts/show_logs.py <phase>"
        print(_color(f"  힌트: 위반/에러 상세 내용 → {hint}", DIM))
        print()


def _print_phase(phase: Optional[str], entries: list[dict]):
    if not entries:
        print(f"'{phase}' phase의 로그가 없습니다.")
        return

    print(f"\n{BOLD}  Phase: {phase}{RESET}\n")
    print(f"  {'STEP':<6} {'NAME':<20} {'STATUS':<12} {'TIME':<8}  DETAIL")
    print("  " + "─" * 72)

    for e in entries:
        step = e.get("step", "?")
        name = e.get("name", "?")
        status = e.get("status", "?")
        ts = e.get("timestamp", "")
        time_str = ts[11:16] if len(ts) >= 16 else ""

        icon = STATUS_ICON.get(status, "?")
        color = STATUS_COLOR.get(status, "")
        status_display = _color(f"{icon} {status}", color)

        violations = e.get("violations", "")
        error = e.get("error", "")
        detail = violations or error
        detail_str = _color(_truncate(detail, 40), "\033[33m" if violations else "\033[31m") if detail else ""

        print(f"  {step:<6} {name:<20} {status_display:<12}   {time_str:<8}  {detail_str}")

        if violations and len(violations) > 40:
            print()
            for line in violations.strip().splitlines():
                print(f"  {' '*38}{_color(line.strip(), DIM)}")
            print()

        if error and len(error) > 40:
            print()
            for line in error.strip().splitlines():
                print(f"  {' '*38}{_color(line.strip(), '\033[31m')}")
            print()

    print()


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else None
    entries = _load_logs(phase)

    if phase:
        _print_phase(phase, entries)
    else:
        _print_summary(entries)


if __name__ == "__main__":
    main()
