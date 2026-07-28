"""Per-entity agent instructions: the inbox-default seeding contract.

The subtle rule worth pinning: clearing an instruction leaves an EMPTY
row behind, and the inbox default only seeds entities that never had a
row — so a deliberately cleared inbox instruction stays cleared across
restarts instead of resurrecting."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.instructions import (
    INBOX_DEFAULT,
    ensure_inbox_defaults,
    get_map,
    set_instructions,
)


def _tag(tag_id: int, inbox: bool = False):
    return SimpleNamespace(id=tag_id, is_inbox_tag=inbox)


async def test_set_insert_then_update_and_map_filters_empties(db):
    await set_instructions(db, "tag", 1, "Nur Steuerpost.")
    await set_instructions(db, "tag", 2, "Werbung.")
    await set_instructions(db, "tag", 2, "")  # cleared
    assert await get_map(db, "tag") == {1: "Nur Steuerpost."}
    # Different entity types are separate namespaces.
    assert await get_map(db, "correspondent") == {}
    await set_instructions(db, "tag", 1, "Updated.")
    assert (await get_map(db, "tag"))[1] == "Updated."


async def test_inbox_default_seeds_only_inbox_tags_without_a_row(db):
    await ensure_inbox_defaults(db, [_tag(1, inbox=True), _tag(2)])
    m = await get_map(db, "tag")
    assert m == {1: INBOX_DEFAULT}  # non-inbox tag untouched


async def test_cleared_inbox_instruction_never_resurrects(db):
    """The user cleared the seeded default: the empty row must block
    re-seeding on the next tag listing."""
    await ensure_inbox_defaults(db, [_tag(1, inbox=True)])
    await set_instructions(db, "tag", 1, "")
    await ensure_inbox_defaults(db, [_tag(1, inbox=True)])
    assert await get_map(db, "tag") == {}


async def test_no_inbox_tags_is_a_noop(db):
    await ensure_inbox_defaults(db, [_tag(2), _tag(3)])
    assert await get_map(db, "tag") == {}
