import pytest
from fastapi import HTTPException, Request
from app import config
from app.security import require_access, SlidingWindowRateLimiter


def _request(path):
    return Request({"type": "http", "http_version": "1.1", "method": "GET",
                    "path": path, "raw_path": path.encode(), "headers": [],
                    "query_string": b"", "scheme": "http", "server": ("test", 80),
                    "client": ("1.2.3.4", 1234)})


def test_gate_disabled_allows(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "")
    require_access(_request("/documents"), None)  # no exception


def test_gate_blocks_without_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    with pytest.raises(HTTPException) as e:
        require_access(_request("/documents"), None)
    assert e.value.status_code == 401


def test_gate_blocks_wrong_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    with pytest.raises(HTTPException):
        require_access(_request("/documents"), "nope")


def test_gate_allows_correct_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    require_access(_request("/documents"), "secret")  # no exception


def test_gate_allows_health_without_token(monkeypatch):
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "secret")
    require_access(_request("/health"), None)  # no exception


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
