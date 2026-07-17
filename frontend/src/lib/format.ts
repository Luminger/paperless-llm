// The one place dates and numbers become text.
//
// Date and time rendering is a USER PREFERENCE (Settings → Date & time),
// stored locally like the theme. Formatters are rebuilt when the
// preference changes.

export type DatePref = "system" | "iso" | "eu" | "us";
export type TimePref = "24h" | "24h-seconds" | "12h" | "12h-seconds";

const DATE_KEY = "pllm.pref.dateFormat";
const TIME_KEY = "pllm.pref.timeFormat";

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

export function getDateTimePrefs(): { date: DatePref; time: TimePref } {
  return {
    date: (localStorage.getItem(DATE_KEY) as DatePref) || "system",
    time: (localStorage.getItem(TIME_KEY) as TimePref) || "24h-seconds",
  };
}

export function setDateTimePrefs(date: DatePref, time: TimePref): void {
  localStorage.setItem(DATE_KEY, date);
  localStorage.setItem(TIME_KEY, time);
}

// Formatter cache, invalidated when prefs change.
let cacheKey = "";
let dateFmt: Intl.DateTimeFormat;
let timeFmt: Intl.DateTimeFormat;

function ensureFormatters(): { date: DatePref; time: TimePref } {
  const prefs = getDateTimePrefs();
  const key = `${prefs.date}|${prefs.time}`;
  if (key !== cacheKey) {
    cacheKey = key;
    dateFmt = new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
    timeFmt = new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      ...(prefs.time.endsWith("seconds") ? { second: "2-digit" } : {}),
      hourCycle: prefs.time.startsWith("12h") ? "h12" : "h23",
    });
  }
  return prefs;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "Mar 7, 2026" / "2026-03-07" / … — per user preference. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const prefs = ensureFormatters();
  switch (prefs.date) {
    case "iso":
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    case "eu":
      return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
    case "us":
      return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()}`;
    default:
      return dateFmt.format(d);
  }
}

/** Time of day per user preference. */
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
