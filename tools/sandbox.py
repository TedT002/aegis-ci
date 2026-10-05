"""Hibrit izole çalışma ortamı.

* `local`  : Repo, geçici bir dizine kopyalanır; yama ve test orada uygulanır.
             Orijinal repo asla değiştirilmez.
* `docker` : Aynı geçici dizin, ağ erişimi kapalı, bellek/CPU sınırlı ve
             yetkisiz bir container'a mount edilip testler orada koşturulur.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional

from tools.git_ops import GitOps

IGNORE = shutil.ignore_patterns(
    "__pycache__", ".pytest_cache", "*.pyc", ".git", ".venv", "patch.diff", "rca_report.md", "aegis_report.json"
)


class HybridSandbox:
    def __init__(
        self,
        use_docker: bool = False,
        image: str = "aegis-sandbox:latest",
        timeout: int = 120,
        python: Optional[str] = None,
    ):
        self.use_docker = use_docker
        self.image = image
        self.timeout = timeout
        self.python = python or sys.executable

    @property
    def mode(self) -> str:
        return "docker" if self.use_docker else "local"

    @staticmethod
    def docker_available() -> bool:
        if not shutil.which("docker"):
            return False
        try:
            return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
        except Exception:
            return False

    def _test_cmd(self, test_targets: Optional[List[str]]) -> List[str]:
        py = "python" if self.use_docker else self.python
        cmd = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "--color=no"]
        return cmd + list(test_targets or [])

    def _exec(self, workdir: str, cmd: List[str]) -> subprocess.CompletedProcess:
        if self.use_docker:
            cmd = [
                "docker", "run", "--rm", "--network", "none", "--memory", "512m", "--cpus", "1",
                "--pids-limit", "256", "--security-opt", "no-new-privileges",
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-v", f"{workdir}:/workspace", "-w", "/workspace", self.image,
            ] + cmd
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=self.timeout, env=env)

    def run_validation(
        self,
        repo_dir: str,
        patch_diff: Optional[str],
        test_file_rel: Optional[str] = None,
        test_code: Optional[str] = None,
        test_targets: Optional[List[str]] = None,
    ) -> Dict:
        """Repo'yu izole kopyalar, (opsiyonel) yamayı uygular, testleri koşturur.

        `patch_diff=None` → yamasız çalıştırma (reproduction kanıtı için).
        `test_targets=None` → tüm test paketi (regresyon + sentezlenmiş test).
        """
        start = time.time()
        with tempfile.TemporaryDirectory(prefix="aegis_sandbox_") as tmp_dir:
            shutil.copytree(repo_dir, tmp_dir, dirs_exist_ok=True, ignore=IGNORE)

            if test_file_rel and test_code is not None:
                target_test_path = os.path.join(tmp_dir, test_file_rel)
                os.makedirs(os.path.dirname(target_test_path), exist_ok=True)
                with open(target_test_path, "w", encoding="utf-8") as f:
                    f.write(test_code)

            if patch_diff:
                ok, msg = GitOps.apply_diff(tmp_dir, patch_diff)
                if not ok:
                    return {
                        "success": False, "stage": "patch_application", "exit_code": 1,
                        "stdout": "", "stderr": msg, "error": "Patch application failed.",
                        "duration_s": round(time.time() - start, 3),
                    }

            cmd = self._test_cmd(test_targets)
            try:
                run = self._exec(tmp_dir, cmd)
            except subprocess.TimeoutExpired:
                return {
                    "success": False, "stage": "test_execution", "exit_code": 124, "stdout": "",
                    "stderr": f"Timeout after {self.timeout}s: {shlex.join(cmd)}",
                    "error": "Sandbox timeout.", "duration_s": round(time.time() - start, 3),
                }

            ok = run.returncode == 0
            return {
                "success": ok, "stage": "test_execution", "exit_code": run.returncode,
                "stdout": run.stdout, "stderr": run.stderr,
                "error": None if ok else "Tests failed in sandbox.",
                "duration_s": round(time.time() - start, 3),
            }
