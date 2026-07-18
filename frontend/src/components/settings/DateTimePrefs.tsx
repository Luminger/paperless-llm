// Date & time — the usual OS-style picker: concrete formats with live
// examples, and a timezone list labeled with GMT offsets. Stored on
// the server (shared workspace), cached locally for instant rendering.

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MonitorSmartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import {
  DATE_PREFS,
  TIME_PREFS,
  browserDateTimeDefaults,
  formatClock,
  formatDate,
  formatDateTime,
  getDateTimePrefs,
  setDateTimePrefs,
  timeZoneOptions,
  type DatePref,
  type TimePref,
} from "../../lib/format";

/** Format NOW per candidate pref, for live example labels. */
function exampleFor(kind: "date" | "time", value: string): string {
  const current = getDateTimePrefs();
  try {
    if (kind === "date") {
      setDateTimePrefs(value as DatePref, current.time, current.timeZone);
      return formatDate(new Date().toISOString());
    }
    setDateTimePrefs(current.date, value as TimePref, current.timeZone);
    return formatClock(new Date().toISOString());
  } finally {
    setDateTimePrefs(current.date, current.time, current.timeZone);
  }
}

export function DateTimePrefs() {
  const qc = useQueryClient();
  const [prefs, setPrefs] = useState(getDateTimePrefs);
  const save = useMutation({
    mutationFn: (p: { date: DatePref; time: TimePref; timeZone: string }) =>
      api.putPrefs({
        date_format: p.date,
        time_format: p.time,
        time_zone: p.timeZone,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.prefs() }),
  });
  const update = (date: DatePref, time: TimePref, timeZone: string) => {
    setDateTimePrefs(date, time, timeZone); // instant, local cache
    setPrefs({ date, time, timeZone });
    save.mutate({ date, time, timeZone }); // persisted server-side
  };
  const zones = useMemo(timeZoneOptions, []);
  const browser = browserDateTimeDefaults();
  const matchesBrowser =
    prefs.timeZone === browser.timeZone &&
    prefs.date === browser.date &&
    // Seconds are a taste on top of the 12/24 convention — the browser
    // can only speak to the convention.
    prefs.time.startsWith(browser.time);
  // The preview is a clock, not a snapshot.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Date &amp; time</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-[11rem_1fr] items-center gap-3">
          <Label className="font-normal text-muted-foreground">Time zone</Label>
          <SimpleSelect
            ariaLabel="timezone"
            value={prefs.timeZone}
            onValueChange={(v) => update(prefs.date, prefs.time, v)}
            options={zones}
          />
          <Label className="font-normal text-muted-foreground">Date format</Label>
          <SimpleSelect
            ariaLabel="date format"
            value={prefs.date}
            onValueChange={(v) => update(v as DatePref, prefs.time, prefs.timeZone)}
            options={DATE_PREFS.map((o) => ({
              value: o.value,
              label: `${o.label} — ${exampleFor("date", o.value)}`,
            }))}
          />
          <Label className="font-normal text-muted-foreground">Time format</Label>
          <SimpleSelect
            ariaLabel="time format"
            value={prefs.time}
            onValueChange={(v) => update(prefs.date, v as TimePref, prefs.timeZone)}
            options={TIME_PREFS.map((o) => ({
              value: o.value,
              label: `${o.label.split(" (")[0]} — ${exampleFor("time", o.value)}`,
            }))}
          />
        </div>
        <div className="flex items-center justify-between gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={matchesBrowser}
            title={
              matchesBrowser
                ? "Already matching this browser's settings"
                : `Sets ${browser.timeZone}, ${browser.date === "iso" ? "Year-Month-Day" : browser.date === "eu" ? "Day.Month.Year" : "Month/Day/Year"}, ${browser.time === "12h" ? "12-hour" : "24-hour"}`
            }
            onClick={() => update(browser.date, browser.time, browser.timeZone)}
          >
            <MonitorSmartphone className="size-3.5" />
            Use this browser's settings
          </Button>
        </div>
        <div className="rounded-lg border bg-muted/40 p-3 text-sm">
          <span className="text-muted-foreground">Right now: </span>
          {formatDateTime(now.toISOString())}
        </div>
        <p className="text-xs text-muted-foreground/70">
          Saved on the server — every browser and device shows the same
          formats. Timestamps themselves are always stored in UTC.
        </p>
        {save.error && (
          <p className="text-xs text-destructive">
            could not save to the server — the setting applies locally for now
          </p>
        )}
      </CardContent>
    </Card>
  );
}
