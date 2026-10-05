from math_service import calculate_moving_average


def test_regular_case():
    assert calculate_moving_average([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [2.0, 3.0, 4.0]


def test_zero_window():
    assert calculate_moving_average([1.0, 2.0], 0) == []


def test_empty():
    assert calculate_moving_average([], 3) == []
