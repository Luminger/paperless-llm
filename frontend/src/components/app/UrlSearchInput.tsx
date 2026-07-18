// THE realtime search box: debounced into the URL, and — crucially —
// reconciled FROM the URL. One-directional local state resurrects a
// cleared query after a nav-click to the bare route (AUDIT FP-H2);
// this component treats the URL as the source of truth and its local
// state as an edit buffer.

import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { useUrlParam, useUrlPatch } from "../../hooks/useUrlState";

export function UrlSearchInput({
  param = "q",
  placeholder,
  ariaLabel,
  className,
  resetKeys = ["page"],
}: {
  param?: string;
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
  /** Params cleared when the query changes (page reset). */
  resetKeys?: string[];
}) {
  const [submitted] = useUrlParam(param);
  const patchUrl = useUrlPatch();
  const [value, setValue] = useState(submitted);
  const lastSubmitted = useRef(submitted);

  // External URL change (nav-click, reset button, back/forward): the
  // URL wins over whatever the buffer holds.
  useEffect(() => {
    if (submitted !== lastSubmitted.current) {
      lastSubmitted.current = submitted;
      setValue(submitted);
    }
  }, [submitted]);

  // Debounced write — no Search button.
  useEffect(() => {
    if (value === submitted) return;
    const t = setTimeout(() => {
      lastSubmitted.current = value;
      patchUrl({
        [param]: value || null,
        ...Object.fromEntries(resetKeys.map((k) => [k, null])),
      });
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, submitted, param, patchUrl]);

  return (
    <Input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className={className}
    />
  );
}
