"""Aegis-CI veri sözleşmeleri (I/O Contracts).

Her ajan yalnızca bu Pydantic modellerini üretir/tüketir. Böylece LLM tabanlı
veya deterministik (heuristic) ajanlar birbirinin yerine takılabilir ve
çıktıları şema seviyesinde doğrulanır.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TriageResult(BaseModel):
    """3.1 Triage Agent çıktısı."""

    root_cause: str
    faulty_files: List[str]
    suspected_functions: List[str] = Field(default_factory=list)
    error_type: str
    error_message: str = ""
    faulty_line: Optional[int] = None
    faulty_code: Optional[str] = None
    failing_tests: List[str] = Field(default_factory=list)
    source: str = "heuristic"


class TestSynthResult(BaseModel):
    """3.2 Test Synthesizer Agent çıktısı."""

    __test__ = False  # pytest'in bu sınıfı test sınıfı sanmasını engeller

    test_file_path: str
    test_code: str
    failing_inputs: List[Dict[str, Any]] = Field(default_factory=list)
    source: str = "heuristic"


class PatchResult(BaseModel):
    """3.3 Patcher Agent çıktısı."""

    target_file: str
    unified_diff: str
    strategy: str = ""
    source: str = "heuristic"


class ValidationResult(BaseModel):
    """3.4 Sandbox / Verification Agent çıktısı."""

    success: bool
    stage: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    duration_s: float = 0.0


class IterationRecord(BaseModel):
    iteration: int
    strategy: str
    unified_diff: str
    validation: ValidationResult


class ExecutionState(BaseModel):
    log_content: str
    repo_path: str
    max_retries: int = 3
    current_retry: int = 0
    triage: Optional[TriageResult] = None
    synthesized_test: Optional[TestSynthResult] = None
    reproduction: Optional[ValidationResult] = None
    current_patch: Optional[PatchResult] = None
    final_validation: Optional[ValidationResult] = None
    verification_passed: bool = False
    iteration_history: List[IterationRecord] = Field(default_factory=list)
    artifacts: Dict[str, str] = Field(default_factory=dict)
