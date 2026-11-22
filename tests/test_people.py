import pytest

from pyzkaccess_native import CategoryScheme, RepeatDetector


def test_category_scheme():
    s = CategoryScheme()
    assert s.user_id(12, 3) == 123 and s.split(123) == (12, 3)
    assert s.split(6) is None and s.split(1000) is None
    with pytest.raises(ValueError):
        s.user_id(100, 0)


def test_repeat_detector():
    now = [0.0]
    d = RepeatDetector(window=3, clock=lambda: now[0])
    assert d.check(100) is False
    now[0] = 1.5
    assert d.check(100) is True          # second presentation
    now[0] = 2.0
    assert d.check(100) is False         # a third one starts a new pair
    now[0] = 6.0
    assert d.check(100) is False         # too late
