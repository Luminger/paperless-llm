// Pure derivation of what a turn shows (AUDIT FS-C2): ONE place decides
// streaming-vs-finished — which proposals belong to the turn, which
// items feed the timeline, which prose is the closing summary. The
// streaming and finished paths used to diverge inside TurnBody's JSX;
// now a divergence is a visible branch in a unit-testable function.

import type { Proposal, Step, TranscriptItem } from "../../api";
import type { LiveActivity } from "../../hooks/useSessionEvents";
import { isInternalKind } from "../../lib/proposal-kinds";

export interface TurnView {
  /** Live rendering: items come from the SSE reducer, proposals are
   * matched by step_id (result.proposal_ids exists only afterwards). */
  streaming: boolean;
  /** True while a failed attempt waits for its scheduled retry — NOT
   * streaming (there is a transcript worth showing, and no live data
   * will arrive until the retry runs). AUDIT FS-8. */
  retryScheduled: boolean;
  items: TranscriptItem[];
  mine: Proposal[];
  /** Index of the closing prose in `items`; -1 while streaming. */
  summaryIdx: number;
}

export function stepProposals(step: Step, proposals: Proposal[]): Proposal[] {
  const ids = (step.result.proposal_ids as number[] | undefined) ?? [];
  const byId = new Map(proposals.map((p) => [p.id, p]));
  return ids
    .map((id) => byId.get(id))
    .filter((p): p is Proposal => p != null && !isInternalKind(p.kind));
}

export function deriveTurnView(
  step: Step,
  proposals: Proposal[],
  live: LiveActivity | undefined,
): TurnView {
  const retryScheduled =
    step.state === "pending" && step.scheduled_at != null;
  const streaming =
    !retryScheduled && (step.state === "running" || step.state === "pending");
  const mine = streaming
    ? proposals.filter((p) => p.step_id === step.id && !isInternalKind(p.kind))
    : stepProposals(step, proposals);
  // A retry-scheduled step renders the failed attempt's transcript (if
  // any) rather than an empty "working…" pulse.
  const items = streaming ? (live?.items ?? []) : step.transcript;

  // The summary is the LAST agent prose — but only once the turn is
  // done; mid-stream prose is just the growing tail of the work.
  let summaryIdx = -1;
  if (!streaming) {
    for (let i = items.length - 1; i >= 0; i--) {
      if (items[i].role === "agent") {
        summaryIdx = i;
        break;
      }
    }
  }
  return { streaming, retryScheduled, items, mine, summaryIdx };
}
