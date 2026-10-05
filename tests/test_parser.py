from tools.parser import LogParser


def test_parser():
    sample = '''File "/workspace/sample_repo/math_service.py", line 10
IndexError: list index out of range'''
    res = LogParser.parse_traceback(sample)
    assert res["error_type"] == "IndexError"
    assert "math_service.py" in res["faulty_files"][0]


def test_pytest_format(sample_log):
    res = LogParser.parse_traceback(sample_log)
    assert res["error_type"] == "IndexError"
    assert res["error_message"] == "list index out of range"
    assert res["failing_tests"] == ["tests/test_math.py::test_empty"]
    assert "math_service.py" in res["faulty_files"]
    frame = LogParser.innermost_app_frame(res["frames"])
    assert frame["file"] == "math_service.py" and frame["line"] == 10
    assert "calculate_moving_average" in res["functions"]


def test_classic_traceback_frames():
    log = '''Traceback (most recent call last):
  File "/ci/tests/test_a.py", line 3, in test_a
    f()
  File "/ci/app/core.py", line 42, in f
    return d["k"]
KeyError: 'k'
'''
    res = LogParser.parse_traceback(log)
    assert res["error_type"] == "KeyError"
    assert res["lines"] == [3, 42]
    assert res["functions"] == ["test_a", "f"]
    assert LogParser.innermost_app_frame(res["frames"])["file"] == "/ci/app/core.py"


def test_dotted_exception_and_unknown():
    assert LogParser.parse_traceback("E   json.decoder.JSONDecodeError: bad")["error_type"] == "JSONDecodeError"
    res = LogParser.parse_traceback("nothing useful here")
    assert res["error_type"] == "UnknownError" and res["frames"] == []
    assert LogParser.innermost_app_frame([]) == {}
