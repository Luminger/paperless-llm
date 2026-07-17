import { Navigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { errorMessage } from "../lib/errors";

/** Proposals are reviewed on their session's timeline; deep links to
 * /proposals/:id resolve to the owning session. */
export default function ProposalRedirect() {
  const { id } = useParams();
  const { data: p, error } = useQuery({
    queryKey: ["proposal", Number(id)],
    queryFn: () => api.getProposal(Number(id)),
  });
  if (error) return <p className="text-red-600">{errorMessage(error)}</p>;
  if (!p) return <p className="text-zinc-500">Loading…</p>;
  return <Navigate to={`/sessions/${p.session_id}`} replace />;
}
