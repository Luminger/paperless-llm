from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_paperless
from app.api.schemas import ProposalOut, ProposalPatch
from app.db.models import AppliedChange, Proposal, ProposalStatus
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.proposals import ApplyError, apply_proposal, revert_change, validate_payload

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def _out(p: Proposal) -> ProposalOut:
    out = ProposalOut.model_validate(p)
    if p.applied_change:
        out.applied = True
        out.reverted = p.applied_change.reverted_at is not None
    return out


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
    db: AsyncSession = Depends(get_session),
) -> list[ProposalOut]:
    q = (
        select(Proposal)
        .options(selectinload(Proposal.applied_change))
        .order_by(Proposal.created_at.desc())
        .limit(500)
    )
    if status:
        q = q.where(Proposal.status == status)
    if session_id:
        q = q.where(Proposal.session_id == session_id)
    return [_out(p) for p in (await db.scalars(q)).all()]


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
    if p.status not in (ProposalStatus.pending, ProposalStatus.approved):
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


@router.post("/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: int, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    p = await _load(db, proposal_id)
    if p.status != ProposalStatus.pending:
        raise HTTPException(409, f"proposal is {p.status}")
    p.status = ProposalStatus.approved
    await db.commit()
    return _out(p)


@router.post("/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    p = await _load(db, proposal_id)
    if p.status not in (ProposalStatus.pending, ProposalStatus.approved):
        raise HTTPException(409, f"proposal is {p.status}")
    p.status = ProposalStatus.rejected
    await db.commit()
    return _out(p)


@router.post("/{proposal_id}/apply")
async def apply(
    proposal_id: int,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> ProposalOut:
    p = await _load(db, proposal_id)
    try:
        await apply_proposal(paperless, db, p)
    except ApplyError as e:
        raise HTTPException(409, str(e)) from e
    # The freshly committed AppliedChange isn't in the identity map's
    # cached relationship state (expire_on_commit=False); expire so the
    # reload sees it.
    db.expire_all()
    return _out(await _load(db, proposal_id))


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
    try:
        await revert_change(paperless, db, change)
    except ApplyError as e:
        raise HTTPException(409, str(e)) from e
    return _out(await _load(db, proposal_id))
