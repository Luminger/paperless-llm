"""Sampling levers -> wire format. The anti-repetition knobs exist to
tune away VLM OCR loops (a hard page makes the model emit the same
lines until the output limit) without code changes."""

from app.config import SamplingOverrides, reset_settings_cache
from app.llm.factory import _settings_from, embeddings_semaphore, llm_semaphore


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


def test_embeddings_semaphore_shares_agent_endpoint_admission(monkeypatch):
    """Shared endpoint = shared admission (see ocr_model): embeddings
    pointed at the agent's URL must reuse the agent's semaphore instead
    of replacing it with a differently-sized one."""
    from app.config import get_settings

    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__BASE_URL", "http://gpu:9000/v1/")
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__MODEL", "embed")
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__MAX_CONCURRENT", "7")
    monkeypatch.setenv("PLLM_LLM__AGENT__BASE_URL", "http://gpu:9000/v1")
    reset_settings_cache()
    agent = get_settings().llm.agent
    assert embeddings_semaphore() is llm_semaphore(
        agent.base_url, agent.max_concurrent
    )

    # A dedicated embeddings endpoint gets its own cap.
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__BASE_URL", "http://tei:8080/v1")
    reset_settings_cache()
    assert embeddings_semaphore() is llm_semaphore("http://tei:8080/v1", 7)
    reset_settings_cache()
