"""3.4 Verification Agent — sandbox içinde kanıt üretimi."""
import re
from typing import List, Optional

from core.state import PatchResult, TestSynthResult, TriageResult, ValidationResult
from tools.sandbox import HybridSandbox


def summarize_failures(output: str, limit: int = 6) -> List[str]:
    """Pytest çıktısından kısa, okunabilir hata özetleri çıkarır."""
    lines = [l.strip() for l in output.splitlines()]
    failed = [l for l in lines if l.startswith(("FAILED ", "ERROR "))]
    return failed[:limit] or [l for l in lines if l.startswith("E ")][:limit]


class VerificationAgent:
    def __init__(self, sandbox: HybridSandbox):
        self.sandbox = sandbox

    def prove_reproduction(self, repo_path: str, synth: TestSynthResult, triage: TriageResult) -> ValidationResult:
        """Sentezlenen test YAMASIZ kodda, raporlanan hata tipiyle FAIL etmelidir.

        success=True → hata kanıtlandı (test beklendiği gibi kırmızı).
        """
        raw = self.sandbox.run_validation(
            repo_dir=repo_path, patch_diff=None, test_file_rel=synth.test_file_path,
            test_code=synth.test_code, test_targets=[synth.test_file_path],
        )
        out = raw["stdout"] + raw["stderr"]
        # pytest exit 1 = testler koştu ve FAIL oldu (2 = toplama/sözdizimi hatası → kanıt sayılmaz)
        reproduced = raw["exit_code"] == 1 and bool(re.search(rf"\b{re.escape(triage.error_type)}\b", out))
        return ValidationResult(
            success=reproduced, stage="reproduction", exit_code=raw["exit_code"],
            stdout=raw["stdout"], stderr=raw["stderr"], duration_s=raw.get("duration_s", 0.0),
            error=None if reproduced else "Synthesized test did not fail with the reported error on unpatched code.",
        )

    def verify(self, repo_path: str, patch: PatchResult, synth: TestSynthResult,
               test_targets: Optional[List[str]] = None) -> ValidationResult:
        """Yama + sentezlenen test + tüm regresyon testleri → exit code 0 zorunlu."""
        raw = self.sandbox.run_validation(
            repo_dir=repo_path, patch_diff=patch.unified_diff, test_file_rel=synth.test_file_path,
            test_code=synth.test_code, test_targets=test_targets,
        )
        return ValidationResult(**raw)
