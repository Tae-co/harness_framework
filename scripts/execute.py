#!/usr/bin/env python3
"""
Harness Step Executor — phase 내 step을 순차 실행하고 자가 교정한다.

Usage:
    python3 scripts/execute.py <phase-dir> [--push]
"""

import argparse
import contextlib
import fnmatch
import json
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def progress_indicator(label: str):
    """터미널 진행 표시기. with 문으로 사용하며 .elapsed 로 경과 시간을 읽는다."""
    frames = "◐◓◑◒"
    stop = threading.Event()
    t0 = time.monotonic()

    def _animate():
        idx = 0
        while not stop.wait(0.12):
            sec = int(time.monotonic() - t0)
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {label} [{sec}s]")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write("\r" + " " * (len(label) + 20) + "\r")
        sys.stderr.flush()

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    info = types.SimpleNamespace(elapsed=0.0)
    try:
        yield info
    finally:
        stop.set()
        th.join()
        info.elapsed = time.monotonic() - t0


class StepExecutor:
    """Phase 디렉토리 안의 step들을 순차 실행하는 하네스."""

    MAX_RETRIES = 3
    FEAT_MSG = "feat({phase}): step {num} — {name}"
    CHORE_MSG = "chore({phase}): step {num} output"
    TZ = timezone(timedelta(hours=9))
    SENSITIVE_PATTERNS = [
        ".env", ".env.*", "*.key", "*.pem", "*.p12", "*.pfx",
        "*credentials*", "*secrets*", "*.secret",
    ]

    def __init__(self, phase_dir_name: str, *, auto_push: bool = False):
        self._root = str(ROOT)
        self._phases_dir = ROOT / "phases"
        self._phase_dir = self._phases_dir / phase_dir_name
        self._phase_dir_name = phase_dir_name
        self._top_index_file = self._phases_dir / "index.json"
        self._auto_push = auto_push

        if not self._phase_dir.is_dir():
            print(f"ERROR: {self._phase_dir} not found")
            sys.exit(1)

        self._index_file = self._phase_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._phase_name = idx.get("phase", phase_dir_name)
        self._total = len(idx["steps"])

    def run(self):
        self._print_header()
        self._check_blockers()
        self._checkout_branch()
        guardrails = self._load_guardrails()
        self._ensure_created_at()
        self._execute_all_steps(guardrails)
        self._finalize()
        self._move_plan_to_completed()
        self._evolve_rules()

    # --- timestamps ---

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- JSON I/O ---

    @staticmethod
    def _read_json(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(p: Path, data: dict):
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- git ---

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self._root, capture_output=True, text=True)

    def _checkout_branch(self):
        branch = f"feat-{self._phase_name}"

        r = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            print(f"  ERROR: git을 사용할 수 없거나 git repo가 아닙니다.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        if r.stdout.strip() == branch:
            return

        r = self._run_git("rev-parse", "--verify", branch)
        r = self._run_git("checkout", branch) if r.returncode == 0 else self._run_git("checkout", "-b", branch)

        if r.returncode != 0:
            print(f"  ERROR: 브랜치 '{branch}' checkout 실패.")
            print(f"  {r.stderr.strip()}")
            print(f"  Hint: 변경사항을 stash하거나 commit한 후 다시 시도하세요.")
            sys.exit(1)

        print(f"  Branch: {branch}")

    def _unstage_sensitive_files(self):
        result = self._run_git("diff", "--cached", "--name-only")
        for f in result.stdout.strip().splitlines():
            basename = Path(f).name
            if any(fnmatch.fnmatch(basename, pat) for pat in self.SENSITIVE_PATTERNS):
                self._run_git("reset", "HEAD", "--", f)
                print(f"  WARN: 민감 파일 스테이징 제외: {f}")

    def _commit_step(self, step_num: int, step_name: str):
        output_rel = f"phases/{self._phase_dir_name}/step{step_num}-output.json"
        index_rel = f"phases/{self._phase_dir_name}/index.json"

        self._run_git("add", "-A")
        self._unstage_sensitive_files()
        self._run_git("reset", "HEAD", "--", output_rel)
        self._run_git("reset", "HEAD", "--", index_rel)

        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(phase=self._phase_name, num=step_num, name=step_name)
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                print(f"  WARN: 코드 커밋 실패: {r.stderr.strip()}")

        self._run_git("add", "-A")
        self._unstage_sensitive_files()
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num)
            r = self._run_git("commit", "-m", msg)
            if r.returncode != 0:
                print(f"  WARN: housekeeping 커밋 실패: {r.stderr.strip()}")

    # --- top-level index ---

    def _update_top_index(self, status: str):
        if not self._top_index_file.exists():
            return
        top = self._read_json(self._top_index_file)
        ts = self._stamp()
        for phase in top.get("phases", []):
            if phase.get("dir") == self._phase_dir_name:
                phase["status"] = status
                ts_key = {"completed": "completed_at", "error": "failed_at", "blocked": "blocked_at"}.get(status)
                if ts_key:
                    phase[ts_key] = ts
                break
        self._write_json(self._top_index_file, top)

    # --- guardrails & context ---

    def _load_guardrails(self) -> str:
        sections = []
        claude_md = ROOT / "CLAUDE.md"
        if claude_md.exists():
            sections.append(f"## 프로젝트 규칙 (CLAUDE.md)\n\n{claude_md.read_text()}")
        docs_dir = ROOT / "docs"
        if docs_dir.is_dir():
            for doc in sorted(docs_dir.glob("*.md")):
                sections.append(f"## {doc.stem}\n\n{doc.read_text()}")
        violations_history = self._load_recent_violations()
        if violations_history:
            sections.append(violations_history)
        return "\n\n---\n\n".join(sections) if sections else ""

    def _load_recent_violations(self) -> str:
        logs_dir = ROOT / "logs"
        if not logs_dir.exists():
            return ""
        entries = []
        for log_file in logs_dir.glob("*/*.json"):
            try:
                data = json.loads(log_file.read_text(encoding="utf-8"))
                if data.get("violations"):
                    entries.append(data)
            except Exception:
                continue
        if not entries:
            return ""
        entries.sort(key=lambda d: d.get("timestamp", ""))
        lines = [
            f"- [{d.get('timestamp', '')[:10]}] {d.get('phase', '')} {d.get('name', '')}: {d['violations']}"
            for d in entries[-10:]
        ]
        return "## 과거 규칙 위반 이력 (동일한 실수를 반복하지 마라)\n\n" + "\n".join(lines)

    def _write_step_log(self, step_num: int, step_name: str, status: str,
                        violations: Optional[str] = None, error: Optional[str] = None):
        log_dir = ROOT / "logs" / self._phase_dir_name
        log_dir.mkdir(parents=True, exist_ok=True)
        entry: dict = {
            "timestamp": self._stamp(),
            "phase": self._phase_dir_name,
            "step": step_num,
            "name": step_name,
            "status": status,
        }
        if violations:
            entry["violations"] = violations
        if error:
            entry["error"] = error
        log_file = log_dir / f"step{step_num}.json"
        log_file.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _build_step_context(index: dict) -> str:
        lines = [
            f"- Step {s['step']} ({s['name']}): {s['summary']}"
            for s in index["steps"]
            if s["status"] == "completed" and s.get("summary")
        ]
        if not lines:
            return ""
        return "## 이전 Step 산출물\n\n" + "\n".join(lines) + "\n\n"

    def _build_preamble(self, guardrails: str, step_context: str,
                        prev_error: Optional[str] = None) -> str:
        commit_example = self.FEAT_MSG.format(
            phase=self._phase_name, num="N", name="<step-name>"
        )
        retry_section = ""
        if prev_error:
            retry_section = (
                f"\n## ⚠ 이전 시도 실패 — 아래 에러를 반드시 참고하여 수정하라\n\n"
                f"{prev_error}\n\n---\n\n"
            )
        return (
            f"당신은 {self._project} 프로젝트의 개발자입니다. 아래 step을 수행하세요.\n\n"
            f"{guardrails}\n\n---\n\n"
            f"{step_context}{retry_section}"
            f"## 작업 규칙\n\n"
            f"1. 이전 step에서 작성된 코드를 확인하고 일관성을 유지하라.\n"
            f"2. 이 step에 명시된 작업만 수행하라. 추가 기능이나 파일을 만들지 마라.\n"
            f"3. 기존 테스트를 깨뜨리지 마라.\n"
            f"4. AC(Acceptance Criteria) 검증을 직접 실행하라.\n"
            f"5. /phases/{self._phase_dir_name}/index.json의 해당 step status를 업데이트하라:\n"
            f"   - AC 통과 → \"completed\" + \"summary\" 필드에 이 step의 산출물을 한 줄로 요약\n"
            f"   - {self.MAX_RETRIES}회 수정 시도 후에도 실패 → \"error\" + \"error_message\" 기록\n"
            f"   - 사용자 개입이 필요한 경우 (API 키, 인증, 수동 설정 등) → \"blocked\" + \"blocked_reason\" 기록 후 즉시 중단\n"
            f"6. 모든 변경사항을 커밋하라:\n"
            f"   {commit_example}\n\n---\n\n"
        )

    # --- Claude 호출 ---

    def _review_step(self, step: dict) -> Optional[str]:
        """완료된 step의 규칙 위반 여부를 검토한다. 위반 있으면 문자열, 없으면 None."""
        step_num = step["step"]
        step_file = self._phase_dir / f"step{step_num}.md"
        if not step_file.exists():
            return None

        step_content = step_file.read_text()
        modified = self._run_git("diff", "HEAD", "--name-only").stdout.strip()
        staged = self._run_git("diff", "--cached", "--name-only").stdout.strip()
        untracked = self._run_git("ls-files", "--others", "--exclude-standard").stdout.strip()
        all_changed = "\n".join(filter(None, [modified, staged, untracked])) or "(변경 없음)"

        claude_md = ROOT / "CLAUDE.md"
        rules = claude_md.read_text() if claude_md.exists() else ""

        review_prompt = (
            f"방금 실행된 step을 규칙 준수 여부 관점에서 검토하라.\n\n"
            f"## Step 지시사항\n\n{step_content}\n\n"
            f"## 실제로 변경된 파일\n\n{all_changed}\n\n"
            f"## 프로젝트 규칙 (CLAUDE.md)\n\n{rules}\n\n"
            f"## 검토 기준\n\n"
            f"1. step 지시사항의 범위를 벗어난 파일을 수정했는가?\n"
            f"2. 새 기능/로직 추가 시 테스트가 함께 작성됐는가?\n"
            f"3. CLAUDE.md의 CRITICAL 규칙을 위반했는가?\n\n"
            f"위반이 없으면 정확히 'OK'만 출력하라.\n"
            f"위반이 있으면 항목별로 간결하게 나열하라. 'OK'를 포함하지 마라."
        )

        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", review_prompt],
            cwd=self._root, capture_output=True, text=True, timeout=300,
        )

        output = result.stdout.strip()
        if not output or output.upper() == "OK":
            return None
        return f"[규칙 위반 감지]\n{output}"

    def _invoke_claude(self, step: dict, preamble: str) -> dict:
        step_num, step_name = step["step"], step["name"]
        step_file = self._phase_dir / f"step{step_num}.md"

        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        prompt = preamble + step_file.read_text()
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "json", prompt],
            cwd=self._root, capture_output=True, text=True, timeout=1800,
        )

        if result.returncode != 0:
            print(f"\n  WARN: Claude가 비정상 종료됨 (code {result.returncode})")
            if result.stderr:
                print(f"  stderr: {result.stderr[:500]}")

        output = {
            "step": step_num, "name": step_name,
            "exitCode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        }
        out_path = self._phase_dir / f"step{step_num}-output.json"
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

        return output

    # --- 헤더 & 검증 ---

    def _print_header(self):
        print(f"\n{'='*60}")
        print(f"  Harness Step Executor")
        print(f"  Phase: {self._phase_name} | Steps: {self._total}")
        if self._auto_push:
            print(f"  Auto-push: enabled")
        print(f"{'='*60}")

    def _check_blockers(self):
        index = self._read_json(self._index_file)
        for s in reversed(index["steps"]):
            if s["status"] == "error":
                print(f"\n  ✗ Step {s['step']} ({s['name']}) failed.")
                print(f"  Error: {s.get('error_message', 'unknown')}")
                print(f"  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if s["status"] == "blocked":
                print(f"\n  ⏸ Step {s['step']} ({s['name']}) blocked.")
                print(f"  Reason: {s.get('blocked_reason', 'unknown')}")
                print(f"  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if s["status"] != "pending":
                break

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    # --- 실행 루프 ---

    def _execute_single_step(self, step: dict, guardrails: str) -> bool:
        """단일 step 실행 (재시도 포함). 완료되면 True, 실패/차단이면 False."""
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for s in self._read_json(self._index_file)["steps"] if s["status"] == "completed")
        prev_error = None
        last_error = None
        same_error_count = 0

        for attempt in range(1, self.MAX_RETRIES + 1):
            index = self._read_json(self._index_file)
            step_context = self._build_step_context(index)
            preamble = self._build_preamble(guardrails, step_context, prev_error)

            tag = f"Step {step_num}/{self._total - 1} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            with progress_indicator(tag) as pi:
                self._invoke_claude(step, preamble)
            elapsed = int(pi.elapsed)

            index = self._read_json(self._index_file)
            status = next((s.get("status", "pending") for s in index["steps"] if s["step"] == step_num), "pending")
            ts = self._stamp()

            if status == "completed":
                with progress_indicator(f"Step {step_num} 규칙 검토") as _:
                    violations = self._review_step(step)

                if violations and attempt < self.MAX_RETRIES:
                    for s in index["steps"]:
                        if s["step"] == step_num:
                            s["status"] = "pending"
                            s.pop("error_message", None)
                    self._write_json(self._index_file, index)
                    prev_error = violations
                    print(f"  ⚠ Step {step_num}: 규칙 위반 감지 — 재시도 {attempt}/{self.MAX_RETRIES}")
                    continue

                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["completed_at"] = ts
                self._write_json(self._index_file, index)
                self._write_step_log(step_num, step_name,
                                     "completed_with_violations" if violations else "completed",
                                     violations=violations)
                self._commit_step(step_num, step_name)
                warn = " ⚠ 위반사항 있음" if violations else ""
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]{warn}")
                return True

            if status == "blocked":
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["blocked_at"] = ts
                self._write_json(self._index_file, index)
                reason = next((s.get("blocked_reason", "") for s in index["steps"] if s["step"] == step_num), "")
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                self._update_top_index("blocked")
                sys.exit(2)

            err_msg = next(
                (s.get("error_message", "Step did not update status") for s in index["steps"] if s["step"] == step_num),
                "Step did not update status",
            )

            if attempt < self.MAX_RETRIES:
                # circuit breaker: 동일 에러 2회 연속 시 중단
                if err_msg == last_error:
                    same_error_count += 1
                else:
                    same_error_count = 1
                    last_error = err_msg

                if same_error_count >= 2:
                    for s in index["steps"]:
                        if s["step"] == step_num:
                            s["status"] = "error"
                            s["error_message"] = f"[circuit breaker] 동일 에러 반복: {err_msg}"
                            s["failed_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    print(f"  ✗ Step {step_num}: circuit breaker 발동 — 동일 에러 {same_error_count}회 반복")
                    print(f"    Error: {err_msg}")
                    self._update_top_index("error")
                    sys.exit(1)

                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "pending"
                        s.pop("error_message", None)
                self._write_json(self._index_file, index)
                prev_error = err_msg
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg}")
            else:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "error"
                        s["error_message"] = f"[{self.MAX_RETRIES}회 시도 후 실패] {err_msg}"
                        s["failed_at"] = ts
                self._write_json(self._index_file, index)
                self._write_step_log(step_num, step_name, "error", error=err_msg)
                self._commit_step(step_num, step_name)
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg}")
                self._update_top_index("error")
                sys.exit(1)

        return False  # unreachable

    def _execute_all_steps(self, guardrails: str):
        while True:
            index = self._read_json(self._index_file)
            pending = next((s for s in index["steps"] if s["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            for s in index["steps"]:
                if s["step"] == step_num and "started_at" not in s:
                    s["started_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    break

            self._execute_single_step(pending, guardrails)

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._update_top_index("completed")

        self._run_git("add", "-A")
        self._unstage_sensitive_files()
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._phase_name}): mark phase completed"
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  ✓ {msg}")

        if self._auto_push:
            branch = f"feat-{self._phase_name}"
            r = self._run_git("push", "-u", "origin", branch)
            if r.returncode != 0:
                print(f"\n  ERROR: git push 실패: {r.stderr.strip()}")
                sys.exit(1)
            print(f"  ✓ Pushed to origin/{branch}")

        print(f"\n{'='*60}")
        print(f"  Phase '{self._phase_name}' completed!")
        print(f"{'='*60}")

    def _move_plan_to_completed(self):
        plans_active = ROOT / "plans" / "active"
        plans_completed = ROOT / "plans" / "completed"
        plan_file = plans_active / f"{self._phase_dir_name}.md"

        if not plan_file.exists():
            return

        plans_completed.mkdir(parents=True, exist_ok=True)
        dest = plans_completed / f"{self._phase_dir_name}.md"
        plan_file.rename(dest)

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            self._run_git("commit", "-m", f"chore({self._phase_name}): move plan to completed")

        print(f"  ✓ Plan moved to plans/completed/{self._phase_dir_name}.md")

    def _evolve_rules(self):
        """이번 phase 위반 이력을 분석해 CLAUDE.md에 새 CRITICAL 규칙을 자동 추가한다."""
        log_dir = ROOT / "logs" / self._phase_dir_name
        if not log_dir.exists():
            return

        violations = []
        for log_file in sorted(log_dir.glob("*.json")):
            try:
                data = json.loads(log_file.read_text(encoding="utf-8"))
                if data.get("violations"):
                    violations.append(f"- step{data['step']} ({data['name']}): {data['violations']}")
            except Exception:
                continue

        if not violations:
            return

        claude_md = ROOT / "CLAUDE.md"
        if not claude_md.exists():
            return

        current_rules = claude_md.read_text(encoding="utf-8")

        prompt = (
            f"아래는 이번 phase에서 발생한 규칙 위반 목록이다:\n\n"
            + "\n".join(violations)
            + f"\n\n아래는 현재 프로젝트 규칙 파일(CLAUDE.md)이다:\n\n{current_rules}\n\n"
            f"위반 패턴을 분석해서 향후 동일한 실수가 반복되지 않도록 "
            f"CLAUDE.md의 '## CRITICAL 규칙' 섹션에 추가할 새 규칙을 제안하라.\n\n"
            f"조건:\n"
            f"- 이미 존재하는 규칙과 중복되면 제안하지 마라.\n"
            f"- '코드를 잘 작성하라' 같은 너무 일반적인 규칙은 제안하지 마라.\n"
            f"- 위반 패턴에서 직접 도출되는 구체적인 규칙만 제안하라.\n\n"
            f"새 규칙이 없으면 정확히 'NONE'만 출력하라.\n"
            f"새 규칙이 있으면 아래 형식으로만 출력하라 (설명 없이):\n"
            f"- CRITICAL: {{규칙 내용}}\n"
        )

        with progress_indicator("규칙 진화 분석") as _:
            result = subprocess.run(
                ["claude", "-p", "--dangerously-skip-permissions", prompt],
                cwd=self._root, capture_output=True, text=True, timeout=300,
            )

        output = result.stdout.strip()
        if not output or output.upper() == "NONE":
            print("  ✓ 추가할 새 규칙 없음")
            return

        new_rules = [
            line.strip() for line in output.splitlines()
            if line.strip().startswith("- CRITICAL:")
        ]
        if not new_rules:
            return

        lines = current_rules.splitlines()
        insert_idx = len(lines)
        in_critical = False
        for i, line in enumerate(lines):
            if line.strip() == "## CRITICAL 규칙":
                in_critical = True
            elif in_critical and line.startswith("##"):
                insert_idx = i
                break

        lines[insert_idx:insert_idx] = new_rules
        claude_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self._run_git("add", "CLAUDE.md")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            self._run_git("commit", "-m", f"chore({self._phase_name}): evolve harness rules")

        print(f"  ✓ CLAUDE.md에 {len(new_rules)}개 규칙 추가:")
        for rule in new_rules:
            print(f"    {rule}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. 0-mvp)")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    args = parser.parse_args()

    StepExecutor(args.phase_dir, auto_push=args.push).run()


if __name__ == "__main__":
    main()
