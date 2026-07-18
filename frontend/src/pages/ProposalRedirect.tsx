import { Navigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { keys } from "../lib/keys";
import { ErrorNotice, LoadingState } from "@/components/app/states";

/** Proposals are reviewed on their session's timeline; deep links to
 * /proposals/:id resolve to the owning session. */
export default function ProposalRedirect() {
  const { id } = useParams();
  const { data: p, error } = useQuery({
    queryKey: keys.proposal(Number(id)),
    queryFn: () => api.getProposal(Number(id)),
  });
  if (error) return <ErrorNotice error={error} />;
  if (!p) return <LoadingState lines={2} />;
  return <Navigate to={`/sessions/${p.session_id}`} replace />;
}
