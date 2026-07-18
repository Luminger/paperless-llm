"""Tolerant int-list coercion for tool args — mitigates serving-stack
tool parsers that mangle JSON arrays (observed live: qwen3_xml +
Qwen3.6-27b falls back to comma-separated strings)."""

from __future__ import annotations

import pytest

from app.agents.tools import _int_list


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ([], []),
        ([1, 2], [1, 2]),
        (3, [3]),
        ("1,2,3", [1, 2, 3]),
        (" 1 , 2 ", [1, 2]),
        ("[4, 5]", [4, 5]),
        ("7", [7]),
        ("1;2", [1, 2]),
        ("", []),
    ],
)
def test_int_list(value, expected):
    assert _int_list(value) == expected


def test_int_list_rejects_garbage_as_model_retry():
    """AUDIT BC-F4: garbage is a ModelRetry (self-correctable in-turn),
    never a bare ValueError that fails the step deterministically."""
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry, match="list of integer ids"):
        _int_list("one,two")
    with pytest.raises(ModelRetry):
        _int_list("1, 2 and 5")
