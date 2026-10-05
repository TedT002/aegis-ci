"""Güvenli Unified Diff uygulayıcı ve diff üretici."""
import difflib
import os
import subprocess
import uuid
from typing import List, Tuple


class GitOps:
    @staticmethod
    def make_diff(rel_path: str, original: List[str], modified: List[str], context: int = 3) -> str:
        """İki satır listesinden `git apply` uyumlu unified diff üretir."""
        return "".join(
            difflib.unified_diff(original, modified, fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}", n=context)
        )

    @staticmethod
    def check_diff(repo_dir: str, diff_text: str) -> Tuple[bool, str]:
        """Yamayı uygulamadan, uygulanabilir olup olmadığını kontrol eder."""
        return GitOps._run(repo_dir, diff_text, check_only=True)

    @staticmethod
    def apply_diff(repo_dir: str, diff_text: str) -> Tuple[bool, str]:
        """Önce `git apply`, başarısız olursa `patch -p1` ile uygular.

        Her iki araç da önce dry-run ile denenir; böylece yarım uygulanmış
        (bozuk) bir çalışma dizini asla bırakılmaz.
        """
        return GitOps._run(repo_dir, diff_text, check_only=False)

    @staticmethod
    def _run(repo_dir: str, diff_text: str, check_only: bool) -> Tuple[bool, str]:
        if not diff_text or not diff_text.strip():
            return False, "Empty diff."
        patch_name = f".aegis_{uuid.uuid4().hex[:8]}.patch"
        patch_file = os.path.join(repo_dir, patch_name)
        try:
            with open(patch_file, "w", encoding="utf-8") as f:
                f.write(diff_text if diff_text.endswith("\n") else diff_text + "\n")

            git_check = subprocess.run(
                ["git", "apply", "--check", "--ignore-whitespace", patch_name],
                cwd=repo_dir, capture_output=True, text=True,
            )
            if git_check.returncode == 0:
                if check_only:
                    return True, "git apply --check OK."
                proc = subprocess.run(
                    ["git", "apply", "--ignore-whitespace", patch_name],
                    cwd=repo_dir, capture_output=True, text=True,
                )
                if proc.returncode == 0:
                    return True, "Patch applied successfully via git apply."

            patch_check = subprocess.run(
                ["patch", "-p1", "--dry-run", "--batch", "--fuzz=0", "-i", patch_name],
                cwd=repo_dir, capture_output=True, text=True,
            )
            if patch_check.returncode == 0:
                if check_only:
                    return True, "patch --dry-run OK."
                proc = subprocess.run(
                    ["patch", "-p1", "--batch", "--fuzz=0", "--no-backup-if-mismatch", "-i", patch_name],
                    cwd=repo_dir, capture_output=True, text=True,
                )
                if proc.returncode == 0:
                    return True, "Patch applied successfully via patch command."

            return False, f"git apply: {git_check.stderr.strip()}\npatch: {(patch_check.stdout + patch_check.stderr).strip()}"
        except FileNotFoundError as e:
            return False, f"Required tool missing: {e}"
        finally:
            if os.path.exists(patch_file):
                os.remove(patch_file)
