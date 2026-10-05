import os

import pytest

from core.agents import PatcherAgent, TestSynthesizerAgent, TriageAgent, VerificationAgent
from core.llm import LLMError, build_llm, extract_json
from core.state import ExecutionState, TestSynthResult
from tests.conftest import FakeLLM
from tools.sandbox import HybridSandbox


def _state(repo, log, llm=None):
    st = ExecutionState(log_content=log, repo_path=repo)
    st.triage = TriageAgent(repo, llm).run(log)
    return st


def test_triage_contract(sample_repo, sample_log):
    t = TriageAgent(sample_repo).run(sample_log)
    assert t.faulty_files == ["math_service.py"]
    assert t.suspected_functions == ["calculate_moving_average"]
    assert t.error_type == "IndexError" and t.faulty_line == 10
    assert "range(window_size)" in t.faulty_code
    assert t.failing_tests == ["tests/test_math.py::test_empty"]


def test_triage_rejects_unmappable_log(sample_repo):
    with pytest.raises(ValueError):
        TriageAgent(sample_repo).run('File "/x/unknown.py", line 1, in f\nKeyError: 1')


def test_synth_produces_failing_then_passing_test(sample_repo, sample_log):
    st = _state(sample_repo, sample_log)
    synth = TestSynthesizerAgent(sample_repo).run(st.triage)
    compile(synth.test_code, synth.test_file_path, "exec")
    assert {"data": [], "window_size": 1} in synth.failing_inputs
    assert {"data": [1.0], "window_size": 2} in synth.failing_inputs  # çeşitlilik: boş olmayan vaka
    proof = VerificationAgent(HybridSandbox()).prove_reproduction(sample_repo, synth, st.triage)
    assert proof.success and proof.exit_code == 1


def test_patcher_candidates_and_feedback_loop(sample_repo, sample_log):
    st = _state(sample_repo, sample_log)
    st.synthesized_test = TestSynthesizerAgent(sample_repo).run(st.triage)
    patcher = PatcherAgent(sample_repo)
    first = patcher.run(st)
    assert first.strategy == "guard: if not data: return []"
    assert first.unified_diff.count("\n+") == 3  # +++ başlığı + 2 satır guard → minimal
    assert "# Kasıtlı Hata" in first.unified_diff  # yorum bloğu korunur

    from core.state import IterationRecord, ValidationResult
    st.iteration_history.append(IterationRecord(iteration=1, strategy=first.strategy, unified_diff=first.unified_diff,
                                                validation=ValidationResult(success=False, stage="test_execution")))
    second = patcher.run(st)
    assert second.strategy == "guard: if len(data) < window_size: return []"


def test_patcher_exhaustion(sample_repo, sample_log):
    from core.state import IterationRecord, ValidationResult
    st = _state(sample_repo, sample_log)
    st.synthesized_test = TestSynthResult(test_file_path="t.py", test_code="", failing_inputs=[])
    patcher = PatcherAgent(sample_repo)
    for _ in range(10):
        try:
            p = patcher.run(st)
        except ValueError as e:
            assert "exhausted" in str(e)
            break
        st.iteration_history.append(IterationRecord(iteration=1, strategy=p.strategy, unified_diff="",
                                                    validation=ValidationResult(success=False, stage="x")))
    else:
        pytest.fail("Patcher never exhausted candidates")


def test_zero_division_generalisation(zero_div_repo):
    repo, log = zero_div_repo
    st = _state(repo, log)
    assert st.triage.faulty_files == ["stats.py"] and st.triage.suspected_functions == ["mean"]
    st.synthesized_test = TestSynthesizerAgent(repo).run(st.triage)
    patch = PatcherAgent(repo).run(st)
    assert "if not values:" in patch.unified_diff and "return 0.0" in patch.unified_diff
    res = VerificationAgent(HybridSandbox()).verify(repo, patch, st.synthesized_test)
    assert res.success, res.stdout


# ------------------------------------------------------------------ LLM path
def test_llm_helpers(monkeypatch):
    assert extract_json('noise ```json\n{"a": 1}\n``` tail') == {"a": 1}
    with pytest.raises(LLMError):
        extract_json("no json")
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert build_llm("auto") is None and build_llm("none") is None
    with pytest.raises(LLMError):
        build_llm("gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert build_llm("auto").provider == "openai"
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    assert build_llm("auto").provider == "gemini"


def test_llm_triage_enrichment_keeps_facts(sample_repo, sample_log):
    llm = FakeLLM([{"root_cause": "Line 10 indexes data[i] for i < window_size even when len(data) < window_size."}])
    t = TriageAgent(sample_repo, llm).run(sample_log)
    assert t.root_cause.startswith("Line 10") and t.source == "heuristic+fake"
    assert t.faulty_line == 10  # olgular parser'dan
    t2 = TriageAgent(sample_repo, FakeLLM([LLMError("down")])).run(sample_log)
    assert t2.source == "heuristic"


def test_llm_patch_diff_is_computed_locally(sample_repo, sample_log):
    fixed = '''def calculate_moving_average(data: List[float], window_size: int) -> List[float]:
    """O(n) kayan pencere (sliding window) ile hareketli ortalama hesaplar."""
    if window_size <= 0 or len(data) < window_size:
        return []
    averages = []
    # Kasıtlı Hata: data boşken veya boyutu window_size'dan küçükken patlar
    window_sum = sum(data[i] for i in range(window_size))
    averages.append(window_sum / window_size)
    for i in range(window_size, len(data)):
        window_sum += data[i] - data[i - window_size]
        averages.append(window_sum / window_size)
    return averages
'''
    st = _state(sample_repo, sample_log)
    st.synthesized_test = TestSynthesizerAgent(sample_repo).run(st.triage)
    patch = PatcherAgent(sample_repo, FakeLLM([{"fixed_function": fixed}])).run(st)
    assert patch.source == "fake"
    assert "-    if window_size <= 0:" in patch.unified_diff
    assert VerificationAgent(HybridSandbox()).verify(sample_repo, patch, st.synthesized_test).success

    # Bozuk LLM çıktısı → deterministik moda düşer
    p2 = PatcherAgent(sample_repo, FakeLLM([{"fixed_function": "def broken(:"}])).run(st)
    assert p2.source == "heuristic"


def test_llm_synth_is_gated(sample_repo, sample_log):
    st = _state(sample_repo, sample_log)
    llm = FakeLLM([{"test_code": "def test_nothing():\n    assert True\n"}])
    synth = TestSynthesizerAgent(sample_repo, llm).run(st.triage)
    assert synth.source == "fake"
    proof = VerificationAgent(HybridSandbox()).prove_reproduction(sample_repo, synth, st.triage)
    assert not proof.success  # hayali test kanıt kapısından geçemez
    assert TestSynthesizerAgent(sample_repo, FakeLLM([{"test_code": "def x(:"}])).run(st.triage).source == "heuristic"
