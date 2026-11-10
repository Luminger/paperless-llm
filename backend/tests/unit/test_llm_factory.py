"""Sampling levers -> wire format. The anti-repetition knobs exist to
tune away VLM OCR loops (a hard page makes the model emit the same
lines until the output limit) without code changes."""

import asyncio

import pytest

from app.config import SamplingOverrides, get_settings, reset_settings_cache
from app.llm.factory import _settings_from, embeddings_semaphore, llm_semaphore


def test_standard_sampling_maps_to_native_fields():
    s = SamplingOverrides(
        temperature=0.2,
        top_p=0.9,
        max_tokens=3000,
        presence_penalty=1.5,
        frequency_penalty=0.4,
    )
    out = _settings_from(s, "server_default", 60)
    # ModelSettings is a TypedDict of NotRequired keys: read with .get().
    assert out.get("temperature") == 0.2
    assert out.get("top_p") == 0.9
    assert out.get("max_tokens") == 3000
    assert out.get("presence_penalty") == 1.5
    assert out.get("frequency_penalty") == 0.4
    assert "extra_body" not in out


def test_server_specific_levers_travel_via_extra_body():
    s = SamplingOverrides(repetition_penalty=1.08, top_k=40, min_p=0.05)
    out = _settings_from(s, "off", 60)
    # Merged WITH the thinking toggle - both live in extra_body.
    assert out.get("extra_body") == {
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
    assert embeddings_semaphore() is llm_semaphore(agent.base_url, agent.max_concurrent)

    # A dedicated embeddings endpoint gets its own cap.
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__BASE_URL", "http://tei:8080/v1")
    reset_settings_cache()
    assert embeddings_semaphore() is llm_semaphore("http://tei:8080/v1", 7)
    reset_settings_cache()


# ----- endpoint admission: reentrancy + a bounded wait -----------------

ADMISSION_URL = "http://gpu:9100/v1"


@pytest.fixture
def shared_endpoint(monkeypatch):
    """The deployed shape: agent and embeddings share ONE endpoint and
    the agent admits a single request at a time."""
    from app.llm import factory

    monkeypatch.setenv("PLLM_LLM__AGENT__BASE_URL", ADMISSION_URL)
    monkeypatch.setenv("PLLM_LLM__AGENT__MAX_CONCURRENT", "1")
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__BASE_URL", ADMISSION_URL)
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__MODEL", "bge-m3")
    reset_settings_cache()
    monkeypatch.setattr(factory, "_semaphores", {})
    yield
    reset_settings_cache()


async def test_endpoint_admission_is_reentrant_for_the_holding_task(shared_endpoint):
    """The runner holds the endpoint permit for the WHOLE agent.run, so a
    tool executed inside the turn may need the same endpoint (embeddings
    admission resolves to the agent's semaphore). asyncio.Semaphore is not
    reentrant: the turn then waits on a permit only it can release, and no
    timeout covers the acquire -> the step is parked in 'running' forever
    (incident: tag 'Rente' session, llm.agent.max_concurrent=1)."""
    agent = get_settings().llm.agent
    sem = llm_semaphore(agent.base_url, agent.max_concurrent)

    async with asyncio.timeout(5):
        async with llm_semaphore(agent.base_url, agent.max_concurrent):
            async with embeddings_semaphore():
                pass
            async with embeddings_semaphore():
                pass

    # Every level unwound: the permit is free again.
    assert not sem.locked()


async def test_endpoint_admission_still_queues_independent_work(shared_endpoint):
    """Reentrancy is scoped to the CONTEXT holding the permit — a turn plus
    the tasks it spawns to run tool calls (pydantic-ai runs each graph step
    in a child task). Independent work must still queue, otherwise the
    shared-endpoint cap would be meaningless."""
    agent = get_settings().llm.agent
    entered = asyncio.Event()
    go = asyncio.Event()

    async def other_turn() -> None:
        # Another worker's turn: started BEFORE this context holds anything.
        await go.wait()
        async with embeddings_semaphore():
            entered.set()

    task = asyncio.create_task(other_turn())
    async with llm_semaphore(agent.base_url, agent.max_concurrent):
        go.set()
        await asyncio.sleep(0.05)
        assert not task.done()
        assert not entered.is_set()

    async with asyncio.timeout(5):
        await task
    assert entered.is_set()


async def test_admission_wait_is_bounded(shared_endpoint, monkeypatch):
    """Safety net: an acquire that cannot be satisfied inside the budget
    must fail the caller (-> the step records an error) instead of
    silently parking it in 'running' forever. The abandoned waiter must
    not leak the permit or a wake-up."""
    from app.llm import factory
    from app.llm.factory import AdmissionTimeout

    monkeypatch.setenv("PLLM_LLM__ADMISSION_TIMEOUT_SECONDS", "0.05")
    reset_settings_cache()
    monkeypatch.setattr(factory, "_semaphores", {})
    agent = get_settings().llm.agent
    entered = asyncio.Event()
    go = asyncio.Event()

    async def other_turn() -> None:
        await go.wait()
        async with embeddings_semaphore():
            entered.set()

    task = asyncio.create_task(other_turn())
    async with llm_semaphore(agent.base_url, agent.max_concurrent):
        go.set()
        with pytest.raises(AdmissionTimeout):
            await task
        assert not entered.is_set()

    sem = llm_semaphore(agent.base_url, agent.max_concurrent)
    assert not sem.locked()
    # And it is usable again immediately (no phantom waiter ahead of us).
    async with asyncio.timeout(5):
        async with sem:
            pass
    assert not sem.locked()
