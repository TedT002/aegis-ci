"""3.1 Triage Agent — ham CI logundan kesin kök neden analizi."""
import os
from typing import Optional

from core.llm import LLMClient, LLMError
from core.state import TriageResult
from tools.code_analyzer import find_function, resolve_repo_file
from tools.parser import LogParser

EXPLANATIONS = {
    "IndexError": "a sequence is indexed beyond its length; the function lacks a boundary check on input size",
    "KeyError": "a mapping is accessed with a key that may be absent; missing membership check",
    "ZeroDivisionError": "a divisor can be zero for some inputs; missing guard on the denominator",
    "TypeError": "an operation receives a value of an unexpected type (often None); missing input validation",
    "AttributeError": "an attribute is accessed on an object that may be None or the wrong type",
    "ValueError": "a builtin received an argument with an invalid value (e.g. empty sequence to max/min)",
    "StopIteration": "an iterator is exhausted earlier than expected; missing emptiness check",
}


class TriageAgent:
    def __init__(self, repo_path: str, llm: Optional[LLMClient] = None):
        self.repo_path = repo_path
        self.llm = llm

    def run(self, log_content: str) -> TriageResult:
        parsed = LogParser.parse_traceback(log_content)
        frames = parsed["frames"]

        # Log'daki frame'leri iç→dış tarayıp repo içinde var olan ilk uygulama dosyasını bul.
        rel_file, line, func_hint = None, None, None
        app = LogParser.innermost_app_frame(frames)
        ordered = ([app] if app else []) + list(reversed(frames))
        for fr in ordered:
            rel = resolve_repo_file(self.repo_path, fr["file"])
            if rel and not os.path.basename(rel).startswith("test_"):
                rel_file, line, func_hint = rel, fr["line"], fr.get("function")
                break
        if rel_file is None:
            raise ValueError("Triage failed: no stack frame in the log maps to a source file in the repository.")

        with open(os.path.join(self.repo_path, rel_file), encoding="utf-8") as f:
            source = f.read()
        func = find_function(source, name=func_hint, line=line)
        src_lines = source.splitlines()
        faulty_code = src_lines[line - 1].strip() if line and 0 < line <= len(src_lines) else None

        err = parsed["error_type"]
        func_name = func.name if func else (func_hint or "<module>")
        explanation = EXPLANATIONS.get(err, "the function raises an unhandled exception for some inputs")
        root_cause = (
            f"{err} in `{func_name}()` at {rel_file}:{line} while executing `{faulty_code}` "
            f"({parsed['error_message'] or 'no message'}): {explanation}."
        )
        result = TriageResult(
            root_cause=root_cause, faulty_files=[rel_file],
            suspected_functions=[func_name] if func else [], error_type=err,
            error_message=parsed["error_message"], faulty_line=line, faulty_code=faulty_code,
            failing_tests=parsed["failing_tests"],
        )
        if self.llm:
            result = self._refine_with_llm(result, source)
        return result

    def _refine_with_llm(self, result: TriageResult, source: str) -> TriageResult:
        """LLM yalnızca anlatımı zenginleştirir; dosya/satır/hata tipi gibi olgular parser'dan gelir."""
        numbered = "\n".join(f"{i + 1:4d}| {l}" for i, l in enumerate(source.splitlines()))
        try:
            data = self.llm.complete_json(
                "You are a senior SRE performing root-cause analysis of a CI failure.",
                f"Facts (authoritative): {result.model_dump_json()}\n\nSource of {result.faulty_files[0]}:\n{numbered}\n\n"
                'Return {"root_cause": "<2-3 precise sentences naming the exact line, the triggering input condition and why it fails>"}',
            )
            text = str(data.get("root_cause", "")).strip()
            if text:
                return result.model_copy(update={"root_cause": text, "source": f"heuristic+{self.llm.provider}"})
        except LLMError:
            pass
        return result
