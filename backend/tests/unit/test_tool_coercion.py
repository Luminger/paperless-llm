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


def test_int_list_rejects_garbage():
    with pytest.raises(ValueError):
        _int_list("one,two")
