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
  const storedZone = localStorage.getItem(TZ_KEY);
  return {
    // Legacy "system" (device-locale) collapses to ISO — concrete
    // formats only, now that prefs are server-shared.
    date: storedDate === "eu" || storedDate === "us" ? storedDate : "iso",
    time: (["24h", "24h-seconds", "12h", "12h-seconds"] as const).includes(
      localStorage.getItem(TIME_KEY) as TimePref,
    )
      ? (localStorage.getItem(TIME_KEY) as TimePref)
      : "24h-seconds",
    // Concrete zones only; anything unset/legacy reads as the browser's
    // zone (and becomes explicit on the next save).
    timeZone:
      storedZone && storedZone !== "system"
        ? storedZone
        : Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

// ---- change subscription (AUDIT FP-M1) --------------------------------
// Date formatting reads module state; components render its output.
// Anything that CHANGES the prefs bumps this store so subscribed
// consumers (the app root) re-render with fresh values.
let prefsVersion = 0;
const prefsListeners = new Set<() => void>();

function bumpPrefs(): void {
  prefsVersion += 1;
  for (const l of prefsListeners) l();
}

export function subscribeDateTimePrefs(listener: () => void): () => void {
  prefsListeners.add(listener);
  return () => prefsListeners.delete(listener);
}

export function dateTimePrefsVersion(): number {
  return prefsVersion;
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
  bumpPrefs();
}

export function setDateTimePrefs(
  date: DatePref,
  time: TimePref,
  timeZone: string = getDateTimePrefs().timeZone,
): void {
  localStorage.setItem(DATE_KEY, date);
  localStorage.setItem(TIME_KEY, time);
  localStorage.setItem(TZ_KEY, timeZone);
  bumpPrefs();
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
    // AUDIT FP-M2: a zone this browser's ICU doesn't know must not
    // white-screen every page that shows a date — fall back to UTC.
    let tz: { timeZone: string } | undefined = { timeZone: prefs.timeZone };
    try {
      new Intl.DateTimeFormat(undefined, tz);
    } catch {
      tz = { timeZone: "UTC" };
    }
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

/** "812" / "1.2k" / "34k" / "1.2M" — compact counts for stat cards
 * and token totals. */
export function formatCompact(n: number | null | undefined): string {
  if (n == null) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(0)}k`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
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

/** What "use this browser's settings" would set: its IANA zone, the
 * locale's day/month/year order, and its 12/24-hour convention. */
export function browserDateTimeDefaults(): {
  timeZone: string;
  date: DatePref;
  time: TimePref;
} {
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const parts = new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(2001, 11, 31));
  const order = parts
    .filter((p) => p.type === "year" || p.type === "month" || p.type === "day")
    .map((p) => p.type)
    .join("-");
  const date: DatePref = order.startsWith("year")
    ? "iso"
    : order.startsWith("month")
      ? "us"
      : "eu";
  const hour12 =
    new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions()
      .hour12 ?? false;
  return { timeZone, date, time: hour12 ? "12h" : "24h" };
}


/** PURE example formatting for candidate prefs (AUDIT FP-L8): builds
 * throwaway formatters instead of round-tripping through the global
 * store during render (which, with the change subscription, would
 * re-render the whole app once per ticking second). */
export function formatWithPrefs(
  prefs: { date: DatePref; time: TimePref; timeZone: string },
  iso: string,
  kind: "date" | "clock",
): string {
  let tz: { timeZone: string } = { timeZone: prefs.timeZone };
  try {
    new Intl.DateTimeFormat(undefined, tz);
  } catch {
    tz = { timeZone: "UTC" };
  }
  const d = new Date(iso);
  if (kind === "clock") {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      ...(prefs.time.endsWith("seconds") ? { second: "2-digit" } : {}),
      hourCycle: prefs.time.startsWith("12h") ? "h12" : "h23",
      ...tz,
    }).format(d);
  }
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...tz,
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const [y, m, day] = [get("year"), get("month"), get("day")];
  switch (prefs.date) {
    case "eu":
      return `${day}.${m}.${y}`;
    case "us":
      return `${m}/${day}/${y}`;
    default:
      return `${y}-${m}-${day}`;
  }
}
