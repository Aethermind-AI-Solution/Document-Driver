from app.security import SlidingWindowRateLimiter


def test_rate_limiter_allows_then_blocks():
    rl = SlidingWindowRateLimiter()
    assert rl.allow("ip", 100.0, 2, 60) is True
    assert rl.allow("ip", 100.5, 2, 60) is True
    assert rl.allow("ip", 101.0, 2, 60) is False


def test_rate_limiter_recovers_after_window():
    rl = SlidingWindowRateLimiter()
    assert rl.allow("ip", 100.0, 1, 60) is True
    assert rl.allow("ip", 130.0, 1, 60) is False   # still inside the window
    assert rl.allow("ip", 200.0, 1, 60) is True    # window elapsed


def test_rate_limiter_per_key_isolation():
    rl = SlidingWindowRateLimiter()
    assert rl.allow("a", 100.0, 1, 60) is True
    assert rl.allow("b", 100.0, 1, 60) is True      # different key unaffected
