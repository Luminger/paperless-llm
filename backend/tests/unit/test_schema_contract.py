"""Structural wire-contract guards over app.api.schemas."""

from __future__ import annotations

import inspect
import typing
from datetime import datetime

from pydantic import BaseModel, PlainSerializer

import app.api.schemas as schemas


def _naked_datetime(hint: object) -> bool:
    """True when a plain `datetime` hides in this annotation without a
    UTC-forcing PlainSerializer wrapper."""
    if hasattr(hint, "__metadata__"):  # Annotated[...]
        base = typing.get_args(hint)[0]
        if base is datetime and any(
            isinstance(m, PlainSerializer) for m in hint.__metadata__
        ):
            return False
        return _naked_datetime(base)
    if hint is datetime:
        return True
    return any(_naked_datetime(a) for a in typing.get_args(hint))


def test_every_wire_datetime_serializes_explicit_utc():
    """All timestamps UTC on the wire — structurally. SQLite hands back
    NAIVE datetimes; a bare `datetime` field serializes them without an
    offset and browsers misread them as local time. Every datetime in a
    response schema must be UtcDateTime (found the hard way twice)."""
    offenders: list[str] = []
    for name, obj in vars(schemas).items():
        if (
            inspect.isclass(obj)
            and issubclass(obj, BaseModel)
            and obj.__module__ == schemas.__name__
        ):
            hints = typing.get_type_hints(obj, include_extras=True)
            offenders.extend(
                f"{name}.{field}"
                for field, hint in hints.items()
                if _naked_datetime(hint)
            )
    assert not offenders, (
        f"bare datetime fields on the wire (use UtcDateTime): {offenders}"
    )
