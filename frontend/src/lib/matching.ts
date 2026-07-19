// THE matching-rule vocabulary. Proposals, the taxonomy list and the
// entity pages all speak it — one set of names, one set of field
// labels ("Auto-assignment", "Match pattern", "Case"), so a rule reads
// the same wherever it appears.

/** Canonical short names per paperless matching_algorithm. */
export const MATCHING_NAMES: Record<number, string> = {
  0: "None",
  1: "Any word",
  2: "All words",
  3: "Exact match",
  4: "Regular expression",
  5: "Fuzzy word",
  6: "Automatic",
};

/** Algorithms that need a `match` pattern (1..5); 0 and 6 don't. */
export const PATTERN_ALGORITHMS = new Set([1, 2, 3, 4, 5]);

export const matchingName = (n: number | null | undefined): string =>
  n == null ? "—" : (MATCHING_NAMES[n] ?? `algorithm ${n}`);

/** The descriptive form the proposal editor's select uses — same names,
 * plus what the mode means for the user. */
export const matchingDescription = (n: number): string => {
  if (n === 6) return "Automatic (learns from your decisions)";
  if (n === 0) return "None (no automatic assignment)";
  return `${matchingName(n)} — needs a pattern`;
};

/** Select options for rule editors. */
export const MATCHING_OPTIONS = Object.keys(MATCHING_NAMES).map((v) => ({
  value: v,
  label: matchingDescription(Number(v)),
}));

/** One-line summary for table cells: `Any word · “Telarko” · match
 * case`. Returns null when matching is effectively off (none, or a
 * pattern mode without a pattern — paperless's inert API default). */
export function matchingSummary(e: {
  match?: string;
  matching_algorithm?: number;
  is_insensitive?: boolean;
}): string | null {
  const algo = e.matching_algorithm ?? 0;
  if (algo === 6) return MATCHING_NAMES[6];
  if (algo === 0 || !e.match) return null;
  const cased = e.is_insensitive === false ? " · match case" : "";
  return `${matchingName(algo)} · “${e.match}”${cased}`;
}
