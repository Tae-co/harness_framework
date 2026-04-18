"""
execute.py 리팩터링 안전망 테스트.
리팩터링 전후 동작이 동일한지 검증한다.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import execute as ex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """phases/, CLAUDE.md, docs/ 를 갖춘 임시 프로젝트 구조."""
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()

    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Rules\n- rule one\n- rule two")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "arch.md").write_text("# Architecture\nSome content")
    (docs_dir / "guide.md").write_text("# Guide\nAnother doc")

    return tmp_path


@pytest.fixture
def phase_dir(tmp_project):
    """step 3개를 가진 phase 디렉토리."""
    d = tmp_project / "phases" / "0-mvp"
    d.mkdir()

    index = {
        "project": "TestProject",
        "phase": "mvp",
        "steps": [
            {"step": 0, "name": "setup", "status": "completed", "summary": "프로젝트 초기화 완료"},
            {"step": 1, "name": "core", "status": "completed", "summary": "핵심 로직 구현"},
            {"step": 2, "name": "ui", "status": "pending"},
        ],
    }
    (d / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    (d / "step2.md").write_text("# Step 2: UI\n\nUI를 구현하세요.")

    return d


@pytest.fixture
def top_index(tmp_project):
    """phases/index.json (top-level)."""
    top = {
        "phases": [
            {"dir": "0-mvp", "status": "pending"},
            {"dir": "1-polish", "status": "pending"},
        ]
    }
    p = tmp_project / "phases" / "index.json"
    p.write_text(json.dumps(top, indent=2))
    return p


@pytest.fixture
def executor(tmp_project, phase_dir):
    """테스트용 StepExecutor 인스턴스. git 호출은 별도 mock 필요."""
    with patch.object(ex, "ROOT", tmp_project):
        inst = ex.StepExecutor("0-mvp")
    # 내부 경로를 tmp_project 기준으로 재설정
    inst._root = str(tmp_project)
    inst._phases_dir = tmp_project / "phases"
    inst._phase_dir = phase_dir
    inst._phase_dir_name = "0-mvp"
    inst._index_file = phase_dir / "index.json"
    inst._top_index_file = tmp_project / "phases" / "index.json"
    return inst


# ---------------------------------------------------------------------------
# _stamp (= 이전 now_iso)
# ---------------------------------------------------------------------------

class TestStamp:
    def test_returns_kst_timestamp(self, executor):
        result = executor._stamp()
        assert "+0900" in result

    def test_format_is_iso(self, executor):
        result = executor._stamp()
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert dt.tzinfo is not None

    def test_is_current_time(self, executor):
        before = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0)
        result = executor._stamp()
        after = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0) + timedelta(seconds=1)
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert before <= parsed <= after


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------

class TestJsonHelpers:
    def test_roundtrip(self, tmp_path):
        data = {"key": "값", "nested": [1, 2, 3]}
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, data)
        loaded = ex.StepExecutor._read_json(p)
        assert loaded == data

    def test_save_ensures_ascii_false(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"한글": "테스트"})
        raw = p.read_text()
        assert "한글" in raw
        assert "\\u" not in raw

    def test_save_indented(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"a": 1})
        raw = p.read_text()
        assert "\n" in raw

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ex.StepExecutor._read_json(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# _load_guardrails
# ---------------------------------------------------------------------------

class TestLoadGuardrails:
    def test_loads_claude_md_and_docs(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "# Rules" in result
        assert "rule one" in result
        assert "# Architecture" in result
        assert "# Guide" in result

    def test_sections_separated_by_divider(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "---" in result

    def test_docs_sorted_alphabetically(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        arch_pos = result.index("arch")
        guide_pos = result.index("guide")
        assert arch_pos < guide_pos

    def test_no_claude_md(self, executor, tmp_project):
        (tmp_project / "CLAUDE.md").unlink()
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "CLAUDE.md" not in result
        assert "Architecture" in result

    def test_no_docs_dir(self, executor, tmp_project):
        import shutil
        shutil.rmtree(tmp_project / "docs")
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_guardrails()
        assert "Rules" in result
        assert "Architecture" not in result

    def test_empty_project(self, tmp_path):
        with patch.object(ex, "ROOT", tmp_path):
            # executor가 필요 없는 static-like 동작이므로 임시 인스턴스
            phases_dir = tmp_path / "phases" / "dummy"
            phases_dir.mkdir(parents=True)
            idx = {"project": "T", "phase": "t", "steps": []}
            (phases_dir / "index.json").write_text(json.dumps(idx))
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
            result = inst._load_guardrails()
        assert result == ""


# ---------------------------------------------------------------------------
# _build_step_context
# ---------------------------------------------------------------------------

class TestBuildStepContext:
    def test_includes_completed_with_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "Step 0 (setup): 프로젝트 초기화 완료" in result
        assert "Step 1 (core): 핵심 로직 구현" in result

    def test_excludes_pending(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "ui" not in result

    def test_excludes_completed_without_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        del index["steps"][0]["summary"]
        result = ex.StepExecutor._build_step_context(index)
        assert "setup" not in result
        assert "core" in result

    def test_empty_when_no_completed(self):
        index = {"steps": [{"step": 0, "name": "a", "status": "pending"}]}
        result = ex.StepExecutor._build_step_context(index)
        assert result == ""

    def test_has_header(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert result.startswith("## 이전 Step 산출물")


# ---------------------------------------------------------------------------
# _build_preamble
# ---------------------------------------------------------------------------

class TestBuildPreamble:
    def test_includes_project_name(self, executor):
        result = executor._build_preamble("", "")
        assert "TestProject" in result

    def test_includes_guardrails(self, executor):
        result = executor._build_preamble("GUARD_CONTENT", "")
        assert "GUARD_CONTENT" in result

    def test_includes_step_context(self, executor):
        ctx = "## 이전 Step 산출물\n\n- Step 0: done"
        result = executor._build_preamble("", ctx)
        assert "이전 Step 산출물" in result

    def test_includes_commit_example(self, executor):
        result = executor._build_preamble("", "")
        assert "feat(mvp):" in result

    def test_includes_rules(self, executor):
        result = executor._build_preamble("", "")
        assert "작업 규칙" in result
        assert "AC" in result

    def test_no_retry_section_by_default(self, executor):
        result = executor._build_preamble("", "")
        assert "이전 시도 실패" not in result

    def test_retry_section_with_prev_error(self, executor):
        result = executor._build_preamble("", "", prev_error="타입 에러 발생")
        assert "이전 시도 실패" in result
        assert "타입 에러 발생" in result

    def test_includes_max_retries(self, executor):
        result = executor._build_preamble("", "")
        assert str(ex.StepExecutor.MAX_RETRIES) in result

    def test_includes_index_path(self, executor):
        result = executor._build_preamble("", "")
        assert "/phases/0-mvp/index.json" in result


# ---------------------------------------------------------------------------
# _update_top_index
# ---------------------------------------------------------------------------

class TestUpdateTopIndex:
    def test_completed(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "completed"
        assert "completed_at" in mvp

    def test_error(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("error")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "error"
        assert "failed_at" in mvp

    def test_blocked(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("blocked")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "blocked"
        assert "blocked_at" in mvp

    def test_other_phases_unchanged(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        polish = next(p for p in data["phases"] if p["dir"] == "1-polish")
        assert polish["status"] == "pending"

    def test_nonexistent_dir_is_noop(self, executor, top_index):
        executor._top_index_file = top_index
        executor._phase_dir_name = "no-such-dir"
        original = json.loads(top_index.read_text())
        executor._update_top_index("completed")
        after = json.loads(top_index.read_text())
        for p_before, p_after in zip(original["phases"], after["phases"]):
            assert p_before["status"] == p_after["status"]

    def test_no_top_index_file(self, executor, tmp_path):
        executor._top_index_file = tmp_path / "nonexistent.json"
        executor._update_top_index("completed")  # should not raise


# ---------------------------------------------------------------------------
# _checkout_branch (mocked)
# ---------------------------------------------------------------------------

class TestCheckoutBranch:
    def _mock_git(self, executor, responses):
        call_idx = {"i": 0}
        def fake_git(*args):
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx < len(responses):
                return responses[idx]
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

    def test_already_on_branch(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="feat-mvp\n", stderr=""),
        ])
        executor._checkout_branch()  # should return without checkout

    def test_branch_exists_checkout(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_branch_not_exists_create(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="not found"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_checkout_fails_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="dirty tree"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1

    def test_no_git_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=1, stdout="", stderr="not a git repo"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _commit_step (mocked)
# ---------------------------------------------------------------------------

class TestCommitStep:
    def test_two_phase_commit(self, executor):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 2
        assert "feat(mvp):" in commit_calls[0][2]
        assert "chore(mvp):" in commit_calls[1][2]

    def test_no_code_changes_skips_feat_commit(self, executor):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached") and "--quiet" in args:
                # 첫 번째 --quiet 체크(feat): 변경 없음(0=no diff), 두 번째(chore): 변경 있음(1=has diff)
                quiet_calls = [c for c in calls if c[:2] == ("diff", "--cached") and "--quiet" in c]
                return MagicMock(returncode=0 if len(quiet_calls) == 1 else 1)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        commit_msgs = [c[2] for c in calls if c[0] == "commit"]
        assert len(commit_msgs) == 1
        assert "chore" in commit_msgs[0]


# ---------------------------------------------------------------------------
# _invoke_claude (mocked)
# ---------------------------------------------------------------------------

class TestInvokeClaude:
    def test_invokes_claude_with_correct_args(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"result": "ok"}', stderr="")
        step = {"step": 2, "name": "ui"}
        preamble = "PREAMBLE\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            output = executor._invoke_claude(step, preamble)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--output-format" in cmd
        assert "PREAMBLE" in cmd[-1]
        assert "UI를 구현하세요" in cmd[-1]

    def test_saves_output_json(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result):
            executor._invoke_claude(step, "preamble")

        output_file = executor._phase_dir / "step2-output.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["step"] == 2
        assert data["name"] == "ui"
        assert data["exitCode"] == 0

    def test_nonexistent_step_file_exits(self, executor):
        step = {"step": 99, "name": "nonexistent"}
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_claude(step, "preamble")
        assert exc_info.value.code == 1

    def test_timeout_is_1800(self, executor):
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_claude(step, "preamble")

        assert mock_run.call_args[1]["timeout"] == 1800


# ---------------------------------------------------------------------------
# progress_indicator (= 이전 Spinner)
# ---------------------------------------------------------------------------

class TestProgressIndicator:
    def test_context_manager(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.15)
        assert pi.elapsed >= 0.1

    def test_elapsed_increases(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.2)
        assert pi.elapsed > 0


# ---------------------------------------------------------------------------
# main() CLI 파싱 (mocked)
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_no_args_exits(self):
        with patch("sys.argv", ["execute.py"]):
            with pytest.raises(SystemExit) as exc_info:
                ex.main()
            assert exc_info.value.code == 2  # argparse exits with 2

    def test_invalid_phase_dir_exits(self):
        with patch("sys.argv", ["execute.py", "nonexistent"]):
            with patch.object(ex, "ROOT", Path("/tmp/fake_nonexistent")):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1

    def test_missing_index_exits(self, tmp_project):
        (tmp_project / "phases" / "empty").mkdir()
        with patch("sys.argv", ["execute.py", "empty"]):
            with patch.object(ex, "ROOT", tmp_project):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_blockers (= 이전 main() error/blocked 체크)
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def _make_executor_with_steps(self, tmp_project, steps):
        d = tmp_project / "phases" / "test-phase"
        d.mkdir(exist_ok=True)
        index = {"project": "T", "phase": "test", "steps": steps}
        (d / "index.json").write_text(json.dumps(index))

        with patch.object(ex, "ROOT", tmp_project):
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_project)
        inst._phases_dir = tmp_project / "phases"
        inst._phase_dir = d
        inst._phase_dir_name = "test-phase"
        inst._index_file = d / "index.json"
        inst._top_index_file = tmp_project / "phases" / "index.json"
        inst._phase_name = "test"
        inst._total = len(steps)
        return inst

    def test_error_step_exits_1(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "bad", "status": "error", "error_message": "fail"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    def test_blocked_step_exits_2(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "stuck", "status": "blocked", "blocked_reason": "API key"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _review_step
# ---------------------------------------------------------------------------

class TestReviewStep:
    def test_ok_output_returns_none(self, executor):
        mock_result = MagicMock(returncode=0, stdout="OK", stderr="")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("subprocess.run", return_value=mock_result):
            result = executor._review_step({"step": 2, "name": "ui"})

        assert result is None

    def test_violation_returns_formatted_string(self, executor):
        mock_result = MagicMock(returncode=0, stdout="테스트 없이 기능 추가됨", stderr="")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("subprocess.run", return_value=mock_result):
            result = executor._review_step({"step": 2, "name": "ui"})

        assert result is not None
        assert "[규칙 위반 감지]" in result
        assert "테스트 없이 기능 추가됨" in result

    def test_empty_output_returns_none(self, executor):
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("subprocess.run", return_value=mock_result):
            result = executor._review_step({"step": 2, "name": "ui"})

        assert result is None

    def test_missing_step_file_returns_none(self, executor):
        result = executor._review_step({"step": 99, "name": "nonexistent"})
        assert result is None

    def test_prompt_includes_step_content(self, executor):
        mock_result = MagicMock(returncode=0, stdout="OK", stderr="")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._review_step({"step": 2, "name": "ui"})

        prompt = mock_run.call_args[0][0][-1]
        assert "UI를 구현하세요" in prompt

    def test_prompt_includes_changed_files(self, executor):
        mock_result = MagicMock(returncode=0, stdout="OK", stderr="")
        git_responses = [
            MagicMock(returncode=0, stdout="src/Foo.java\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        executor._run_git = MagicMock(side_effect=git_responses)

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._review_step({"step": 2, "name": "ui"})

        prompt = mock_run.call_args[0][0][-1]
        assert "src/Foo.java" in prompt

    def test_timeout_is_300(self, executor):
        mock_result = MagicMock(returncode=0, stdout="OK", stderr="")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._review_step({"step": 2, "name": "ui"})

        assert mock_run.call_args[1]["timeout"] == 300


# ---------------------------------------------------------------------------
# _write_step_log
# ---------------------------------------------------------------------------

class TestWriteStepLog:
    def test_creates_log_file(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            executor._write_step_log(2, "ui", "completed")
        assert (tmp_project / "logs" / "0-mvp" / "step2.json").exists()

    def test_log_contains_required_fields(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            executor._write_step_log(2, "ui", "completed")
        data = json.loads((tmp_project / "logs" / "0-mvp" / "step2.json").read_text())
        assert data["phase"] == "0-mvp"
        assert data["step"] == 2
        assert data["name"] == "ui"
        assert data["status"] == "completed"
        assert "timestamp" in data

    def test_violations_included_when_provided(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            executor._write_step_log(2, "ui", "completed_with_violations", violations="테스트 파일 없음")
        data = json.loads((tmp_project / "logs" / "0-mvp" / "step2.json").read_text())
        assert data["violations"] == "테스트 파일 없음"

    def test_error_included_when_provided(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            executor._write_step_log(2, "ui", "error", error="빌드 실패")
        data = json.loads((tmp_project / "logs" / "0-mvp" / "step2.json").read_text())
        assert data["error"] == "빌드 실패"

    def test_no_extra_keys_when_optional_args_absent(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            executor._write_step_log(2, "ui", "completed")
        data = json.loads((tmp_project / "logs" / "0-mvp" / "step2.json").read_text())
        assert "violations" not in data
        assert "error" not in data

    def test_creates_parent_directories(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            executor._write_step_log(2, "ui", "completed")
        assert (tmp_project / "logs" / "0-mvp").is_dir()


# ---------------------------------------------------------------------------
# _load_recent_violations
# ---------------------------------------------------------------------------

class TestLoadRecentViolations:
    def test_returns_empty_when_no_logs_dir(self, executor, tmp_project):
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_recent_violations()
        assert result == ""

    def test_returns_empty_when_no_violations(self, executor, tmp_project):
        log_dir = tmp_project / "logs" / "0-mvp"
        log_dir.mkdir(parents=True)
        entry = {"timestamp": "2026-04-01T10:00:00+0900", "phase": "0-mvp",
                 "step": 0, "name": "setup", "status": "completed"}
        (log_dir / "step0.json").write_text(json.dumps(entry))
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_recent_violations()
        assert result == ""

    def test_returns_violations_when_present(self, executor, tmp_project):
        log_dir = tmp_project / "logs" / "0-mvp"
        log_dir.mkdir(parents=True)
        entry = {"timestamp": "2026-04-01T10:00:00+0900", "phase": "0-mvp",
                 "step": 0, "name": "setup", "status": "completed_with_violations",
                 "violations": "테스트 파일 없음"}
        (log_dir / "step0.json").write_text(json.dumps(entry))
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_recent_violations()
        assert "테스트 파일 없음" in result
        assert "과거 규칙 위반 이력" in result

    def test_limits_to_last_10(self, executor, tmp_project):
        log_dir = tmp_project / "logs" / "test-phase"
        log_dir.mkdir(parents=True)
        for i in range(15):
            entry = {"timestamp": f"2026-04-{i+1:02d}T10:00:00+0900",
                     "phase": "test-phase", "step": i, "name": f"s{i}",
                     "status": "completed_with_violations", "violations": f"위반 {i}"}
            (log_dir / f"step{i}.json").write_text(json.dumps(entry))
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_recent_violations()
        lines = [l for l in result.split("\n") if l.startswith("- ")]
        assert len(lines) == 10

    def test_skips_malformed_log_files(self, executor, tmp_project):
        log_dir = tmp_project / "logs" / "0-mvp"
        log_dir.mkdir(parents=True)
        (log_dir / "step0.json").write_text("not valid json{{{")
        with patch.object(ex, "ROOT", tmp_project):
            result = executor._load_recent_violations()
        assert result == ""


# ---------------------------------------------------------------------------
# _move_plan_to_completed
# ---------------------------------------------------------------------------

class TestMovePlanToCompleted:
    def _setup_plan(self, tmp_project, content="# Plan"):
        active = tmp_project / "plans" / "active"
        active.mkdir(parents=True)
        completed = tmp_project / "plans" / "completed"
        completed.mkdir(parents=True)
        plan_file = active / "0-mvp.md"
        plan_file.write_text(content)
        return plan_file

    def test_moves_plan_to_completed(self, executor, tmp_project):
        plan_file = self._setup_plan(tmp_project)
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        with patch.object(ex, "ROOT", tmp_project):
            executor._move_plan_to_completed()
        assert not plan_file.exists()
        assert (tmp_project / "plans" / "completed" / "0-mvp.md").exists()

    def test_preserves_plan_content(self, executor, tmp_project):
        self._setup_plan(tmp_project, content="# Plan: my feature\n\n## 목표\n테스트")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        with patch.object(ex, "ROOT", tmp_project):
            executor._move_plan_to_completed()
        content = (tmp_project / "plans" / "completed" / "0-mvp.md").read_text()
        assert "테스트" in content

    def test_noop_when_plan_not_found(self, executor, tmp_project):
        (tmp_project / "plans" / "active").mkdir(parents=True)
        git_mock = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        executor._run_git = git_mock
        with patch.object(ex, "ROOT", tmp_project):
            executor._move_plan_to_completed()
        git_mock.assert_not_called()

    def test_commits_after_move(self, executor, tmp_project):
        self._setup_plan(tmp_project)
        git_calls = []
        def fake_git(*args):
            git_calls.append(args)
            if args[0] == "diff":
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        with patch.object(ex, "ROOT", tmp_project):
            executor._move_plan_to_completed()
        commit_calls = [c for c in git_calls if c[0] == "commit"]
        assert len(commit_calls) == 1
        assert "completed" in commit_calls[0][2]

    def test_no_commit_when_nothing_staged(self, executor, tmp_project):
        self._setup_plan(tmp_project)
        git_calls = []
        def fake_git(*args):
            git_calls.append(args)
            if args[0] == "diff":
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        with patch.object(ex, "ROOT", tmp_project):
            executor._move_plan_to_completed()
        commit_calls = [c for c in git_calls if c[0] == "commit"]
        assert len(commit_calls) == 0


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def _make_executor(self, tmp_project):
        d = tmp_project / "phases" / "test-cb"
        d.mkdir(exist_ok=True)
        index = {"project": "T", "phase": "test",
                 "steps": [{"step": 0, "name": "setup", "status": "pending"}]}
        (d / "index.json").write_text(json.dumps(index))
        (d / "step0.md").write_text("# Step 0\n\n작업하세요.")

        inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_project)
        inst._phases_dir = tmp_project / "phases"
        inst._phase_dir = d
        inst._phase_dir_name = "test-cb"
        inst._index_file = d / "index.json"
        inst._top_index_file = tmp_project / "phases" / "index.json"
        inst._phase_name = "test"
        inst._total = 1
        inst._project = "T"
        inst._auto_push = False
        inst._write_step_log = MagicMock()
        inst._commit_step = MagicMock()
        inst._update_top_index = MagicMock()
        return inst

    def _make_error_invoker(self, inst, error_message):
        def fake_invoke(step, preamble):
            index = json.loads(inst._index_file.read_text())
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "error"
                    s["error_message"] = error_message
            inst._index_file.write_text(json.dumps(index))
            return {"step": 0, "name": "setup", "exitCode": 0, "stdout": "", "stderr": ""}
        return fake_invoke

    def test_same_error_twice_triggers_circuit_breaker(self, tmp_project):
        inst = self._make_executor(tmp_project)
        call_count = {"n": 0}

        def fake_invoke(step, preamble):
            call_count["n"] += 1
            index = json.loads(inst._index_file.read_text())
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "error"
                    s["error_message"] = "동일한 에러"
            inst._index_file.write_text(json.dumps(index))
            return {"step": 0, "name": "setup", "exitCode": 0, "stdout": "", "stderr": ""}

        inst._invoke_claude = fake_invoke
        with pytest.raises(SystemExit) as exc_info:
            inst._execute_single_step({"step": 0, "name": "setup", "status": "pending"}, "")
        assert exc_info.value.code == 1
        assert call_count["n"] == 2

    def test_circuit_breaker_writes_error_message(self, tmp_project):
        inst = self._make_executor(tmp_project)
        inst._invoke_claude = self._make_error_invoker(inst, "반복 에러")
        with pytest.raises(SystemExit):
            inst._execute_single_step({"step": 0, "name": "setup", "status": "pending"}, "")
        data = json.loads(inst._index_file.read_text())
        step = data["steps"][0]
        assert step["status"] == "error"
        assert "circuit breaker" in step["error_message"]

    def test_different_errors_exhaust_all_retries(self, tmp_project):
        inst = self._make_executor(tmp_project)
        errors = ["에러 A", "에러 B", "에러 C"]
        call_count = {"n": 0}

        def fake_invoke(step, preamble):
            n = call_count["n"]
            call_count["n"] += 1
            index = json.loads(inst._index_file.read_text())
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "error"
                    s["error_message"] = errors[min(n, len(errors) - 1)]
            inst._index_file.write_text(json.dumps(index))
            return {"step": 0, "name": "setup", "exitCode": 0, "stdout": "", "stderr": ""}

        inst._invoke_claude = fake_invoke
        with pytest.raises(SystemExit) as exc_info:
            inst._execute_single_step({"step": 0, "name": "setup", "status": "pending"}, "")
        assert exc_info.value.code == 1
        assert call_count["n"] == ex.StepExecutor.MAX_RETRIES


# ---------------------------------------------------------------------------
# _unstage_sensitive_files
# ---------------------------------------------------------------------------

class TestUnstageSensitiveFiles:
    def test_unstages_env_file(self, executor):
        reset_calls = []
        def fake_git(*args):
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=0, stdout=".env\nsrc/main.py\n", stderr="")
            reset_calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._unstage_sensitive_files()
        assert any(".env" in str(c) for c in reset_calls)

    def test_does_not_unstage_normal_files(self, executor):
        reset_calls = []
        def fake_git(*args):
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=0, stdout="src/main.py\nREADME.md\n", stderr="")
            reset_calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._unstage_sensitive_files()
        assert len(reset_calls) == 0

    def test_unstages_pem_and_key_files(self, executor):
        reset_calls = []
        def fake_git(*args):
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=0, stdout="server.pem\napi.key\n", stderr="")
            reset_calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._unstage_sensitive_files()
        unstaged = [c for c in reset_calls if c[0] == "reset"]
        assert len(unstaged) == 2


# ---------------------------------------------------------------------------
# _evolve_rules
# ---------------------------------------------------------------------------

class TestEvolveRules:
    def _setup(self, tmp_project, violations=None):
        log_dir = tmp_project / "logs" / "0-mvp"
        log_dir.mkdir(parents=True)
        if violations:
            for i, v in enumerate(violations):
                entry = {
                    "timestamp": f"2026-04-{i+1:02d}T10:00:00+0900",
                    "phase": "0-mvp", "step": i, "name": f"step{i}",
                    "status": "completed_with_violations", "violations": v,
                }
                (log_dir / f"step{i}.json").write_text(json.dumps(entry))

        claude_md = tmp_project / "CLAUDE.md"
        claude_md.write_text(
            "# 프로젝트\n\n## CRITICAL 규칙\n- CRITICAL: 기존 규칙\n\n## 명령어\nnpm test\n"
        )
        return claude_md

    def test_noop_when_no_log_dir(self, executor, tmp_project):
        claude_md = tmp_project / "CLAUDE.md"
        claude_md.write_text("# 프로젝트\n\n## CRITICAL 규칙\n- CRITICAL: 기존 규칙\n")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        with patch.object(ex, "ROOT", tmp_project):
            with patch("subprocess.run") as mock_run:
                executor._evolve_rules()
        mock_run.assert_not_called()

    def test_noop_when_no_violations_in_logs(self, executor, tmp_project):
        log_dir = tmp_project / "logs" / "0-mvp"
        log_dir.mkdir(parents=True)
        entry = {"timestamp": "2026-04-01T10:00:00+0900", "phase": "0-mvp",
                 "step": 0, "name": "setup", "status": "completed"}
        (log_dir / "step0.json").write_text(json.dumps(entry))
        (tmp_project / "CLAUDE.md").write_text("# 규칙\n")
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        with patch.object(ex, "ROOT", tmp_project):
            with patch("subprocess.run") as mock_run:
                executor._evolve_rules()
        mock_run.assert_not_called()

    def test_noop_when_claude_returns_none(self, executor, tmp_project):
        claude_md = self._setup(tmp_project, violations=["테스트 없이 기능 추가"])
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        mock_result = MagicMock(returncode=0, stdout="NONE", stderr="")
        with patch.object(ex, "ROOT", tmp_project):
            with patch("subprocess.run", return_value=mock_result):
                executor._evolve_rules()
        assert claude_md.read_text().count("- CRITICAL:") == 1

    def test_adds_new_rules_to_critical_section(self, executor, tmp_project):
        claude_md = self._setup(tmp_project, violations=["테스트 없이 기능 추가"])
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        new_rule = "- CRITICAL: 새 기능 추가 시 반드시 테스트를 먼저 작성하라"
        mock_result = MagicMock(returncode=0, stdout=new_rule, stderr="")
        with patch.object(ex, "ROOT", tmp_project):
            with patch("subprocess.run", return_value=mock_result):
                executor._evolve_rules()
        content = claude_md.read_text()
        assert "새 기능 추가 시 반드시 테스트를 먼저 작성하라" in content

    def test_new_rules_inserted_inside_critical_section(self, executor, tmp_project):
        claude_md = self._setup(tmp_project, violations=["아키텍처 위반"])
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        new_rule = "- CRITICAL: 서비스 레이어에서 뷰를 임포트하지 마라"
        mock_result = MagicMock(returncode=0, stdout=new_rule, stderr="")
        with patch.object(ex, "ROOT", tmp_project):
            with patch("subprocess.run", return_value=mock_result):
                executor._evolve_rules()
        content = claude_md.read_text()
        critical_idx = content.index("## CRITICAL 규칙")
        commands_idx = content.index("## 명령어")
        new_rule_idx = content.index("서비스 레이어에서")
        assert critical_idx < new_rule_idx < commands_idx

    def test_commits_when_rules_added(self, executor, tmp_project):
        self._setup(tmp_project, violations=["위반 발생"])
        git_calls = []
        def fake_git(*args):
            git_calls.append(args)
            if args[0] == "diff":
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        mock_result = MagicMock(returncode=0, stdout="- CRITICAL: 새 규칙", stderr="")
        with patch.object(ex, "ROOT", tmp_project):
            with patch("subprocess.run", return_value=mock_result):
                executor._evolve_rules()
        commit_calls = [c for c in git_calls if c[0] == "commit"]
        assert len(commit_calls) == 1
        assert "evolve" in commit_calls[0][2]

    def test_ignores_non_critical_lines_in_output(self, executor, tmp_project):
        claude_md = self._setup(tmp_project, violations=["위반"])
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        noisy_output = "분석 결과:\n- CRITICAL: 실제 규칙\n설명 텍스트"
        mock_result = MagicMock(returncode=0, stdout=noisy_output, stderr="")
        with patch.object(ex, "ROOT", tmp_project):
            with patch("subprocess.run", return_value=mock_result):
                executor._evolve_rules()
        content = claude_md.read_text()
        assert "실제 규칙" in content
        assert "설명 텍스트" not in content
