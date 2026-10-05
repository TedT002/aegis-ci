"""3.3 Patcher Agent — minimal, hedefli Unified Diff üretimi.

Deterministik mod "guard clause synthesis" uygular: hata tipine ve fonksiyon
imzasına göre aday koşullar üretir (basitten karmaşığa), her iterasyonda bir
sonrakini dener. Sandbox geri bildirimi reddedilen adayları eler.

LLM modu fonksiyonun düzeltilmiş halini ister; diff'i LLM'e yazdırmaz,
`difflib` ile kendisi üretir. Böylece bozuk/uygulanamaz diff riski sıfırlanır.
"""
import os
import re
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from core.llm import LLMClient, LLMError
from core.state import ExecutionState, IterationRecord, PatchResult
from tools.code_analyzer import FunctionInfo, default_return_literal, find_function
from tools.git_ops import GitOps

SEQ_ERRORS = {"IndexError", "StopIteration", "ValueError", "ZeroDivisionError"}


class PatcherAgent:
    def __init__(self, repo_path: str, llm: Optional[LLMClient] = None):
        self.repo_path = repo_path
        self.llm = llm

    def run(self, state: ExecutionState) -> PatchResult:
        rel = state.triage.faulty_files[0]
        with open(os.path.join(self.repo_path, rel), encoding="utf-8") as f:
            source = f.read()
        func = find_function(source, name=(state.triage.suspected_functions or [None])[0], line=state.triage.faulty_line)
        if func is None:
            raise ValueError("Patcher: target function not found.")

        tried = {rec.strategy for rec in state.iteration_history}
        if self.llm:
            try:
                return self._llm_patch(state, rel, source, func)
            except (LLMError, ValueError, KeyError, SyntaxError, TypeError):
                pass

        for cond in self.candidate_conditions(func, state.triage.error_type, state.synthesized_test.failing_inputs):
            strategy = f"guard: if {cond}: return {default_return_literal(func.returns)}"
            if strategy in tried:
                continue
            diff = self._guard_diff(rel, source, func, cond, state.triage.faulty_line)
            return PatchResult(target_file=rel, unified_diff=diff, strategy=strategy, source="heuristic")
        raise ValueError("Patcher: all candidate strategies exhausted.")

    # ------------------------------------------------------------------ candidates
    @staticmethod
    def candidate_conditions(func: FunctionInfo, error_type: str, failing_inputs: List[Dict[str, Any]]) -> List[str]:
        def observed(name: str, typ: tuple) -> bool:
            return any(isinstance(fi.get(name), typ) and not isinstance(fi.get(name), bool) for fi in failing_inputs)

        seqs = [p.name for p in func.params if p.kind == "seq" or observed(p.name, (list, tuple, str))]
        nums = [p.name for p in func.params if p.kind in ("int", "float") or observed(p.name, (int, float))]
        nums = [n for n in nums if n not in seqs]
        maps = [p.name for p in func.params if p.kind == "map"]
        anys = [p.name for p in func.params]

        conds: List[str] = []
        if error_type == "ZeroDivisionError":
            conds += [f"{n} == 0" for n in nums]
        if error_type in SEQ_ERRORS:
            conds += [f"not {s}" for s in seqs]
            conds += [f"len({s}) < {n}" for s, n in product(seqs, nums)]
            conds += [f"len({s}) <= {n}" for s, n in product(seqs, nums)]
        if error_type == "KeyError":
            conds += [f"not {m}" for m in maps]
        if error_type in ("TypeError", "AttributeError"):
            conds += [f"{a} is None" for a in anys]
        conds += [f"not {s}" for s in seqs if f"not {s}" not in conds]
        return list(dict.fromkeys(conds))

    @staticmethod
    def _insertion_line(lines: List[str], func: FunctionInfo, faulty_line: Optional[int]) -> Tuple[int, str]:
        """Guard'ın ekleneceği (0-tabanlı) indeks ve girintisi."""
        if faulty_line and func.contains(faulty_line):
            target = lines[faulty_line - 1]
            indent = target[: len(target) - len(target.lstrip())]
            if indent == func.body_indent and faulty_line > func.body_start - 1:
                idx = faulty_line - 1
                while idx - 1 >= func.body_start - 1 and lines[idx - 1].strip().startswith("#"):
                    idx -= 1  # hatalı satırın üstündeki yorum bloğunu koru
                return idx, indent
        return func.body_start - 1, func.body_indent

    def _guard_diff(self, rel: str, source: str, func: FunctionInfo, cond: str, faulty_line: Optional[int]) -> str:
        lines = source.splitlines(keepends=True)
        idx, indent = self._insertion_line(lines, func, faulty_line)
        guard = [f"{indent}if {cond}:\n", f"{indent}    return {default_return_literal(func.returns)}\n"]
        return GitOps.make_diff(rel, lines, lines[:idx] + guard + lines[idx:])

    # ------------------------------------------------------------------ llm
    def _llm_patch(self, state: ExecutionState, rel: str, source: str, func: FunctionInfo) -> PatchResult:
        lines = source.splitlines(keepends=True)
        func_src = "".join(lines[func.lineno - 1: func.end_lineno])
        feedback = "\n\n".join(
            f"Attempt {r.iteration} ({r.strategy}) FAILED at {r.validation.stage}:\n{r.unified_diff}\n"
            f"--- output ---\n{(r.validation.stdout + r.validation.stderr)[-1500:]}"
            for r in state.iteration_history
        ) or "None (first attempt)."
        data = self.llm.complete_json(
            "You are a senior engineer producing the smallest possible bug fix. Change only the lines "
            "required. Do not refactor, rename, reformat or change behaviour for inputs that already work.",
            f"Root cause: {state.triage.root_cause}\nReproduction test:\n```python\n{state.synthesized_test.test_code}\n```\n"
            f"Previous attempts and sandbox feedback:\n{feedback}\n\n"
            f"Current function (lines {func.lineno}-{func.end_lineno} of {rel}):\n```python\n{func_src}```\n"
            'Return {"fixed_function": "<complete corrected function source, same indentation>", "explanation": "..."}',
        )
        fixed = re.sub(r"^```(?:python)?\n|```\s*$", "", str(data["fixed_function"]).strip("\n")) + "\n"
        compile(fixed if not fixed.startswith((" ", "\t")) else "if True:\n" + fixed, rel, "exec")
        new_lines = lines[: func.lineno - 1] + fixed.splitlines(keepends=True) + lines[func.end_lineno:]
        diff = GitOps.make_diff(rel, lines, new_lines)
        if not diff:
            raise ValueError("LLM returned an unchanged function.")
        return PatchResult(
            target_file=rel, unified_diff=diff, source=self.llm.provider,
            strategy=f"llm:{len(state.iteration_history)}",
        )
