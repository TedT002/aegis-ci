# 🛡️ Aegis-CI: Autonomous Self-Healing CI/CD & Multi-Agent Verification Engine

[![CI Verification](https://github.com/TedT002/aegis-ci/actions/workflows/aegis-ci.yml/badge.svg)](https://github.com/TedT002/aegis-ci/actions)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Test Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/TedT002/aegis-ci)
[![Tests Passing](https://img.shields.io/badge/tests-43%20passed-success.svg)](https://github.com/TedT002/aegis-ci)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Architecture: Multi--Agent Closed--Loop](https://img.shields.io/badge/architecture-closed--loop%20feedback-purple.svg)](https://github.com/TedT002/aegis-ci)

> **Aegis-CI** moves beyond naive LLM chatbots and prompt wrappers. It is a deterministic, closed-loop multi-agent verification engine that automatically detects runtime and test failures in CI/CD pipelines, isolates the technical root cause, synthesizes minimal reproduction unit tests, develops targeted unified git patches, and rigorously verifies them in an isolated sandbox until 100% of regressions pass.

---

## 🌟 Why Aegis-CI?

Standard AI coding assistants suffer from critical limitations when applied to real software engineering workflows:
- **Hallucinated fixes:** Generating plausible-looking patches that break other edge cases or fail to compile.
- **Unverified diffs:** Refactoring entire files instead of proposing minimal, surgically targeted git diffs.
- **No proof of reproduction:** Assuming a bug exists without first creating an isolated failing test that reproduces it.

**Aegis-CI solves this with a Closed-Loop State Machine:**

```
                                      ┌────────────────────────────────────────────────────────┐
                                      │                                                        │
┌──────────────┐     ┌──────────────┐ │   ┌───────────────────┐     ┌──────────────────────┐   │
│  CI Failure  │ ──> │ Triage Agent │ ──> │  Test Synthesizer │ ──> │  Reproduction Proof  │   │
│     Log      │     │  (AST Scope) │ │   │ (Boundary Prober) │     │ (Must FAIL Pre-Patch)│   │
└──────────────┘     └──────────────┘ │   └───────────────────┘     └──────────────────────┘   │
                                      │                                        │               │
                                      │                                        ▼               │
                                      │                             ┌──────────────────────┐   │
                                      │        ┌──────────────────> │     Patcher Agent    │   │
                                      │        │   Failure Telemetry│ (Minimal Diff Synth) │   │
                                      │        │   Feedback Loop    └──────────────────────┘   │
                                      │        │                               │               │
                                      │        │                               ▼               │
                                      │        │                    ┌──────────────────────┐   │
                                      │        └─────────────────── │  Sandbox Verifier    │   │
                                      │         (Exit != 0 / Error) │ (Local Temp / Docker)│   │
                                      │                             └──────────────────────┘   │
                                      │                                        │ (Exit == 0)   │
                                      │                                        ▼               │
                                      │                             ┌──────────────────────┐   │
                                      │                             │ Verified patch.diff  │   │
                                      │                             │ & rca_report.md      │   │
                                      │                             └──────────────────────┘   │
                                      └────────────────────────────────────────────────────────┘
```

---

## 🤖 Multi-Agent Architecture & I/O Contracts

The engine coordinates 4 specialized agents through strictly validated Pydantic v2 schemas:

### 1. Triage Agent (`core/agents/triage.py`)
- **Input:** Raw CI failure log (`pytest` trace, classic Python traceback, exit codes).
- **Function:** Parses the stack frame, maps CI worker paths to local repository files, inspects the AST (Abstract Syntax Tree), and isolates the enclosing function, faulty line, and error type.
- **Schema:** `TriageResult` (`root_cause`, `faulty_files`, `suspected_functions`, `error_type`, `faulty_line`, `faulty_code`).

### 2. Test Synthesizer Agent (`core/agents/test_synth.py`)
- **Input:** `TriageResult` + source code AST analysis.
- **Function:** Uses boundary value probing (`tools/prober.py`) to systematically measure inputs that trigger the reported exception. Synthesizes a minimal `test_aegis_reproduce.py` containing both parameterized failing cases and characterization assertions locking existing behavior.
- **Schema:** `TestSynthResult` (`test_file_path`, `test_code`, `failing_inputs`).

### 3. Anti-Hallucination Reproduction Gate (`core/agents/verifier.py`)
- **Rule:** Before any patch is attempted, the synthesized test **must fail on the unpatched codebase** with the exact error reported in CI.
- If the test passes or errors out with a syntax error, the engine halts or falls back. No unprovable bugs are patched.

### 4. Patcher Agent (`core/agents/patcher.py`)
- **Input:** `TriageResult` + failing inputs + previous iteration feedback telemetry.
- **Function:** Generates minimal, surgically targeted Unified Diffs (`git apply` / `patch -p1` compatible). Supports deterministic guard-clause synthesis as well as LLM-assisted patching with local diff generation.
- **Schema:** `PatchResult` (`target_file`, `unified_diff`, `strategy`).

### 5. Verification Agent & Hybrid Sandbox (`tools/sandbox.py`)
- **Environment:** Clones repository into an isolated temporary workspace or Docker container (`network: none`, memory/CPU caps, non-root).
- **Execution:** Applies patch dry-run, runs synthesized tests AND the full regression suite.
- **Closed Loop:** If any test fails, the output is fed back to the Patcher (up to `--max-retries`). When all tests pass (exit 0), artifacts are approved and published.

---

## ⚡ Quick Start

### 1. Installation
Clone the repository and install dependencies:

```bash
git clone https://github.com/TedT002/aegis-ci.git
cd aegis-ci
pip install -r requirements.txt
pip install -e .
```

### 2. Running on the Sample Broken Repository
Run the closed-loop repair engine on the included benchmark failure:

```bash
python -m core.orchestrator --log-path fixtures/sample_repo/failure.log --repo-path fixtures/sample_repo
```

Or using the installed CLI tool:
```bash
aegis-ci --log-path fixtures/sample_repo/failure.log --repo-path fixtures/sample_repo
```

### 3. CLI Flags & Options
| Flag | Default | Description |
|---|:---:|---|
| `--log-path` | *Required* | Path to the CI failure log or traceback file |
| `--repo-path` | *Required* | Target repository root directory |
| `--max-retries` | `3` | Maximum Patcher ⇄ Sandbox feedback iterations |
| `--sandbox` | `local` | Sandbox backend (`local`, `docker`, `auto`) |
| `--docker-image`| `aegis-sandbox:latest` | Docker image to use when running containerized |
| `--llm` | `auto` | Reasoning backend (`auto`, `none`, `gemini`, `openai`) |
| `--output-dir` | Repo root | Custom destination for `patch.diff` and `rca_report.md` |
| `--apply` | `false` | Automatically apply verified patch to the working repository |
| `--quiet` | `false` | Suppress ANSI terminal banner and stage telemetry |

---

## 📊 Sample Run Walkthrough

When executed on [`fixtures/sample_repo`](fixtures/sample_repo), Aegis-CI analyzes an `IndexError` in `calculate_moving_average()`:

```text
════════════════════════════════════════════════════════════════════════
  🛡️  AEGIS-CI — Autonomous Self-Healing Engine
════════════════════════════════════════════════════════════════════════
   • Repository : fixtures/sample_repo
   • Sandbox    : local
   • Reasoning  : deterministic (no LLM key found)

[1/5] Triage Agent — root cause isolation
   ✔ IndexError: list index out of range
   ✔ Location : math_service.py:10 in calculate_moving_average()
   • Code     : window_sum = sum(data[i] for i in range(window_size))
   • Root cause: IndexError in `calculate_moving_average()` at math_service.py:10...

[2/5] Test Synthesizer Agent — minimal reproduction test
   ✔ Generated tests/test_aegis_reproduce.py (source: heuristic)
   • Probed inputs raising IndexError: [{'data': [], 'window_size': 1}, ...]

[3/5] Reproduction Proof — test must FAIL on unpatched code
   ✔ Bug reproduced: test FAILED with IndexError (exit 1) ✓ proof established

[4/5] Patcher ⇄ Sandbox feedback loop (max 3 iterations)

   ── Iteration 1/3
   • Strategy: guard: if not data: return []
   ✘ Sandbox rejected patch at stage 'test_execution' (exit 1) → feeding back to Patcher

   ── Iteration 2/3
   • Strategy: guard: if len(data) < window_size: return []
   ✔ Sandbox: reproduction + regression suite PASSED (exit 0) — 13 passed in 0.01s

[5/5] Report & artifacts
   ✔ patch.diff   → fixtures/sample_repo/patch.diff
   ✔ rca_report   → fixtures/sample_repo/rca_report.md
   ✔ json         → fixtures/sample_repo/aegis_report.json
```

### Generated Patch (`fixtures/sample_repo/patch.diff`)
```diff
--- a/math_service.py
+++ b/math_service.py
@@ -6,6 +6,8 @@
     if window_size <= 0:
         return []
     averages = []
+    if len(data) < window_size:
+        return []
     # Kasıtlı Hata: data boşken veya boyutu window_size'dan küçükken patlar
     window_sum = sum(data[i] for i in range(window_size))
     averages.append(window_sum / window_size)
```

---

## 🧪 Comprehensive Verification & Test Suite

The test suite covers full loop orchestration, edge cases, Docker command generation, AST parsing, dry-run safety, and mock LLM integrations:

```bash
pytest tests/ --cov=core --cov=tools --cov-report=term-missing
```

### Coverage Report
```text
---------- coverage: platform linux, python 3.10.12 ----------
Name                        Stmts   Miss  Cover   Missing
---------------------------------------------------------
core/__init__.py                0      0   100%
core/agents/__init__.py         5      0   100%
core/agents/patcher.py         83      0   100%
core/agents/test_synth.py      70      0   100%
core/agents/triage.py          47      0   100%
core/agents/verifier.py        19      0   100%
core/llm.py                    71      0   100%
core/orchestrator.py          155      0   100%
core/report.py                 22      0   100%
core/state.py                  49      0   100%
core/ui.py                     46      0   100%
tools/__init__.py               0      0   100%
tools/code_analyzer.py         90      0   100%
tools/git_ops.py               43      0   100%
tools/parser.py                59      0   100%
tools/prober.py                30      0   100%
tools/sandbox.py               56      0   100%
---------------------------------------------------------
TOTAL                         845      0   100%

============================== 43 passed in 8.09s ==============================
```

---

## 📁 Repository Layout

```
.
├── .github/workflows/
│   └── aegis-ci.yml           # GitHub Actions automated matrix CI
├── core/
│   ├── agents/
│   │   ├── triage.py          # Triage Agent (AST & frame mapper)
│   │   ├── test_synth.py      # Test Synthesizer (Boundary value prober)
│   │   ├── patcher.py         # Patcher Agent (Feedback loop diff generator)
│   │   └── verifier.py        # Verification & Anti-hallucination gate
│   ├── llm.py                 # Pluggable Gemini / OpenAI client
│   ├── orchestrator.py        # Main state machine & CLI entry point
│   ├── report.py              # RCA Markdown & JSON generator
│   ├── state.py               # Pydantic v2 I/O contracts
│   └── ui.py                  # ANSI terminal formatter
├── fixtures/
│   └── sample_repo/           # Benchmark broken repository & failure log
├── tools/
│   ├── code_analyzer.py       # AST inspector & type parser
│   ├── git_ops.py             # Safe atomic git apply & patch runner
│   ├── parser.py              # Traceback and pytest regex parser
│   ├── prober.py              # Boundary input isolation runner
│   └── sandbox.py             # Hybrid temporary & Docker sandbox
├── tests/
│   ├── test_agents.py         # Unit tests for each agent
│   ├── test_edge_cases.py     # Resiliency, timeout, and fallback tests
│   ├── test_orchestrator.py   # End-to-end integration tests
│   ├── test_parser.py         # Log parser verification
│   └── test_tools.py          # AST, sandbox, and git ops tests
├── Dockerfile.sandbox         # Hardened sandbox container definition
├── pyproject.toml             # Standard PEP 517/621 package config
├── requirements.txt           # Project dependencies
└── README.md
```

---

## 📄 License
This project is licensed under the [MIT License](LICENSE).
