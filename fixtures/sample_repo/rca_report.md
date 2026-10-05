# Aegis-CI — Root Cause Analysis Report

**Status:** ✅ VERIFIED — ready for PR  
**Generated:** 2026-10-05 09:32:19 UTC  
**Repair iterations:** 2/3

## 1. Triage

| Field | Value |
|---|---|
| Error type | `IndexError` |
| Error message | `list index out of range` |
| Faulty file | `math_service.py` |
| Faulty line | `10` → `window_sum = sum(data[i] for i in range(window_size))` |
| Suspected function | `calculate_moving_average` |
| Failing CI tests | `tests/test_math.py::test_empty` |
| Analysis source | heuristic |

**Root cause:** IndexError in `calculate_moving_average()` at math_service.py:10 while executing `window_sum = sum(data[i] for i in range(window_size))` (list index out of range): a sequence is indexed beyond its length; the function lacks a boundary check on input size.

## 2. Reproduction Proof

A minimal test (`tests/test_aegis_reproduce.py`, source: heuristic) was synthesized and executed against the **unpatched** code in an isolated sandbox.

- Result: **FAILED as expected — bug reproduced**
- Exit code: `1`
- Inputs proven to trigger `IndexError`: `{'data': [], 'window_size': 1}`, `{'data': [], 'window_size': 2}`, `{'data': [], 'window_size': 3}`, `{'data': [], 'window_size': 5}`, `{'data': [1.0], 'window_size': 2}`, `{'data': [1.0, 2.0], 'window_size': 3}`

```python
"""Aegis-CI auto-generated reproduction test.

Root cause : IndexError in `calculate_moving_average()` at math_service.py:10 while executing `window_sum = sum(data[i] for i in range(window_size))` (list index out of range): a sequence is indexed beyond its length; the function lacks a boundary check on input size.
Contract   : `calculate_moving_average` must not raise IndexError for boundary inputs and must
             keep its existing behaviour for inputs that already worked.
"""
import pytest

from math_service import calculate_moving_average

# Inputs measured (by probing the original code) to raise IndexError.
FAILING_INPUTS = [
    {'data': [], 'window_size': 1},
    {'data': [], 'window_size': 2},
    {'data': [], 'window_size': 3},
    {'data': [], 'window_size': 5},
    {'data': [1.0], 'window_size': 2},
    {'data': [1.0, 2.0], 'window_size': 3},
]

# Characterisation cases: outputs recorded from the original implementation.
PRESERVED_BEHAVIOUR = [
    ({'data': [1.0], 'window_size': 1}, [1.0]),
    ({'data': [1.0, 2.0], 'window_size': 1}, [1.0, 2.0]),
    ({'data': [1.0, 2.0], 'window_size': 2}, [1.5]),
    ({'data': [1.0, 2.0, 3.0, 4.0], 'window_size': 1}, [1.0, 2.0, 3.0, 4.0]),
]


@pytest.mark.parametrize("kwargs", FAILING_INPUTS)
def test_aegis_reproduces_indexerror(kwargs):
    try:
        result = calculate_moving_average(**kwargs)
    except IndexError as exc:
        pytest.fail(f"calculate_moving_average(**{kwargs}) raised IndexError: {exc}")
    assert isinstance(result, list)


@pytest.mark.parametrize("kwargs, expected", PRESERVED_BEHAVIOUR)
def test_aegis_preserves_behaviour(kwargs, expected):
    assert calculate_moving_average(**kwargs) == expected
```

## 3. Repair Loop

| Iteration | Strategy | Stage | Exit | Result |
|---|---|---|---|---|
| 1 | `guard: if not data: return []` | test_execution | 1 | ❌ FAILED tests/test_aegis_reproduce.py::test_aegis_reproduces_indexerror[kwargs4]; FAILED tests/test_aegis_reproduce.py::test_aegis_reproduces_indexerror[kwargs5] |
| 2 | `guard: if len(data) < window_size: return []` | test_execution | 0 | ✅ pass |

## 4. Verified Patch

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

## 5. Verification

- Sandbox: reproduction test + full regression suite
- Exit code: `0`
- Pytest summary: `13 passed in 0.01s`
- Duration: 0.21s
