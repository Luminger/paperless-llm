// Reference chips: the agent writes [[type:id]] tokens in its prose;
// the UI expands them into interactive coins — name resolved, details
// on hover, click navigates.

import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import { formatDate } from "../../lib/format";
import { proposalKindLabel } from "../../components/ProposalCard";
import { entityHref } from "../../pages/EntityPage";

export const REF_TOKEN_RE =
  /\[\[(document|tag|correspondent|document_type|storage_path|proposal):(\d+)\]\]/g;

/** Rewrites [[type:id]] tokens into markdown links with a pllm://
 * scheme, which the markdown renderer maps onto RefChip. */
export function tokenizeRefs(md: string): string {
  return md.replace(REF_TOKEN_RE, (_m, type, id) => `[${type}](pllm://${type}/${id})`);
}

const LISTERS: Record<string, (() => Promise<{ id: number; name: string; document_count?: number | null }[]>) | undefined> = {
  tag: api.listTags,
  correspondent: api.listCorrespondents,
  document_type: api.listDocumentTypes,
  storage_path: api.listStoragePaths,
};

function Chip({
  to,
  label,
  tooltip,
}: {
  to: string;
  label: React.ReactNode;
  tooltip: React.ReactNode;
}) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Link to={to} className="mx-0.5 inline-flex align-baseline no-underline">
            <Badge
              variant="secondary"
              className="cursor-pointer px-1.5 py-0 font-normal text-primary transition-colors hover:bg-primary/15"
            >
              {label}
            </Badge>
          </Link>
        </TooltipTrigger>
        <TooltipContent>{tooltip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function TaxonomyChip({ type, id }: { type: string; id: number }) {
  const { data } = useQuery({
    queryKey: keys.entities(type),
    queryFn: LISTERS[type]!,
  });
  const entity = data?.find((e) => e.id === id);
  const typeLabel = type.replaceAll("_", " ");
  return (
    <Chip
      to={entityHref(type, id)}
      label={entity?.name ?? (data ? "(unknown)" : "…")}
      tooltip={
        <span className="capitalize">
          {typeLabel}
          {entity?.document_count != null && ` · ${entity.document_count} documents`}
        </span>
      }
    />
  );
}

function DocumentChip({ id }: { id: number }) {
  const { data } = useQuery({
    queryKey: keys.document(id),
    queryFn: () => api.getDocument(id),
  });
  return (
    <Chip
      to={entityHref("document", id)}
      label={data ? data.title || "(untitled)" : "…"}
      tooltip={<span>Document{data?.created && ` · created ${formatDate(data.created)}`}</span>}
    />
  );
}

function ProposalChip({ id }: { id: number }) {
  const { data } = useQuery({
    queryKey: ["proposal", id],
    queryFn: () => api.getProposal(id),
  });
  return (
    <Chip
      to={`/proposals/${id}`}
      label={data ? `proposal: ${proposalKindLabel(data)}` : "…"}
      tooltip={
        <span>
          Proposal · {data ? `${proposalKindLabel(data)} · ${data.status.replaceAll("_", " ")}` : "loading"}
        </span>
      }
    />
  );
}

export function RefChip({ type, id }: { type: string; id: number }) {
  if (type === "document") return <DocumentChip id={id} />;
  if (type === "proposal") return <ProposalChip id={id} />;
  if (LISTERS[type]) return <TaxonomyChip type={type} id={id} />;
  return <span>{`[[${type}:${id}]]`}</span>;
}

/** react-markdown `a` override: pllm:// links become chips, everything
 * else stays a normal (external-safe) link. */
export function MarkdownLink({
  href,
  children,
  ...rest
}: React.ComponentProps<"a">) {
  const m = href?.match(/^pllm:\/\/([a-z_]+)\/(\d+)$/);
  if (m) return <RefChip type={m[1]} id={Number(m[2])} />;
  return (
    <a href={href} target="_blank" rel="noreferrer" {...rest}>
      {children}
    </a>
  );
}
