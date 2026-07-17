from __future__ import annotations

import textwrap

from app.config import Settings, reset_settings_cache


def _settings(monkeypatch, tmp_path, toml: str = "", env: dict[str, str] | None = None):
    cfg = tmp_path / "paperless-llm.toml"
    cfg.write_text(textwrap.dedent(toml))
    monkeypatch.setenv("PAPERLESS_LLM_CONFIG", str(cfg))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    return Settings()


def test_defaults(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path)
    assert s.llm.agent.model == "qwen3.6-27b"
    assert s.llm.agent.supports_streaming is False
    assert s.llm.ocr.max_images_per_request == 2
    assert not s.llm.embeddings.enabled
    assert not s.llm.reranker.enabled


def test_toml_layer(monkeypatch, tmp_path):
    s = _settings(
        monkeypatch,
        tmp_path,
        """
        [llm.agent]
        base_url = "http://gpu-box:9000/v1"
        model = "some-other-model"
        max_concurrent = 4

        [llm.ocr]
        max_images_per_request = 1
        [llm.ocr.sampling]
        temperature = 0.0

        [paperless]
        base_url = "http://paperless:8000"
        token = "abc"
        """,
    )
    assert s.llm.agent.base_url == "http://gpu-box:9000/v1"
    assert s.llm.agent.max_concurrent == 4
    assert s.llm.ocr.max_images_per_request == 1
    assert s.llm.ocr.sampling.temperature == 0.0
    assert s.paperless.token == "abc"


def test_env_overrides_toml(monkeypatch, tmp_path):
    s = _settings(
        monkeypatch,
        tmp_path,
        """
        [llm.agent]
        model = "from-toml"
        """,
        env={"PLLM_LLM__AGENT__MODEL": "from-env"},
    )
    assert s.llm.agent.model == "from-env"


def test_ocr_fallback_to_agent(monkeypatch, tmp_path):
    _settings(
        monkeypatch,
        tmp_path,
        """
        [llm.agent]
        base_url = "http://only-endpoint:8001/v1"
        model = "the-model"
        """,
    )
    from app.config import get_settings
    from app.llm.factory import resolved_ocr_profile

    reset_settings_cache()
    assert get_settings().llm.agent.base_url == "http://only-endpoint:8001/v1"
    base_url, model, _, _ = resolved_ocr_profile()
    assert base_url == "http://only-endpoint:8001/v1"
    assert model == "the-model"


def test_embeddings_enabled_flag(monkeypatch, tmp_path):
    s = _settings(
        monkeypatch,
        tmp_path,
        """
        [llm.embeddings]
        base_url = "https://hyperion.example/v1"
        model = "qwen3-embedding-0.6b"
        """,
    )
    assert s.llm.embeddings.enabled
