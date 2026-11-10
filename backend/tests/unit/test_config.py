from __future__ import annotations

import textwrap
from pathlib import Path

from app.config import Settings, reset_settings_cache

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


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


def test_empty_env_value_is_unset(monkeypatch, tmp_path):
    """Compose files export optional vars as ${VAR:-} (an EMPTY string);
    those must neither override lower layers with "" nor count as
    env-provided (which would lock the key in the Settings UI)."""
    from app.config import env_provided_keys

    s = _settings(
        monkeypatch,
        tmp_path,
        """
        [llm.agent]
        model = "from-toml"
        """,
        env={"PLLM_LLM__AGENT__MODEL": "", "PLLM_WEBHOOK__SECRET": ""},
    )
    assert s.llm.agent.model == "from-toml"
    assert s.webhook.secret == ""
    assert env_provided_keys() & {"llm.agent.model", "webhook.secret"} == set()
    # A non-empty value still provides (and locks) the key.
    monkeypatch.setenv("PLLM_LLM__AGENT__MODEL", "from-env")
    reset_settings_cache()
    assert Settings().llm.agent.model == "from-env"
    assert "llm.agent.model" in env_provided_keys()
    reset_settings_cache()


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


def test_install_layout_defaults_to_the_checkout(monkeypatch, tmp_path):
    """root_dir is the backend directory (alembic tree), and
    frontend_dist its sibling — the container/source layout."""
    s = _settings(monkeypatch, tmp_path)
    assert s.root_dir == BACKEND_DIR
    assert (s.root_dir / "alembic.ini").is_file()
    assert (s.root_dir / "alembic").is_dir()
    assert s.frontend_dist == BACKEND_DIR.parent / "frontend" / "dist"


def test_root_dir_relocates_both_paths(monkeypatch, tmp_path):
    """PLLM_ROOT_DIR alone moves root_dir AND frontend_dist — one
    variable for a package that installs the tree elsewhere."""
    root = tmp_path / "lib" / "paperless-llm"
    s = _settings(monkeypatch, tmp_path, env={"PLLM_ROOT_DIR": str(root)})
    assert s.root_dir == root
    assert s.frontend_dist == root.parent / "frontend" / "dist"


def test_frontend_dist_override(monkeypatch, tmp_path):
    root = tmp_path / "root"
    dist = tmp_path / "share" / "paperless-llm" / "dist"
    s = _settings(
        monkeypatch,
        tmp_path,
        env={"PLLM_ROOT_DIR": str(root), "PLLM_FRONTEND_DIST": str(dist)},
    )
    assert s.root_dir == root
    assert s.frontend_dist == dist
