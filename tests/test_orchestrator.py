import json
import os
import subprocess
import sys

from core.orchestrator import AegisOrchestrator, main
from core.state import ExecutionState
from core.ui import Console
from tests.conftest import FIXTURE_REPO, FakeLLM
from tools.sandbox import HybridSandbox


def test_full_loop(sample_repo, sample_log):
    orchestrator = AegisOrchestrator(HybridSandbox(), console=Console(quiet=True))
    state = ExecutionState(log_content=sample_log, repo_path=sample_repo, max_retries=3)
    res = orchestrator.run(state)

    assert res.verification_passed is True
    assert res.current_patch is not None
    assert res.reproduction.success
    assert len(res.iteration_history) == 2  # 1. yama reddedildi, geri besleme ile 2. doğrulandı
    assert not res.iteration_history[0].validation.success
    for name in ("patch.diff", "rca_report.md", "aegis_report.json"):
        assert os.path.isfile(os.path.join(sample_repo, name))
    report = open(os.path.join(sample_repo, "rca_report.md"), encoding="utf-8").read()
    assert "VERIFIED" in report and "FAILED as expected" in report
    data = json.load(open(os.path.join(sample_repo, "aegis_report.json")))
    assert data["verified"] and data["reproduction_proven"] and data["iterations"] == 2


def test_retry_budget_exhausted(sample_repo, sample_log):
    state = ExecutionState(log_content=sample_log, repo_path=sample_repo, max_retries=1)
    res = AegisOrchestrator(console=Console(quiet=True)).run(state)
    assert not res.verification_passed
    assert not os.path.exists(os.path.join(sample_repo, "patch.diff"))  # doğrulanmamış yama teslim edilmez
    assert "NOT FIXED" in open(os.path.join(sample_repo, "rca_report.md"), encoding="utf-8").read()


def test_cli_apply_heals_repo(sample_repo, tmp_path):
    out = tmp_path / "out"
    rc = main(["--log-path", os.path.join(FIXTURE_REPO, "failure.log"), "--repo-path", sample_repo,
               "--output-dir", str(out), "--apply", "--llm", "none", "--quiet"])
    assert rc == 0 and (out / "patch.diff").exists()
    assert os.path.exists(os.path.join(sample_repo, "tests", "test_aegis_reproduce.py"))
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                         cwd=sample_repo, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout  # iyileşmiş repo kendi CI'ından geçer


def test_unreproducible_bug_is_refused(sample_repo):
    log_path = os.path.join(sample_repo, "fake.log")
    with open(log_path, "w") as f:
        f.write('File "/ci/math_service.py", line 10, in calculate_moving_average\nKeyError: \'x\'\n')
    rc = main(["--log-path", log_path, "--repo-path", sample_repo, "--llm", "none", "--quiet"])
    assert rc == 2


def test_triage_failure_exit_code(sample_repo, monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    log_path = os.path.join(sample_repo, "junk.log")
    with open(log_path, "w") as f:
        f.write("everything is fine\n")
    assert main(["--log-path", log_path, "--repo-path", sample_repo, "--llm", "none", "--quiet"]) == 2
    assert main(["--log-path", log_path, "--repo-path", sample_repo, "--llm", "gemini", "--quiet"]) == 2


def test_loop_with_llm_backend(sample_repo, sample_log, capsys):
    """LLM'in ürettiği hatalı yama sandbox'ta reddedilir; geri besleme ile döngü yine kapanır."""
    bad_fix = "def calculate_moving_average(data, window_size):\n    return None\n"
    llm = FakeLLM([{"root_cause": "rc"}, LLMErr(), {"fixed_function": bad_fix}, LLMErr(), LLMErr()])
    state = ExecutionState(log_content=sample_log, repo_path=sample_repo)
    res = AegisOrchestrator(llm=llm).run(state)
    assert res.verification_passed
    assert res.iteration_history[0].strategy.startswith("llm") and not res.iteration_history[0].validation.success
    assert "fake (fake-1)" in capsys.readouterr().out


def LLMErr():
    from core.llm import LLMError
    return LLMError("unavailable")


def test_cli_entrypoint_subprocess(sample_repo):
    """README'deki gerçek komut satırı çağrısı."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run = subprocess.run(
        [sys.executable, "-m", "core.orchestrator", "--log-path", os.path.join(FIXTURE_REPO, "failure.log"),
         "--repo-path", sample_repo, "--llm", "none"],
        cwd=root, capture_output=True, text=True, env=dict(os.environ, NO_COLOR="1"),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "ROOT CAUSE ANALYSIS & VERIFICATION REPORT" in run.stdout
    assert "VERIFIED" in run.stdout
