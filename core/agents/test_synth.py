"""3.2 Test Synthesizer Agent — hatayı kanıtlayan minimal pytest testi."""
import os
from typing import Any, Dict, List, Optional

from core.llm import LLMClient, LLMError
from core.state import TestSynthResult, TriageResult
from tools.code_analyzer import find_function, is_simple_literal, module_name
from tools.prober import probe_function

TEST_FILE = "tests/test_aegis_reproduce.py"
MAX_CASES = 6

RETURN_TYPE_CHECKS = {
    "list": "list", "sequence": "list", "tuple": "tuple", "dict": "dict",
    "str": "str", "int": "int", "float": "(int, float)", "bool": "bool",
}


def _diverse(cases: List[Dict[str, Any]], limit: int = MAX_CASES) -> List[Dict[str, Any]]:
    """En küçük girdilerden başlayıp her parametre için yeni bir değer getiren vakaları seçer."""
    seen: Dict[str, set] = {}
    picked = []
    for case in sorted(cases, key=lambda c: (len(repr(c["kwargs"])), repr(c["kwargs"]))):
        novel = False
        for k, v in case["kwargs"].items():
            if repr(v) not in seen.setdefault(k, set()):
                novel = True
        if novel:
            for k, v in case["kwargs"].items():
                seen[k].add(repr(v))
            picked.append(case)
        if len(picked) >= limit:
            break
    return picked


class TestSynthesizerAgent:
    __test__ = False

    def __init__(self, repo_path: str, llm: Optional[LLMClient] = None):
        self.repo_path = repo_path
        self.llm = llm

    def run(self, triage: TriageResult, use_llm: bool = True) -> TestSynthResult:
        if self.llm and use_llm:
            try:
                return self._llm_synth(triage)
            except (LLMError, ValueError, SyntaxError, TypeError):
                pass
        return self._heuristic_synth(triage)

    # ------------------------------------------------------------------ heuristic
    def _heuristic_synth(self, triage: TriageResult) -> TestSynthResult:
        rel = triage.faulty_files[0]
        with open(os.path.join(self.repo_path, rel), encoding="utf-8") as f:
            source = f.read()
        func = find_function(source, name=(triage.suspected_functions or [None])[0], line=triage.faulty_line)
        if func is None:
            raise ValueError("Test synthesis failed: suspected function not found.")

        results = probe_function(self.repo_path, rel, func)
        failing = [r for r in results if not r["ok"] and r["error_type"] == triage.error_type]
        passing = [r for r in results if r["ok"] and not r.get("unserializable") and is_simple_literal(r["result"])]
        if not failing:
            raise ValueError(f"Test synthesis failed: no boundary input reproduces {triage.error_type}.")

        failing_sel = _diverse(failing)
        nontrivial = [r for r in passing if r["result"] not in ([], {}, (), "", None, 0)]
        passing_sel = _diverse(nontrivial or passing, limit=4)

        mod = module_name(rel)
        err = triage.error_type
        ret_key = func.returns.lower().replace("typing.", "").split("[")[0]
        type_check = RETURN_TYPE_CHECKS.get(ret_key)

        code = [
            f'"""Aegis-CI auto-generated reproduction test.',
            "",
            f"Root cause : {triage.root_cause}",
            f"Contract   : `{func.name}` must not raise {err} for boundary inputs and must",
            "             keep its existing behaviour for inputs that already worked.",
            '"""',
            "import pytest",
            "",
            f"from {mod} import {func.name}",
            "",
            f"# Inputs measured (by probing the original code) to raise {err}.",
            "FAILING_INPUTS = [",
            *[f"    {r['kwargs']!r}," for r in failing_sel],
            "]",
            "",
            "# Characterisation cases: outputs recorded from the original implementation.",
            "PRESERVED_BEHAVIOUR = [",
            *[f"    ({r['kwargs']!r}, {r['result']!r})," for r in passing_sel],
            "]",
            "",
            "",
            '@pytest.mark.parametrize("kwargs", FAILING_INPUTS)',
            f"def test_aegis_reproduces_{err.lower()}(kwargs):",
            "    try:",
            f"        result = {func.name}(**kwargs)",
            f"    except {err} as exc:",
            f'        pytest.fail(f"{func.name}(**{{kwargs}}) raised {err}: {{exc}}")',
        ]
        if type_check:
            code.append(f"    assert isinstance(result, {type_check})")
        if passing_sel:
            code += [
                "",
                "",
                '@pytest.mark.parametrize("kwargs, expected", PRESERVED_BEHAVIOUR)',
                "def test_aegis_preserves_behaviour(kwargs, expected):",
                f"    assert {func.name}(**kwargs) == expected",
            ]
        code.append("")
        return TestSynthResult(
            test_file_path=TEST_FILE, test_code="\n".join(code),
            failing_inputs=[r["kwargs"] for r in failing_sel], source="heuristic",
        )

    # ------------------------------------------------------------------ llm
    def _llm_synth(self, triage: TriageResult) -> TestSynthResult:
        rel = triage.faulty_files[0]
        with open(os.path.join(self.repo_path, rel), encoding="utf-8") as f:
            source = f.read()
        data = self.llm.complete_json(
            "You are a test engineer. Write a minimal pytest reproduction test that FAILS on the current "
            "code because of the reported bug and PASSES once it is fixed. Do not test unrelated behaviour.",
            f"Triage: {triage.model_dump_json()}\nImport path: `from {module_name(rel)} import ...`\n"
            f"Source of {rel}:\n```python\n{source}\n```\n"
            f'Return {{"test_file_path": "{TEST_FILE}", "test_code": "<python source>"}}',
        )
        result = TestSynthResult(**{**data, "test_file_path": TEST_FILE, "source": self.llm.provider})
        compile(result.test_code, TEST_FILE, "exec")  # sözdizimi kontrolü
        return result
