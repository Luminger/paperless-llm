"""Sampling levers -> wire format. The anti-repetition knobs exist to
tune away VLM OCR loops (a hard page makes the model emit the same
lines until the output limit) without code changes."""

from app.config import SamplingOverrides
from app.llm.factory import _settings_from


def test_standard_sampling_maps_to_native_fields():
    s = SamplingOverrides(
        temperature=0.2, top_p=0.9, max_tokens=3000,
        presence_penalty=1.5, frequency_penalty=0.4,
    )
    out = _settings_from(s, "server_default", 60)
    assert out["temperature"] == 0.2
    assert out["top_p"] == 0.9
    assert out["max_tokens"] == 3000
    assert out["presence_penalty"] == 1.5
    assert out["frequency_penalty"] == 0.4
    assert "extra_body" not in out


def test_server_specific_levers_travel_via_extra_body():
    s = SamplingOverrides(repetition_penalty=1.08, top_k=40, min_p=0.05)
    out = _settings_from(s, "off", 60)
    # Merged WITH the thinking toggle - both live in extra_body.
    assert out["extra_body"] == {
        "repetition_penalty": 1.08,
        "top_k": 40,
        "min_p": 0.05,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_unset_sampling_sends_nothing():
    out = _settings_from(SamplingOverrides(), "server_default", None)
    assert out == {}
