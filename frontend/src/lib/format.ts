// The one place dates and numbers become text.
//
// Rendering is a USER PREFERENCE (Settings → Date & time), stored
// locally like the theme: date style, time style, and TIMEZONE. The
// backend guarantees every timestamp is explicit UTC; formatters here
// convert into the chosen zone.

export type DatePref = "iso" | "eu" | "us";
export type TimePref = "24h" | "24h-seconds" | "12h" | "12h-seconds";

const DATE_KEY = "pllm.pref.dateFormat";
const TIME_KEY = "pllm.pref.timeFormat";
const TZ_KEY = "pllm.pref.timeZone";

// Concrete formats only: preferences are stored on the server and
// shared across browsers, so "whatever this device's locale does"
// would render differently everywhere — the opposite of a preference.
export const DATE_PREFS: { value: DatePref; label: string }[] = [
  { value: "iso", label: "Year-Month-Day" },
  { value: "eu", label: "Day.Month.Year" },
  { value: "us", label: "Month/Day/Year" },
];

export const TIME_PREFS: { value: TimePref; label: string }[] = [
  { value: "24h", label: "24-hour (18:49)" },
  { value: "24h-seconds", label: "24-hour with seconds (18:49:15)" },
  { value: "12h", label: "12-hour (6:49 PM)" },
  { value: "12h-seconds", label: "12-hour with seconds (6:49:15 PM)" },
];

/** "GMT+02:00" for a zone right now (offsets shift with DST). */
export function gmtOffset(zone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en", {
      timeZone: zone,
      timeZoneName: "longOffset",
    }).formatToParts(new Date());
    const name = parts.find((p) => p.type === "timeZoneName")?.value ?? "";
    return name === "GMT" ? "GMT+00:00" : name;
  } catch {
    return "";
  }
}

/** Every IANA zone as "(GMT+02:00) Europe/Berlin", sorted by offset
 * then name — the usual OS timezone picker shape. */
export function timeZoneOptions(): { value: string; label: string }[] {
  let zones: string[];
  try {
    zones = Intl.supportedValuesOf("timeZone");
  } catch {
    zones = ["UTC"];
  }
  const toMinutes = (off: string) => {
    const m = /GMT([+-])(\d{2}):(\d{2})/.exec(off);
    return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
  };
  return zones
    .map((z) => {
      const off = gmtOffset(z);
      return { value: z, label: `(${off}) ${z.replaceAll("_", " ")}`, sort: toMinutes(off) };
    })
    .sort((a, b) => a.sort - b.sort || a.value.localeCompare(b.value))
    .map(({ value, label }) => ({ value, label }));
}

export function getDateTimePrefs(): {
  date: DatePref;
  time: TimePref;
  timeZone: string; // "system" or an IANA zone
} {
  const storedDate = localStorage.getItem(DATE_KEY);
  return {
    // Legacy "system" (device-locale) collapses to ISO — concrete
    // formats only, now that prefs are server-shared.
    date: storedDate === "eu" || storedDate === "us" ? storedDate : "iso",
    time: (localStorage.getItem(TIME_KEY) as TimePref) || "24h-seconds",
    timeZone: localStorage.getItem(TZ_KEY) || "system",
  };
}

/** Seed the local cache from the server-side preferences. */
export function hydrateDateTimePrefs(server: {
  date_format: string;
  time_format: string;
  time_zone: string;
}): void {
  localStorage.setItem(DATE_KEY, server.date_format);
  localStorage.setItem(TIME_KEY, server.time_format);
  localStorage.setItem(TZ_KEY, server.time_zone);
}

export function setDateTimePrefs(
  date: DatePref,
  time: TimePref,
  timeZone: string = getDateTimePrefs().timeZone,
): void {
  localStorage.setItem(DATE_KEY, date);
  localStorage.setItem(TIME_KEY, time);
  localStorage.setItem(TZ_KEY, timeZone);
}

// Formatter cache, invalidated when prefs change.
let cacheKey = "";
let timeFmt: Intl.DateTimeFormat;
let partsFmt: Intl.DateTimeFormat;

function ensureFormatters(): ReturnType<typeof getDateTimePrefs> {
  const prefs = getDateTimePrefs();
  const key = `${prefs.date}|${prefs.time}|${prefs.timeZone}`;
  if (key !== cacheKey) {
    cacheKey = key;
    const tz =
      prefs.timeZone === "system" ? undefined : { timeZone: prefs.timeZone };
    timeFmt = new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      ...(prefs.time.endsWith("seconds") ? { second: "2-digit" } : {}),
      hourCycle: prefs.time.startsWith("12h") ? "h12" : "h23",
      ...tz,
    });
    // Numeric parts in the target zone, for the manual date styles.
    partsFmt = new Intl.DateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      ...tz,
    });
  }
  return prefs;
}

/** Y/M/D of the instant, evaluated in the user's chosen timezone. */
function ymdInZone(d: Date): { y: string; m: string; day: string } {
  const parts = partsFmt.formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return { y: get("year"), m: get("month"), day: get("day") };
}

/** Plain dates ("2014-07-11", no time) are calendar dates, not
 * instants — never timezone-shift them. */
function isPlainDate(iso: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(iso);
}

/** "Mar 7, 2026" / "2026-03-07" / … — per user preference. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const prefs = ensureFormatters();
  const plain = isPlainDate(iso.slice(0, 10)) && iso.length <= 10;
  const { y, m, day } = plain
    ? { y: iso.slice(0, 4), m: iso.slice(5, 7), day: iso.slice(8, 10) }
    : ymdInZone(d);
  switch (prefs.date) {
    case "eu":
      return `${day}.${m}.${y}`;
    case "us":
      return `${m}/${day}/${y}`;
    default:
      return `${y}-${m}-${day}`;
  }
}

/** Time of day per user preference, in the chosen timezone. */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  ensureFormatters();
  return timeFmt.format(d);
}

/** Date + time, both per user preference. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${formatDate(iso)}, ${formatClock(iso)}`;
}

/** "3 min ago" / "2 h ago" — freshness labels. */
export function formatAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 10) return "just now";
  if (s < 60) return `${Math.floor(s)} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

const MATCHING_LABELS: Record<number, string> = {
  0: "none",
  1: "any word",
  2: "all words",
  3: "exact match",
  4: "regex",
  5: "fuzzy",
  6: "auto (ML)",
};

/** Human form of a paperless matching rule. Returns null when matching
 * is effectively off (none, or a pattern algorithm without a pattern —
 * paperless's inert API default). */
export function matchingRule(e: {
  match?: string;
  matching_algorithm?: number;
  is_insensitive?: boolean;
}): string | null {
  const algo = e.matching_algorithm ?? 0;
  if (algo === 6) return MATCHING_LABELS[6];
  if (algo === 0 || !e.match) return null;
  const label = MATCHING_LABELS[algo] ?? `algorithm ${algo}`;
  const cased = e.is_insensitive === false ? " (case-sensitive)" : "";
  return `${label} · “${e.match}”${cased}`;
}
