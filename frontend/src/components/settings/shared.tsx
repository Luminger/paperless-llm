// Shared bits of the settings surface.

import { Lock } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[11rem_1fr] gap-3 border-b border-border/50 py-1.5 text-sm last:border-b-0">
      <div className="text-muted-foreground">{label}</div>
      <div className="min-w-0 truncate font-mono text-xs leading-5">{children}</div>
    </div>
  );
}

export function OnOff({
  on,
  labels = ["enabled", "disabled"],
}: {
  on: boolean;
  labels?: [string, string];
}) {
  return (
    <Badge variant="secondary" className={on ? "text-primary" : "text-muted-foreground"}>
      {on ? labels[0] : labels[1]}
    </Badge>
  );
}

/** Where a config value comes from. Environment = locked; ui = an
 * override set right here; file/default stay quiet. */
export function SourceBadge({ source }: { source: string }) {
  if (source === "environment") {
    return (
      <Badge variant="secondary" className="gap-1 font-normal text-muted-foreground">
        <Lock className="size-3" /> environment
      </Badge>
    );
  }
  if (source === "ui") {
    return (
      <Badge variant="secondary" className="font-normal text-primary">
        set here
      </Badge>
    );
  }
  if (source === "file") {
    return (
      <Badge variant="secondary" className="font-normal text-muted-foreground">
        config file
      </Badge>
    );
  }
  return null;
}
