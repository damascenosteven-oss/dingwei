from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response


SENSITIVE_ROUTES = frozenset(
    {
        ("POST", "/api/v1/bootstrap"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/accept-invitation"),
        ("POST", "/api/v1/device/enroll"),
    }
)


@dataclass
class Bucket:
    window: int
    requests: int


class FixedWindowRateLimiter:
    def __init__(self, request_limit: int, window_seconds: int) -> None:
        self.request_limit = request_limit
        self.window_seconds = window_seconds
        self._buckets: dict[tuple[str, str, str], Bucket] = {}
        self._lock = threading.Lock()

    def check(self, client: str, method: str, path: str) -> tuple[bool, int]:
        now = int(time.time())
        window = now // self.window_seconds
        key = (client, method, path)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or bucket.window != window:
                self._buckets[key] = Bucket(window=window, requests=1)
                self._discard_expired(window)
                return True, 0
            if bucket.requests >= self.request_limit:
                retry_after = self.window_seconds - (now % self.window_seconds)
                return False, max(1, retry_after)
            bucket.requests += 1
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def _discard_expired(self, current_window: int) -> None:
        if len(self._buckets) < 10_000:
            return
        self._buckets = {
            key: bucket
            for key, bucket in self._buckets.items()
            if bucket.window >= current_window - 1
        }
        if len(self._buckets) >= 10_000:
            self._buckets.pop(next(iter(self._buckets)))


class SensitiveEndpointRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        limiter: FixedWindowRateLimiter,
    ) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        route = (request.method, request.url.path)
        if route in SENSITIVE_ROUTES:
            client = request.client.host if request.client else "unknown"
            allowed, retry_after = self.limiter.check(client, *route)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁，请稍后重试"},
                    headers={
                        "Retry-After": str(retry_after),
                        "Cache-Control": "no-store",
                    },
                )
        return await call_next(request)
