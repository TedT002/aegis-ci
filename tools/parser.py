"""CI log ayrıştırıcı.

İki formatı destekler:
  * Klasik Python traceback:   File "/x/y.py", line 10, in func
  * Pytest (--tb=long/short):  path/y.py:10: IndexError
                              FAILED tests/test_x.py::test_y - IndexError: msg
"""
import re
from typing import Any, Dict, List

_EXC_LINE = re.compile(r"^(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning))(?::\s*(.*))?$")
_PY_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([\w<>]+))?')
_PYTEST_FRAME = re.compile(r"^([\w./\\-]+\.py):(\d+):(?:\s*(\w+))?\s*$")
_FAILED = re.compile(r"^FAILED\s+(\S+?)(?:\s+-\s+(.*))?$")
_DEF = re.compile(r"^\s*(?:>\s*)?def\s+(\w+)\s*\(")


class LogParser:
    @staticmethod
    def parse_traceback(log_text: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "error_type": "UnknownError",
            "error_message": "",
            "faulty_files": [],
            "lines": [],
            "frames": [],
            "functions": [],
            "failing_tests": [],
        }
        lines = log_text.splitlines()

        # 1) Hata tipi & mesajı: en son görülen exception satırı en derindeki hatadır.
        for raw in lines:
            line = raw.strip()
            m_failed = _FAILED.match(line)
            if m_failed:
                result["failing_tests"].append(m_failed.group(1))
                continue
            m = _EXC_LINE.match(line)
            if m:
                result["error_type"] = m.group(1).split(".")[-1]
                result["error_message"] = (m.group(2) or "").strip()
        if result["error_type"] == "UnknownError":
            m = re.search(r"([A-Za-z_]\w*(?:Error|Exception)):\s*(.*)", log_text)
            if m:
                result["error_type"], result["error_message"] = m.group(1), m.group(2).strip()

        # 2) Stack frame'leri (sırayla, en dıştan en içe)
        frames: List[Dict[str, Any]] = []
        last_def = None
        for raw in lines:
            m_def = _DEF.match(raw)
            if m_def:
                last_def = m_def.group(1)
            m = _PY_FRAME.search(raw)
            if m:
                frames.append({"file": m.group(1), "line": int(m.group(2)), "function": m.group(3)})
                continue
            m = _PYTEST_FRAME.match(raw.strip())
            if m:
                frames.append({"file": m.group(1), "line": int(m.group(2)), "function": last_def})
                last_def = None
        result["frames"] = frames

        seen = set()
        for fr in frames:
            if fr["file"] not in seen:
                seen.add(fr["file"])
                result["faulty_files"].append(fr["file"])
                result["lines"].append(fr["line"])
            if fr["function"] and not fr["function"].startswith("<") and fr["function"] not in result["functions"]:
                result["functions"].append(fr["function"])
        return result

    @staticmethod
    def innermost_app_frame(frames: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Test dosyası ve site-packages olmayan en içteki frame'i döndürür."""
        for fr in reversed(frames):
            path = fr["file"].replace("\\", "/")
            name = path.rsplit("/", 1)[-1]
            if "site-packages" in path or "/tests/" in f"/{path}" or name.startswith("test_"):
                continue
            return fr
        return frames[-1] if frames else {}
