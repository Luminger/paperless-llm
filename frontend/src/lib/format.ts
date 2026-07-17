// The one place dates and numbers become text.
//
// Rendering is a USER PREFERENCE (Settings → Date & time), stored
// locally like the theme: date style, time style, and TIMEZONE. The
// backend guarantees every timestamp is explicit UTC; formatters here
// convert into the chosen zone.

export type DatePref = "system" | "iso" | "eu" | "us";
export type TimePref = "24h" | "24h-seconds" | "12h" | "12h-seconds";

const DATE_KEY = "pllm.pref.dateFormat";
const TIME_KEY = "pllm.pref.timeFormat";
const TZ_KEY = "pllm.pref.timeZone";

export const DATE_PREFS: { value: DatePref; label: string }[] = [
  { value: "system", label: "System locale" },
  { value: "iso", label: "ISO 8601 (2026-07-17)" },
  { value: "eu", label: "European (17.07.2026)" },
  { value: "us", label: "US (07/17/2026)" },
];

export const TIME_PREFS: { value: TimePref; label: string }[] = [
  { value: "24h", label: "24-hour (18:49)" },
  { value: "24h-seconds", label: "24-hour with seconds (18:49:15)" },
  { value: "12h", label: "12-hour (6:49 PM)" },
  { value: "12h-seconds", label: "12-hour with seconds (6:49:15 PM)" },
];

export function timeZoneOptions(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return ["UTC"];
  }
}

export function getDateTimePrefs(): {
  date: DatePref;
  time: TimePref;
  timeZone: string; // "system" or an IANA zone
} {
  return {
    date: (localStorage.getItem(DATE_KEY) as DatePref) || "system",
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
let dateFmt: Intl.DateTimeFormat;
let timeFmt: Intl.DateTimeFormat;
let partsFmt: Intl.DateTimeFormat;

function ensureFormatters(): ReturnType<typeof getDateTimePrefs> {
  const prefs = getDateTimePrefs();
  const key = `${prefs.date}|${prefs.time}|${prefs.timeZone}`;
  if (key !== cacheKey) {
    cacheKey = key;
    const tz =
      prefs.timeZone === "system" ? undefined : { timeZone: prefs.timeZone };
    dateFmt = new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      ...tz,
    });
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
    case "iso":
      return `${y}-${m}-${day}`;
    case "eu":
      return `${day}.${m}.${y}`;
    case "us":
      return `${m}/${day}/${y}`;
    default:
      return plain
        ? dateFmt.format(new Date(Number(y), Number(m) - 1, Number(day)))
        : dateFmt.format(d);
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
