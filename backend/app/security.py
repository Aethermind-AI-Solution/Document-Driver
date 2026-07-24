import secrets, time
from collections import defaultdict
from fastapi import Header, HTTPException, Request
from . import config


def require_access(request: Request, x_access_token: str | None = Header(default=None)):
    """Gate every route (except /health) behind a shared token when one is configured."""
    if not config.DEMO_ACCESS_TOKEN:
        return
    if request.url.path == "/health":
        return
    if not (x_access_token and secrets.compare_digest(x_access_token, config.DEMO_ACCESS_TOKEN)):
        raise HTTPException(401, "Invalid or missing access token")


class SlidingWindowRateLimiter:
    def __init__(self):
        self.hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str, now: float, max_requests: int, window: float) -> bool:
        recent = [t for t in self.hits[key] if t > now - window]
        if len(recent) >= max_requests:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        return True

    def reset(self):
        self.hits.clear()


rate_limiter = SlidingWindowRateLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request):
    if not rate_limiter.allow(_client_ip(request), time.time(),
                              config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW):
        raise HTTPException(429, "Too many requests, please slow down")
