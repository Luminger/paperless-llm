from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_paperless
from app.api.pagination import count_of, paginate
from app.api.presenters import proposal_out as _out
from app.api.schemas import (
    ProposalOut,
    ProposalPage,
    ProposalPatch,
    RevertCheckOut,
)
from app.db.models import AppliedChange, Proposal, ProposalStatus
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.proposals import apply_proposal, revert_change, validate_payload

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


async def _load(db: AsyncSession, proposal_id: int) -> Proposal:
    p = await db.scalar(
        select(Proposal)
        .where(Proposal.id == proposal_id)
        .options(selectinload(Proposal.applied_change))
    )
    if p is None:
        raise HTTPException(404, "proposal not found")
    return p


@router.get("")
async def list_proposals(
    status: ProposalStatus | None = None,
    session_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_session),
) -> ProposalPage:
    where = []
    if status:
        where.append(Proposal.status == status)
    if session_id:
        where.append(Proposal.session_id == session_id)
    q = (
        select(Proposal)
        .where(*where)
        .options(selectinload(Proposal.applied_change))
        .order_by(Proposal.created_at.desc())
    )
    win, q = await paginate(
        db, q, count_of(Proposal, *where), page=page, page_size=page_size
    )
    return ProposalPage(
        count=win.count,
        page=win.page,
        page_size=win.page_size,
        results=[_out(p) for p in (await db.scalars(q)).all()],
    )


@router.get("/{proposal_id}")
async def get_proposal(
    proposal_id: int, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    return _out(await _load(db, proposal_id))


@router.patch("/{proposal_id}")
async def patch_proposal(
    proposal_id: int, body: ProposalPatch, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    """Set/replace the user-edited payload. The body is the FULL
    replacement payload (not a merge — so agent-proposed fields can be
    dropped); `kind` is enforced server-side. The agent original stays
    immutable; pass user_payload=null to discard edits."""
    p = await _load(db, proposal_id)
    if p.status != ProposalStatus.pending:
        raise HTTPException(409, f"proposal is {p.status}; cannot edit")
    if body.user_payload is not None:
        candidate = {**body.user_payload, "kind": p.kind}
        try:
            validate_payload(candidate)
        except ValidationError as e:
            raise HTTPException(422, f"invalid payload: {e}") from e
        p.user_payload = candidate
    else:
        p.user_payload = None
    await db.commit()
    return _out(p)


@router.post("/{proposal_id}/apply")
async def apply(
    proposal_id: int,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> ProposalOut:
    p = await _load(db, proposal_id)
    # Archived sessions never forward-apply; their journal only reverts.
    from app.db.models import Session

    session = await db.get(Session, p.session_id)
    if session is not None and session.archived_at is not None:
        raise HTTPException(
            409, "session is archived: its proposals cannot be applied "
            "(unarchive the session first); applied changes remain revertible"
        )
    await apply_proposal(paperless, db, p)
    # The decision loop: the session continues on its own, telling the
    # agent what the user decided (incl. their edited values).
    from app.services.pipeline import continue_after_decision

    if session is not None:
        await continue_after_decision(db, session, p)
    # The freshly committed AppliedChange isn't in the identity map's
    # cached relationship state (expire_on_commit=False); expire so the
    # reload sees it.
    db.expire_all()
    return _out(await _load(db, proposal_id))


@router.get("/{proposal_id}/revert-check")
async def revert_check(
    proposal_id: int,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> RevertCheckOut:
    """Would reverting this applied proposal change anything? Drives the
    greyed-out Revert button (noop reverts are refused server-side too)."""
    p = await _load(db, proposal_id)
    change = await db.scalar(
        select(AppliedChange)
        .where(AppliedChange.proposal_id == p.id)
        .options(selectinload(AppliedChange.proposal))
    )
    if change is None or change.reverted_at is not None:
        raise HTTPException(409, "proposal has no revertible change")
    from app.proposals.apply import revert_is_noop

    return RevertCheckOut(revert_noop=await revert_is_noop(paperless, p, change))


@router.post("/{proposal_id}/revert")
async def revert(
    proposal_id: int,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> ProposalOut:
    p = await _load(db, proposal_id)
    change = await db.scalar(
        select(AppliedChange)
        .where(AppliedChange.proposal_id == p.id)
        .options(selectinload(AppliedChange.proposal))
    )
    if change is None:
        raise HTTPException(409, "proposal was never applied")
    await revert_change(paperless, db, change)
    return _out(await _load(db, proposal_id))
