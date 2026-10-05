"""Sınır değer (boundary) probu.

Hedef fonksiyonu, tip ipuçlarından türetilen sınır değerleriyle **ayrı bir
süreçte** çağırır ve hangi girdilerin hatayı tetiklediğini kaydeder.
Böylece Test Synthesizer, hatayı kanıtlayan girdileri tahmin etmez; ölçer.
"""
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

from tools.code_analyzer import FunctionInfo, module_name
from tools.sandbox import IGNORE

BOUNDARY_VALUES: Dict[str, List[Any]] = {
    "seq": [[], [1.0], [1.0, 2.0], [1.0, 2.0, 3.0, 4.0]],
    "int": [0, 1, 2, 3, 5, -1],
    "float": [0.0, 1.0, -1.0, 2.5],
    "str": ["", "a", "abc"],
    "bool": [False, True],
    "map": [{}, {"a": 1}],
    "any": [None, 0, [], ""],
}

_PROBE_SCRIPT = r"""
import importlib, json, sys, traceback
mod = importlib.import_module(sys.argv[1])
fn = getattr(mod, sys.argv[2])
cases = json.loads(sys.stdin.read())
out = []
for kwargs in cases:
    try:
        res = fn(**kwargs)
        try:
            json.dumps(res)
            out.append({"kwargs": kwargs, "ok": True, "result": res})
        except TypeError:
            out.append({"kwargs": kwargs, "ok": True, "result": None, "unserializable": True})
    except BaseException as e:
        out.append({"kwargs": kwargs, "ok": False, "error_type": type(e).__name__, "error": str(e)})
print(json.dumps(out))
"""


def build_cases(func: FunctionInfo, limit: int = 256) -> List[Dict[str, Any]]:
    pools = [BOUNDARY_VALUES[p.kind] for p in func.params]
    cases = [dict(zip([p.name for p in func.params], combo)) for combo in itertools.product(*pools)]
    return cases[:limit]


def probe_function(repo_path: str, rel_file: str, func: FunctionInfo, timeout: int = 30) -> List[Dict[str, Any]]:
    """Fonksiyonu izole bir kopyada tüm sınır kombinasyonlarıyla çalıştırır."""
    if func.is_method:
        return []
    cases = build_cases(func)
    with tempfile.TemporaryDirectory(prefix="aegis_probe_") as tmp:
        shutil.copytree(repo_path, tmp, dirs_exist_ok=True, ignore=IGNORE)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=tmp)
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE_SCRIPT, module_name(rel_file), func.name],
            input=json.dumps(cases), cwd=tmp, capture_output=True, text=True, timeout=timeout, env=env,
        )
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []
