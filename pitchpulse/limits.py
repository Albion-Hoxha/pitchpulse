"""Small in-memory limits that keep a public demo from burning the Azure credit.

shortcut: per-process memory, so limits reset on restart and are per replica; move to Redis or
Azure API Management if the app ever runs more than one replica.
"""
import time
from collections import defaultdict, deque


class RateLimiter:
    """Allow at most `limit` hits per `window` seconds for each key."""

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.hits = defaultdict(deque)

    def allow(self, key, cost=1, now=None):
        now = time.monotonic() if now is None else now
        hits = self.hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) + cost > self.limit:
            return False
        hits.extend([now] * cost)
        return True


def client_ip(request):
    """The viewer's IP; Azure Container Apps' ingress puts it first in X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
