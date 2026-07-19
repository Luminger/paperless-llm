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

# ---------------------------------------------------------------------
# Custom-field coercion (the registry-aware half of the modeling)
# ---------------------------------------------------------------------

from app.agents.tools import _coerce_custom_value
from app.paperless.schemas import CustomField


def _field(dt: str, **extra) -> CustomField:
    return CustomField(id=3, name="Invoice date", data_type=dt, extra_data=extra)


def test_custom_value_coercions():
    assert _coerce_custom_value(_field("boolean"), "true") is True
    assert _coerce_custom_value(_field("integer"), "42") == 42
    assert _coerce_custom_value(_field("float"), "1.5") == 1.5
    assert _coerce_custom_value(_field("date"), "2024-05-01T00:00:00") == "2024-05-01"
    assert _coerce_custom_value(_field("string"), "hello") == "hello"
    assert _coerce_custom_value(_field("documentlink"), "1,2") == [1, 2]


def test_custom_value_select_accepts_id_or_label():
    f = _field(
        "select",
        select_options=[{"id": "abc", "label": "Paid"}, {"id": "def", "label": "Open"}],
    )
    assert _coerce_custom_value(f, "abc") == "abc"
    assert _coerce_custom_value(f, "Open") == "def"


def test_custom_value_rejections_are_model_retries():
    import pytest
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry, match="not a boolean"):
        _coerce_custom_value(_field("boolean"), "yes")
    with pytest.raises(ModelRetry, match="Invalid value"):
        _coerce_custom_value(_field("integer"), "a lot")
    with pytest.raises(ModelRetry, match="not an option"):
        _coerce_custom_value(
            _field("select", select_options=[{"id": "abc", "label": "Paid"}]),
            "Overdue",
        )
    with pytest.raises(ModelRetry, match="Invalid value"):
        _coerce_custom_value(_field("date"), "yesterday")
