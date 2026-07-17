"""Who is acting right now — carried via contextvar so any depth of the
call stack (audit records, paperless traffic logging) can attribute its
work without threading parameters everywhere.

Values are namespaced strings so multi-user support later is a value
change, not a schema change: today "user" and "system"; later e.g.
"user:simon".
"""

from __future__ import annotations

from contextvars import ContextVar

# Background workers/pipelines run outside any request => "system".
actor_var: ContextVar[str] = ContextVar("actor", default="system")


def current_actor() -> str:
    return actor_var.get()
