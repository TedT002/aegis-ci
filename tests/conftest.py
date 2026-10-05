import os
import shutil
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_REPO = os.path.join(ROOT, "fixtures", "sample_repo")
ARTIFACTS = ("patch.diff", "rca_report.md", "aegis_report.json", "__pycache__", ".pytest_cache")


@pytest.fixture
def sample_repo(tmp_path):
    """Fixture repo'nun izole kopyası — testler asıl fixture'ı asla değiştirmez."""
    dst = tmp_path / "sample_repo"
    shutil.copytree(FIXTURE_REPO, dst, ignore=shutil.ignore_patterns(*ARTIFACTS))
    return str(dst)


@pytest.fixture
def sample_log():
    with open(os.path.join(FIXTURE_REPO, "failure.log"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def zero_div_repo(tmp_path):
    """Farklı hata tipi + klasik traceback formatı: motorun genelleştiğini kanıtlar."""
    repo = tmp_path / "stats_repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "stats.py").write_text(textwrap.dedent('''\
        from typing import List


        def mean(values: List[float]) -> float:
            """Aritmetik ortalama."""
            total = sum(values)
            return total / len(values)
        '''))
    (repo / "tests" / "test_stats.py").write_text(textwrap.dedent('''\
        from stats import mean


        def test_mean():
            assert mean([2.0, 4.0]) == 3.0
        '''))
    log = textwrap.dedent('''\
        Traceback (most recent call last):
          File "/ci/runner/build/tests/test_stats.py", line 9, in test_mean_empty
            mean([])
          File "/ci/runner/build/stats.py", line 7, in mean
            return total / len(values)
        ZeroDivisionError: division by zero
        ''')
    return str(repo), log


class FakeLLM:
    """Ağ erişimi olmadan LLM yolunu test etmek için sahte istemci."""

    provider = "fake"
    model = "fake-1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, prompt):
        self.calls.append(prompt)
        item = self.responses.pop(0) if self.responses else {}
        if isinstance(item, Exception):
            raise item
        return item
