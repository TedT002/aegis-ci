import os

from tools.code_analyzer import (
    Param, default_return_literal, find_function, is_simple_literal, module_name, resolve_repo_file,
)
from tools.git_ops import GitOps
from tools.prober import probe_function
from tools.sandbox import HybridSandbox

SRC = '''class A:
    def m(self, x: int) -> int:
        return x


def f(data: list, n: int) -> list:
    """doc"""
    if n:
        y = 1
    return data
'''


def test_find_function_and_signature():
    fn = find_function(SRC, line=9)
    assert fn.name == "f" and fn.body_start == 8 and fn.body_indent == "    "
    assert [p.kind for p in fn.params] == ["seq", "int"]
    m = find_function(SRC, name="m")
    assert m.is_method and [p.name for p in m.params] == ["x"]
    assert find_function(SRC, name="missing") is None


def test_helpers(sample_repo):
    assert resolve_repo_file(sample_repo, "/workspace/sample_repo/math_service.py") == "math_service.py"
    assert resolve_repo_file(sample_repo, os.path.join(sample_repo, "math_service.py")) == "math_service.py"
    assert resolve_repo_file(sample_repo, "/nope/other.py") is None
    assert module_name("pkg/mod.py") == "pkg.mod"
    assert default_return_literal("List[float]") == "[]"
    assert default_return_literal("Optional[int]") == "None"
    assert default_return_literal("float") == "0.0"
    assert default_return_literal("Dict[str, int]") == "{}"
    assert Param("x", "Optional[List[int]]").kind == "seq"
    assert Param("x", "").kind == "any"
    assert is_simple_literal([1.0, 2]) and not is_simple_literal(object())


def test_prober_measures_failures(sample_repo):
    with open(os.path.join(sample_repo, "math_service.py")) as f:
        fn = find_function(f.read(), name="calculate_moving_average")
    res = probe_function(sample_repo, "math_service.py", fn)
    failing = [r["kwargs"] for r in res if not r["ok"]]
    assert {"data": [], "window_size": 3} in failing
    assert all(r["error_type"] == "IndexError" for r in res if not r["ok"])
    assert any(r["ok"] and r["result"] == [1.5] for r in res)


def test_git_ops_apply_and_reject(sample_repo):
    path = os.path.join(sample_repo, "math_service.py")
    with open(path) as f:
        original = f.readlines()
    modified = original[:5] + ["    # aegis\n"] + original[5:]
    diff = GitOps.make_diff("math_service.py", original, modified)

    assert GitOps.check_diff(sample_repo, diff)[0]
    with open(path) as f:
        assert f.readlines() == original  # --check yan etkisiz

    ok, msg = GitOps.apply_diff(sample_repo, diff)
    assert ok, msg
    with open(path) as f:
        assert "# aegis" in f.read()

    ok, msg = GitOps.apply_diff(sample_repo, diff.replace("def calculate", "def nope"))
    assert not ok
    assert GitOps.apply_diff(sample_repo, "") == (False, "Empty diff.")
    assert not any(n.endswith(".patch") for n in os.listdir(sample_repo))


def test_sandbox_isolation(sample_repo):
    sb = HybridSandbox()
    before = sorted(os.listdir(sample_repo))

    unpatched = sb.run_validation(sample_repo, None)
    assert not unpatched["success"] and unpatched["exit_code"] == 1
    assert "IndexError" in unpatched["stdout"]

    bad = sb.run_validation(sample_repo, "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert bad["stage"] == "patch_application" and not bad["success"]

    only = sb.run_validation(sample_repo, None, "tests/test_extra.py", "def test_ok():\n    assert True\n",
                             test_targets=["tests/test_extra.py"])
    assert only["success"]
    assert sorted(os.listdir(sample_repo)) == before  # orijinal repo dokunulmadı


def test_sandbox_timeout(sample_repo):
    sb = HybridSandbox(timeout=1)
    res = sb.run_validation(sample_repo, None, "tests/test_slow.py",
                            "import time\ndef test_slow():\n    time.sleep(5)\n", ["tests/test_slow.py"])
    assert res["exit_code"] == 124 and res["error"] == "Sandbox timeout."


def test_docker_command_construction(monkeypatch):
    sb = HybridSandbox(use_docker=True, image="img")
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr("tools.sandbox.subprocess.run", fake_run)
    sb._exec("/tmp/x", sb._test_cmd(None))
    cmd = captured["cmd"]
    assert cmd[:3] == ["docker", "run", "--rm"] and "--network" in cmd and "none" in cmd
    assert "img" in cmd and cmd[cmd.index("img") + 1] == "python"
    assert sb.mode == "docker"
