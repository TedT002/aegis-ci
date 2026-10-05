# Aegis-CI

[![CI](https://github.com/TedT002/aegis-ci/actions/workflows/aegis-ci.yml/badge.svg)](https://github.com/TedT002/aegis-ci/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A small tool that tries to fix a failing CI run on its own. Give it the failure log and the repo, and it will find the broken function, write a test that reproduces the bug, propose a patch, and only keep the patch if the test and the rest of the suite pass in a sandbox.

I built it because most "AI fixes your code" demos stop at *generating* a patch. Nobody checks that the bug was real or that the fix works. Here the model (if you even use one) is never trusted on its own: a patch only counts once it has been run.

## How it works

```
CI log -> Triage -> Test synthesis -> Reproduction check -> Patch <-> Sandbox -> patch.diff + report
                                                              ^          |
                                                              +- failed -+
```

1. **Triage** (`core/agents/triage.py`) parses the traceback / pytest output, maps the frames to files in the repo and uses `ast` to find the function and line that blew up.
2. **Test synthesis** (`core/agents/test_synth.py`) probes the function with boundary inputs (empty list, zero, etc.), finds the ones that raise the reported error and writes a small `test_aegis_reproduce.py`.
3. **Reproduction check** (`core/agents/verifier.py`) runs that test on the *unpatched* code. If it doesn't fail the way CI did, we stop. There's no point patching a bug we can't reproduce.
4. **Patcher** (`core/agents/patcher.py`) produces a minimal unified diff. Without an LLM it uses simple guard-clause heuristics; with one, the LLM suggests the change. If the sandbox rejects a patch, the test output goes back to the patcher for another try (`--max-retries`, default 3).
5. **Sandbox** (`tools/sandbox.py`) copies the repo to a temp dir (or runs a Docker container with no network), applies the patch, then runs the new test plus the existing test suite.

Exit codes: `0` verified patch, `1` couldn't fix it, `2` couldn't reproduce / triage failed.

## Install

```bash
git clone https://github.com/TedT002/aegis-ci.git
cd aegis-ci
pip install -r requirements.txt
pip install -e .
```

Python 3.10+.

## Usage

There's a deliberately broken example in `fixtures/sample_repo`:

```bash
python -m core.orchestrator --log-path fixtures/sample_repo/failure.log --repo-path fixtures/sample_repo
# or, after pip install -e .
aegis-ci --log-path fixtures/sample_repo/failure.log --repo-path fixtures/sample_repo
```

| Flag | Default | What it does |
|---|---|---|
| `--log-path` | required | CI failure log or traceback |
| `--repo-path` | required | Root of the repo to fix |
| `--max-retries` | `3` | Patch / sandbox attempts |
| `--sandbox` | `local` | `local`, `docker` or `auto` |
| `--docker-image` | `aegis-sandbox:latest` | Image to use for the docker sandbox (see `Dockerfile.sandbox`) |
| `--llm` | `auto` | `auto`, `none`, `gemini`, `openai` |
| `--output-dir` | repo root | Where `patch.diff` and `rca_report.md` go |
| `--apply` | off | Apply the verified patch to the repo |
| `--quiet` | off | No banner / progress output |

The LLM is optional. With `--llm auto` it looks for an API key in your environment and falls back to the deterministic mode if there isn't one. The sample above runs fine without any key.

## Example run

On the sample repo, `calculate_moving_average()` throws an `IndexError` on short input. The first guard it tries (`if not data`) isn't enough, the sandbox rejects it, and the second attempt passes:

```text
[1/5] Triage Agent
   IndexError: list index out of range
   Location : math_service.py:10 in calculate_moving_average()

[3/5] Reproduction Proof
   Bug reproduced: test FAILED with IndexError

[4/5] Patcher <-> Sandbox loop
   Iteration 1/3  guard: if not data: return []
      sandbox rejected patch, feeding back to patcher
   Iteration 2/3  guard: if len(data) < window_size: return []
      reproduction + regression suite PASSED

[5/5] Report
   patch.diff, rca_report.md, aegis_report.json written
```

Resulting patch (`fixtures/sample_repo/patch.diff`):

```diff
@@ -6,6 +6,8 @@
     if window_size <= 0:
         return []
     averages = []
+    if len(data) < window_size:
+        return []
     window_sum = sum(data[i] for i in range(window_size))
```

## Tests

```bash
pip install pytest-cov
pytest tests/ --cov=core --cov=tools
```

## Limitations

- Python only (it relies on `ast` and pytest).
- The heuristic patcher really only knows about guard-clause style fixes, e.g. missing bounds checks. For anything more involved you need an LLM backend.
- It fixes one failing location per run.
- The local sandbox is just a temp directory, not real isolation. Use `--sandbox docker` if you're running code you don't trust.

## Layout

```
core/          orchestrator, agents, state models, LLM client, report writer
tools/         log parser, AST helpers, input prober, sandbox, git helpers
tests/         unit and end-to-end tests
fixtures/      sample broken repo used for the demo and the tests
Dockerfile.sandbox
```

## License

MIT, see [LICENSE](LICENSE).
