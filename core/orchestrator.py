"""Aegis-CI kapalı döngü orkestratörü (durum makinesi + CLI).

    TRIAGE → TEST_SYNTH → REPRODUCTION_PROOF → [PATCH → VERIFY]×N → REPORT

Çıkış kodları: 0 = doğrulanmış yama, 1 = onarılamadı, 2 = hata yeniden üretilemedi / triage başarısız.
"""
import argparse
import os
import sys
from typing import Optional

from core.agents import PatcherAgent, TestSynthesizerAgent, TriageAgent, VerificationAgent
from core.agents.verifier import summarize_failures
from core.llm import LLMClient, LLMError, build_llm
from core.report import render_json, render_rca_markdown
from core.state import ExecutionState, IterationRecord
from core.ui import Console, bold, cyan, green, red
from tools.git_ops import GitOps
from tools.sandbox import HybridSandbox

TOTAL_STAGES = 5


class AegisOrchestrator:
    def __init__(
        self,
        sandbox: Optional[HybridSandbox] = None,
        llm: Optional[LLMClient] = None,
        console: Optional[Console] = None,
        output_dir: Optional[str] = None,
        apply: bool = False,
    ):
        self.sandbox = sandbox or HybridSandbox()
        self.llm = llm
        self.console = console or Console()
        self.output_dir = output_dir
        self.apply = apply

    def run(self, state: ExecutionState) -> ExecutionState:
        c, repo = self.console, state.repo_path
        triage_agent = TriageAgent(repo, self.llm)
        synth_agent = TestSynthesizerAgent(repo, self.llm)
        patcher = PatcherAgent(repo, self.llm)
        verifier = VerificationAgent(self.sandbox)

        c.banner("🛡️  AEGIS-CI — Autonomous Self-Healing Engine")
        c.info(f"Repository : {repo}")
        c.info(f"Sandbox    : {self.sandbox.mode}")
        c.info(f"Reasoning  : {f'{self.llm.provider} ({self.llm.model}) + deterministic gates' if self.llm else 'deterministic (no LLM key found)'}")

        # 1 ─ Triage ----------------------------------------------------------
        c.stage(1, TOTAL_STAGES, "Triage Agent — root cause isolation")
        state.triage = triage_agent.run(state.log_content)
        t = state.triage
        c.ok(f"{bold(t.error_type)}: {t.error_message}")
        c.ok(f"Location : {t.faulty_files[0]}:{t.faulty_line} in {bold(', '.join(t.suspected_functions) or '?')}()")
        c.info(f"Code     : {t.faulty_code}")
        c.info(f"Root cause: {t.root_cause}")

        # 2 ─ Test synthesis + 3 ─ reproduction proof -------------------------
        c.stage(2, TOTAL_STAGES, "Test Synthesizer Agent — minimal reproduction test")
        state.synthesized_test = synth_agent.run(t)
        c.ok(f"Generated {state.synthesized_test.test_file_path} (source: {state.synthesized_test.source})")
        if state.synthesized_test.failing_inputs:
            c.info(f"Probed inputs raising {t.error_type}: {state.synthesized_test.failing_inputs}")

        c.stage(3, TOTAL_STAGES, "Reproduction Proof — test must FAIL on unpatched code")
        state.reproduction = verifier.prove_reproduction(repo, state.synthesized_test, t)
        if not state.reproduction.success and state.synthesized_test.source != "heuristic":
            c.warn("LLM-generated test did not reproduce the bug → falling back to deterministic synthesis")
            state.synthesized_test = synth_agent.run(t, use_llm=False)
            state.reproduction = verifier.prove_reproduction(repo, state.synthesized_test, t)
        if not state.reproduction.success:
            c.fail("Bug could not be reproduced. Refusing to patch (anti-hallucination gate).")
            c.block(state.reproduction.stdout[-1500:])
            return state
        c.ok(f"Bug reproduced: test FAILED with {t.error_type} (exit {state.reproduction.exit_code}) ✓ proof established")
        for line in summarize_failures(state.reproduction.stdout, 3):
            c.info(line)

        # 4 ─ Patch / verify feedback loop ------------------------------------
        c.stage(4, TOTAL_STAGES, f"Patcher ⇄ Sandbox feedback loop (max {state.max_retries} iterations)")
        while state.current_retry < state.max_retries and not state.verification_passed:
            state.current_retry += 1
            c.print(bold(f"\n   ── Iteration {state.current_retry}/{state.max_retries}"))
            try:
                patch = patcher.run(state)
            except ValueError as e:
                c.fail(str(e))
                break
            state.current_patch = patch
            c.info(f"Strategy: {patch.strategy} (source: {patch.source})")
            c.block(patch.unified_diff)

            val = verifier.verify(repo, patch, state.synthesized_test)
            state.iteration_history.append(IterationRecord(
                iteration=state.current_retry, strategy=patch.strategy,
                unified_diff=patch.unified_diff, validation=val,
            ))
            state.final_validation = val
            if val.success:
                state.verification_passed = True
                summary = [l for l in val.stdout.strip().splitlines() if l.strip()][-1:]
                c.ok(green(f"Sandbox: reproduction + regression suite PASSED (exit 0) — {summary[0] if summary else ''}"))
            else:
                c.fail(f"Sandbox rejected patch at stage '{val.stage}' (exit {val.exit_code}) → feeding back to Patcher")
                for line in summarize_failures(val.stdout + val.stderr, 4) or [val.stderr.strip()[:300]]:
                    c.info(red(line))

        # 5 ─ Report ----------------------------------------------------------
        c.stage(5, TOTAL_STAGES, "Report & artifacts")
        self._save_artifacts(state)
        for name, path in state.artifacts.items():
            c.ok(f"{name:<12} → {path}")
        self._print_summary(state)
        return state

    # ---------------------------------------------------------------------------
    def _save_artifacts(self, state: ExecutionState) -> None:
        out = self.output_dir or state.repo_path
        os.makedirs(out, exist_ok=True)
        if state.current_patch and state.verification_passed:
            path = os.path.join(out, "patch.diff")
            with open(path, "w", encoding="utf-8") as f:
                f.write(state.current_patch.unified_diff)
            state.artifacts["patch.diff"] = path
        if state.triage:
            path = os.path.join(out, "rca_report.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(render_rca_markdown(state))
            state.artifacts["rca_report"] = path
            path = os.path.join(out, "aegis_report.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(render_json(state))
            state.artifacts["json"] = path

        if self.apply and state.verification_passed:
            ok, msg = GitOps.apply_diff(state.repo_path, state.current_patch.unified_diff)
            if ok:
                test_path = os.path.join(state.repo_path, state.synthesized_test.test_file_path)
                os.makedirs(os.path.dirname(test_path), exist_ok=True)
                with open(test_path, "w", encoding="utf-8") as f:
                    f.write(state.synthesized_test.test_code)
                state.artifacts["applied"] = f"{state.current_patch.target_file} (+ {state.synthesized_test.test_file_path})"
            else:
                self.console.fail(f"Could not apply patch to working tree: {msg}")

    def _print_summary(self, state: ExecutionState) -> None:
        c, t = self.console, state.triage
        c.print()
        c.banner("ROOT CAUSE ANALYSIS & VERIFICATION REPORT")
        rows = [
            ("Status", green("VERIFIED ✔") if state.verification_passed else red("NOT FIXED ✘")),
            ("Error", f"{t.error_type}: {t.error_message}"),
            ("Location", f"{t.faulty_files[0]}:{t.faulty_line}  →  {t.faulty_code}"),
            ("Function", ", ".join(t.suspected_functions)),
            ("Reproduced", "yes — synthesized test failed pre-patch" if state.reproduction and state.reproduction.success else "no"),
            ("Iterations", f"{state.current_retry}/{state.max_retries}"),
        ]
        if state.current_patch:
            rows.append(("Fix", state.current_patch.strategy))
        if state.final_validation:
            tail = [l for l in state.final_validation.stdout.strip().splitlines() if l.strip()][-1:]
            rows.append(("Sandbox", f"exit {state.final_validation.exit_code} — {tail[0] if tail else ''}"))
        for k, v in rows:
            c.print(f"  {cyan(f'{k:<11}')} {v}")
        c.print(f"\n  {bold('Root cause:')} {t.root_cause}")
        c.print(cyan("═" * 72))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aegis-ci", description="Autonomous self-healing CI repair engine.")
    p.add_argument("--log-path", required=True, help="CI failure log (pytest output / traceback)")
    p.add_argument("--repo-path", required=True, help="Repository to repair")
    p.add_argument("--max-retries", type=int, default=3, help="Max Patcher⇄Sandbox iterations (default 3)")
    p.add_argument("--sandbox", choices=["local", "docker", "auto"], default="local",
                   help="Isolation backend (docker requires `docker build -f Dockerfile.sandbox -t aegis-sandbox .`)")
    p.add_argument("--docker-image", default="aegis-sandbox:latest")
    p.add_argument("--llm", choices=["auto", "none", "gemini", "openai"], default="auto",
                   help="Reasoning backend; 'auto' uses GEMINI_API_KEY / OPENAI_API_KEY if present")
    p.add_argument("--output-dir", default=None, help="Where to write patch.diff / rca_report.md (default: repo)")
    p.add_argument("--apply", action="store_true", help="Apply verified patch + reproduction test to the repo")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    with open(args.log_path, "r", encoding="utf-8") as f:
        log_content = f.read()

    use_docker = args.sandbox == "docker" or (args.sandbox == "auto" and HybridSandbox.docker_available())
    try:
        llm = build_llm(args.llm)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    orchestrator = AegisOrchestrator(
        HybridSandbox(use_docker=use_docker, image=args.docker_image), llm=llm,
        console=Console(quiet=args.quiet), output_dir=args.output_dir, apply=args.apply,
    )
    state = ExecutionState(log_content=log_content, repo_path=os.path.abspath(args.repo_path),
                           max_retries=args.max_retries)
    try:
        result = orchestrator.run(state)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if result.verification_passed:
        return 0
    return 2 if not (result.reproduction and result.reproduction.success) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
