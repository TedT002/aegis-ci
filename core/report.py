"""RCA raporu ve makine-okunur özet üretimi."""
import json
from datetime import datetime, timezone

from core.agents.verifier import summarize_failures
from core.state import ExecutionState


def render_rca_markdown(state: ExecutionState) -> str:
    t, s, p, v = state.triage, state.synthesized_test, state.current_patch, state.final_validation
    status = "✅ VERIFIED — ready for PR" if state.verification_passed else "❌ NOT FIXED — human review required"
    out = [
        "# Aegis-CI — Root Cause Analysis Report",
        "",
        f"**Status:** {status}  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Repair iterations:** {state.current_retry}/{state.max_retries}",
        "",
        "## 1. Triage",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Error type | `{t.error_type}` |",
        f"| Error message | `{t.error_message}` |",
        f"| Faulty file | `{', '.join(t.faulty_files)}` |",
        f"| Faulty line | `{t.faulty_line}` → `{t.faulty_code}` |",
        f"| Suspected function | `{', '.join(t.suspected_functions)}` |",
        f"| Failing CI tests | {', '.join(f'`{x}`' for x in t.failing_tests) or '-'} |",
        f"| Analysis source | {t.source} |",
        "",
        f"**Root cause:** {t.root_cause}",
        "",
        "## 2. Reproduction Proof",
        "",
        f"A minimal test (`{s.test_file_path}`, source: {s.source}) was synthesized and executed against the "
        f"**unpatched** code in an isolated sandbox.",
        "",
        f"- Result: **{'FAILED as expected — bug reproduced' if state.reproduction and state.reproduction.success else 'not reproduced'}**",
        f"- Exit code: `{state.reproduction.exit_code if state.reproduction else '-'}`",
    ]
    if s.failing_inputs:
        out.append(f"- Inputs proven to trigger `{t.error_type}`: " + ", ".join(f"`{fi}`" for fi in s.failing_inputs))
    out += ["", "```python", s.test_code.rstrip(), "```", "", "## 3. Repair Loop", "",
            "| Iteration | Strategy | Stage | Exit | Result |", "|---|---|---|---|---|"]
    for r in state.iteration_history:
        out.append(f"| {r.iteration} | `{r.strategy}` | {r.validation.stage} | {r.validation.exit_code} | "
                   f"{'✅ pass' if r.validation.success else '❌ ' + '; '.join(summarize_failures(r.validation.stdout + r.validation.stderr, 2)).replace('|', '/')} |")
    if p:
        out += ["", "## 4. Verified Patch" if state.verification_passed else "## 4. Last Attempted Patch", "",
                "```diff", p.unified_diff.rstrip(), "```"]
    if v:
        tail = [l for l in v.stdout.strip().splitlines() if l.strip()][-1:] or ["-"]
        out += ["", "## 5. Verification", "",
                f"- Sandbox: reproduction test + full regression suite",
                f"- Exit code: `{v.exit_code}`",
                f"- Pytest summary: `{tail[0]}`",
                f"- Duration: {v.duration_s:.2f}s"]
    out.append("")
    return "\n".join(out)


def render_json(state: ExecutionState) -> str:
    return json.dumps({
        "verified": state.verification_passed,
        "iterations": state.current_retry,
        "triage": state.triage.model_dump() if state.triage else None,
        "reproduction_proven": bool(state.reproduction and state.reproduction.success),
        "failing_inputs": state.synthesized_test.failing_inputs if state.synthesized_test else [],
        "patch": state.current_patch.model_dump() if state.current_patch else None,
        "history": [
            {"iteration": r.iteration, "strategy": r.strategy, "stage": r.validation.stage,
             "exit_code": r.validation.exit_code, "success": r.validation.success}
            for r in state.iteration_history
        ],
    }, indent=2, ensure_ascii=False)
