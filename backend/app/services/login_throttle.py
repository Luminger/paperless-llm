"""In-process login throttle: exponential backoff on failed logins.

The login endpoint proxies credentials straight to paperless
(``POST /api/token/``) — without a brake, an exposed instance is a
brute-force oracle against paperless accounts. The app is deliberately
single-process (see DESIGN.md), so a plain module-level dict is the
whole store: no redis, no DB table, counters reset on restart (an
attacker forcing restarts has bigger levers than this throttle).

Keyed on the COMBINED (username, client-ip) pair: a per-username key
alone would let any remote stranger lock the real owner out by
spraying wrong passwords at their account (lockout DoS), while a
per-IP key alone would throttle every user behind one NAT/proxy for a
single bad actor. The combined key stops the common case — one origin
hammering one account — without either failure mode. A distributed
attack rotating IPs is beyond any in-process throttle; that is what
fail2ban / an edge rate limiter are for.

Shape: the first ``login_backoff_after`` failures are free (typo
budget), then each further failure doubles the required wait, capped
at ``login_backoff_cap_seconds``. A successful login — or a quiet
period of one cap-length — clears the key entirely.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from app.config import get_settings

# Test seam (monkeypatch target) and single clock source.
_monotonic = time.monotonic

# Hard bound on tracked keys — an attacker cycling usernames must not
# grow this without limit. Eviction drops the entries closest to
# expiry, i.e. the least dangerous to forget.
_MAX_ENTRIES = 10_000


@dataclass
class _Entry:
    failures: int = 0
    blocked_until: float = 0.0
    last_failure: float = field(default_factory=lambda: 0.0)


# (username lowercased, client ip) -> failure state.
_entries: dict[tuple[str, str], _Entry] = {}


def _key(username: str, ip: str) -> tuple[str, str]:
    return (username.strip().lower(), ip)


def _forget_window() -> float:
    """Idle time after which old failures are forgiven: one cap-length
    (never below a minute, so a tiny cap doesn't neuter the throttle)."""
    return max(float(get_settings().auth.login_backoff_cap_seconds), 60.0)


def _prune(now: float) -> None:
    forget = _forget_window()
    stale = [
        k for k, e in _entries.items()
        if now - e.last_failure > forget and e.blocked_until <= now
    ]
    for k in stale:
        _entries.pop(k, None)
    if len(_entries) > _MAX_ENTRIES:
        # Drop the soonest-to-expire entries first.
        for k, _ in sorted(_entries.items(), key=lambda kv: kv[1].blocked_until)[
            : len(_entries) - _MAX_ENTRIES
        ]:
            _entries.pop(k, None)


def retry_after(username: str, ip: str) -> int:
    """Seconds this (username, ip) pair must still wait; 0 = go ahead."""
    now = _monotonic()
    entry = _entries.get(_key(username, ip))
    if entry is None:
        return 0
    if now - entry.last_failure > _forget_window() and entry.blocked_until <= now:
        _entries.pop(_key(username, ip), None)
        return 0
    remaining = entry.blocked_until - now
    return math.ceil(remaining) if remaining > 0 else 0


def record_failure(username: str, ip: str) -> None:
    """Count a failed login; past the free budget the next attempt is
    delayed 2, 4, 8, … seconds up to the configured cap."""
    cfg = get_settings().auth
    now = _monotonic()
    _prune(now)
    entry = _entries.setdefault(_key(username, ip), _Entry())
    entry.failures += 1
    entry.last_failure = now
    over = entry.failures - cfg.login_backoff_after
    if over >= 0:
        delay = min(float(2 ** (over + 1)), float(cfg.login_backoff_cap_seconds))
        entry.blocked_until = now + delay


def record_success(username: str, ip: str) -> None:
    """A real login proves the credentials' owner is present — the
    slate is wiped for this key."""
    _entries.pop(_key(username, ip), None)


def reset() -> None:
    """Test isolation helper."""
    _entries.clear()
