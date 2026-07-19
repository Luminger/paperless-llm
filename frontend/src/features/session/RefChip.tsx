// Reference chips: the agent writes [[type:id]] tokens in its prose;
// the UI expands them into interactive coins — name resolved, details
// on hover, click navigates.

import { Link } from "react-router-dom";
import {
  entityName,
  isTaxonomyType,
  useEntityList,
  type TaxonomyType,
} from "../../hooks/useTaxonomy";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import { formatDate } from "../../lib/format";
import { proposalKindLabel } from "../../lib/proposal-payload";
import { entityHref } from "../../pages/EntityPage";

export const REF_TOKEN_RE =
  /\[\[(document|tag|correspondent|document_type|storage_path|proposal):(\d+)\]\]/g;

/** Remark plugin: rewrites [[type:id]] tokens into pllm:// links —
 * operating on the mdast TEXT nodes only, so tokens the model quotes
 * inside code spans or fences stay literal (AUDIT FS-9; the old
 * string-level rewrite ran before parsing and mangled code). */
export function remarkRefs() {
  interface Node {
    type: string;
    value?: string;
    url?: string;
    children?: Node[];
  }
  const rewrite = (node: Node): void => {
    if (!node.children) return;
    const out: Node[] = [];
    for (const child of node.children) {
      if (child.type === "code" || child.type === "inlineCode") {
        out.push(child);
        continue;
      }
      // Never rewrite inside links (reinspection): a token in link text
      // would become a chip-link nested in an <a> — invalid interactive
      // nesting with undefined click behavior.
      if (child.type === "link" || child.type === "linkReference") {
        out.push(child);
        continue;
      }
      if (child.type !== "text" || !child.value) {
        rewrite(child);
        out.push(child);
        continue;
      }
      const re = new RegExp(REF_TOKEN_RE.source, "g");
      let last = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(child.value)) !== null) {
        if (m.index > last) out.push({ type: "text", value: child.value.slice(last, m.index) });
        out.push({
          type: "link",
          url: `pllm://${m[1]}/${m[2]}`,
          children: [{ type: "text", value: m[1] }],
        });
        last = m.index + m[0].length;
      }
      if (last === 0) {
        out.push(child); // no tokens — untouched
      } else if (last < child.value.length) {
        out.push({ type: "text", value: child.value.slice(last) });
      }
    }
    node.children = out;
  };
  return (tree: unknown) => rewrite(tree as Node);
}

function Chip({
  to,
  label,
  tooltip,
}: {
  to: string;
  label: React.ReactNode;
  tooltip: React.ReactNode;
}) {
  // The app-level TooltipProvider (main.tsx) covers chips too.
  return (
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
  );
}

function TaxonomyChip({ type, id }: { type: string; id: number }) {
  const { data } = useEntityList(type as TaxonomyType);
  const entity = data?.find((e) => e.id === id);
  const typeLabel = type.replaceAll("_", " ");
  return (
    <Chip
      to={entityHref(type, id)}
      label={entityName(data, id)}
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
    queryKey: keys.proposal(id),
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
  if (isTaxonomyType(type)) return <TaxonomyChip type={type} id={id} />;
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
