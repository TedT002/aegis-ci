"""Hata yolları ve uç durumlar: her savunma mekanizmasının gerçekten çalıştığını kanıtlar."""
import io
import os
import subprocess
import urllib.error

import pytest

from core import llm as llm_mod
from core.agents import PatcherAgent, TestSynthesizerAgent, VerificationAgent
from core.agents.verifier import summarize_failures
from core.orchestrator import AegisOrchestrator, main
from core.state import ExecutionState, TestSynthResult, TriageResult, ValidationResult
from core.ui import Console
from tests.conftest import FakeLLM
from tools import code_analyzer, prober, sandbox
from tools.code_analyzer import FunctionInfo, Param, default_return_literal, find_function
from tools.git_ops import GitOps
from tools.parser import LogParser


# ------------------------------------------------------------------ LLM transport
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_llm_http_transport(monkeypatch):
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", lambda req, timeout: _Resp(b'{"ok": 1}'))
    assert llm_mod.LLMClient._post("http://x", {}, {}) == {"ok": 1}

    def boom(req, timeout):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", boom)
    with pytest.raises(llm_mod.LLMError):
        llm_mod.LLMClient._post("http://x", {}, {})


def test_llm_providers(monkeypatch):
    g = llm_mod.GeminiClient("k", model="m")
    monkeypatch.setattr(g, "_post", lambda url, payload, headers: {
        "candidates": [{"content": {"parts": [{"text": '{"a": 2}'}]}}]})
    assert g.complete_json("s", "p") == {"a": 2}
    monkeypatch.setattr(g, "_post", lambda *a: {"error": "quota"})
    with pytest.raises(llm_mod.LLMError):
        g.complete("s", "p")

    o = llm_mod.OpenAIClient("k", base_url="http://local/v1/")
    assert o.base_url == "http://local/v1"
    monkeypatch.setattr(o, "_post", lambda *a: {"choices": [{"message": {"content": "{\"b\": 3}"}}]})
    assert o.complete_json("s", "p") == {"b": 3}
    monkeypatch.setattr(o, "_post", lambda *a: {})
    with pytest.raises(llm_mod.LLMError):
        o.complete("s", "p")
    with pytest.raises(llm_mod.LLMError):
        llm_mod.extract_json("{not json}")


# ------------------------------------------------------------------ tools edge cases
def test_git_ops_falls_back_to_patch(sample_repo, monkeypatch):
    real_run = subprocess.run

    def no_git(cmd, **kw):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 1, "", "git broken")
        return real_run(cmd, **kw)

    path = os.path.join(sample_repo, "math_service.py")
    lines = open(path).readlines()
    diff = GitOps.make_diff("math_service.py", lines, lines[:1] + ["# x\n"] + lines[1:])
    monkeypatch.setattr("tools.git_ops.subprocess.run", no_git)
    assert GitOps.check_diff(sample_repo, diff) == (True, "patch --dry-run OK.")
    assert GitOps.apply_diff(sample_repo, diff)[1] == "Patch applied successfully via patch command."

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr("tools.git_ops.subprocess.run", missing)
    ok, msg = GitOps.apply_diff(sample_repo, diff)
    assert not ok and "Required tool missing" in msg


def test_docker_available(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: None)
    assert sandbox.HybridSandbox.docker_available() is False
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    assert sandbox.HybridSandbox.docker_available() is True

    def boom(*a, **k):
        raise OSError("no daemon")
    monkeypatch.setattr(sandbox.subprocess, "run", boom)
    assert sandbox.HybridSandbox.docker_available() is False


def test_prober_edge_cases(tmp_path):
    method = FunctionInfo(name="m", lineno=1, end_lineno=2, is_method=True)
    assert prober.probe_function(str(tmp_path), "x.py", method) == []

    fn = FunctionInfo(name="f", lineno=1, end_lineno=2, params=[Param("a", "int")])
    (tmp_path / "broken.py").write_text("raise RuntimeError('import fails')\n")
    assert prober.probe_function(str(tmp_path), "broken.py", fn) == []
    (tmp_path / "silent.py").write_text("import sys\nsys.exit(0)\n")
    assert prober.probe_function(str(tmp_path), "silent.py", fn) == []


def test_parser_and_analyzer_fallbacks():
    res = LogParser.parse_traceback("worker crashed: raised ValueError: bad input")
    assert res["error_type"] == "ValueError" and res["error_message"] == "bad input"
    frames = [{"file": "tests/test_a.py", "line": 1, "function": "t"}]
    assert LogParser.innermost_app_frame(frames) == frames[0]
    assert default_return_literal("MyModel") == "None"
    assert summarize_failures("E   boom\nother") == ["E   boom"]


def test_console_helpers(capsys):
    con = Console()
    con.warn("careful")
    con.block("\n".join(str(i) for i in range(5)), max_lines=2)
    out = capsys.readouterr().out
    assert "careful" in out and "3 more lines" in out
    Console(quiet=True).ok("hidden")
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ agent guard rails
def _triage(**kw):
    base = dict(root_cause="rc", faulty_files=["math_service.py"], suspected_functions=["nope"],
                error_type="IndexError", faulty_line=None)
    base.update(kw)
    return TriageResult(**base)


def test_agents_reject_unknown_function(sample_repo):
    with pytest.raises(ValueError):
        TestSynthesizerAgent(sample_repo).run(_triage())
    st = ExecutionState(log_content="", repo_path=sample_repo, triage=_triage(),
                        synthesized_test=TestSynthResult(test_file_path="t.py", test_code=""))
    with pytest.raises(ValueError):
        PatcherAgent(sample_repo).run(st)


def test_patcher_candidates_by_error_type():
    fn = FunctionInfo(name="f", lineno=1, end_lineno=3,
                      params=[Param("a", "int"), Param("m", "Dict[str, int]"), Param("o", "")])
    assert PatcherAgent.candidate_conditions(fn, "ZeroDivisionError", [])[0] == "a == 0"
    assert PatcherAgent.candidate_conditions(fn, "KeyError", []) == ["not m"]
    assert PatcherAgent.candidate_conditions(fn, "TypeError", []) == ["a is None", "m is None", "o is None"]


def test_patcher_insertion_for_nested_line():
    src = "def f(xs: list) -> list:\n    for x in xs:\n        y = xs[9]\n    return xs\n"
    fn = find_function(src, name="f")
    idx, indent = PatcherAgent._insertion_line(src.splitlines(True), fn, faulty_line=3)
    assert (idx, indent) == (1, "    ")  # iç blok yerine fonksiyon gövdesinin başı


def test_llm_unchanged_function_falls_back(sample_repo, sample_log):
    from core.agents import TriageAgent
    st = ExecutionState(log_content=sample_log, repo_path=sample_repo)
    st.triage = TriageAgent(sample_repo).run(sample_log)
    st.synthesized_test = TestSynthesizerAgent(sample_repo).run(st.triage)
    src = open(os.path.join(sample_repo, "math_service.py")).read()
    fn = find_function(src, name="calculate_moving_average")
    original = "".join(src.splitlines(True)[fn.lineno - 1: fn.end_lineno])
    patch = PatcherAgent(sample_repo, FakeLLM([{"fixed_function": original}])).run(st)
    assert patch.source == "heuristic"


# ------------------------------------------------------------------ orchestrator branches
def test_orchestrator_llm_test_rejected_then_heuristic(sample_repo, sample_log, capsys):
    llm = FakeLLM([{}, {"test_code": "def test_x():\n    assert True\n"}])
    res = AegisOrchestrator(llm=llm).run(ExecutionState(log_content=sample_log, repo_path=sample_repo))
    assert res.verification_passed and res.synthesized_test.source == "heuristic"
    assert "falling back to deterministic synthesis" in capsys.readouterr().out


def test_orchestrator_refuses_when_not_reproduced(sample_repo, sample_log, monkeypatch):
    monkeypatch.setattr(VerificationAgent, "prove_reproduction",
                        lambda *a: ValidationResult(success=False, stage="reproduction", exit_code=0))
    res = AegisOrchestrator(console=Console(quiet=True)).run(
        ExecutionState(log_content=sample_log, repo_path=sample_repo))
    assert not res.verification_passed and res.current_patch is None


def test_orchestrator_stops_when_patcher_exhausted(sample_repo, sample_log, monkeypatch):
    def exhausted(self, state):
        raise ValueError("Patcher: all candidate strategies exhausted.")
    monkeypatch.setattr(PatcherAgent, "run", exhausted)
    assert main(["--log-path", os.path.join(sample_repo, "failure.log"), "--repo-path", sample_repo,
                 "--llm", "none", "--quiet"]) == 1


def test_orchestrator_apply_failure_is_reported(sample_repo, sample_log, monkeypatch, capsys):
    real_apply = GitOps.apply_diff

    def mock_apply(repo_dir, diff_text):
        if repo_dir == sample_repo:
            return False, "conflict"
        return real_apply(repo_dir, diff_text)

    monkeypatch.setattr("core.orchestrator.GitOps.apply_diff", mock_apply)
    res = AegisOrchestrator(apply=True).run(ExecutionState(log_content=sample_log, repo_path=sample_repo))
    assert res.verification_passed and "applied" not in res.artifacts
    assert "Could not apply patch" in capsys.readouterr().out
