from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from .config import Settings


def generate_token(prefix: str = "tsp") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ip_digest(ip: str, salt: str) -> str:
    return hmac.new(salt.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()


def extract_client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in settings.trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            if candidate:
                return candidate[:64]
    return peer[:64]


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    return value


def require_admin(request: Request, settings: Settings) -> None:
    supplied = bearer_token(request)
    if not hmac.compare_digest(supplied, settings.admin_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid admin token")


class SlidingWindowLimiter:
    def __init__(self, limit: int = 240, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            queue = self._events[key]
            while queue and queue[0] < cutoff:
                queue.popleft()
            if len(queue) >= self.limit:
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            queue.append(now)
