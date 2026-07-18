import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import {
  DATE_PREFS,
  TIME_PREFS,
  formatDateTime,
  getDateTimePrefs,
  setDateTimePrefs,
  timeZoneOptions,
  type DatePref,
  type TimePref,
} from "../../lib/format";

/** Server-persisted preference (localStorage is only a warm cache):
 * how dates and times render everywhere in the app. */
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
    // The prefs cache feeds other consumers (PromptTuning shares the
    // same PrefsOut) — keep it honest.
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.prefs() }),
  });
  const update = (date: DatePref, time: TimePref, timeZone: string) => {
    setDateTimePrefs(date, time, timeZone);   // instant, local cache
    setPrefs({ date, time, timeZone });
    save.mutate({ date, time, timeZone });    // persisted server-side
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Date &amp; time</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-[11rem_1fr] items-center gap-3">
          <Label className="font-normal text-muted-foreground">Date format</Label>
          <SimpleSelect
            ariaLabel="date format"
            value={prefs.date}
            onValueChange={(v) => update(v as DatePref, prefs.time, prefs.timeZone)}
            options={DATE_PREFS.map((o) => ({ value: o.value, label: o.label }))}
          />
          <Label className="font-normal text-muted-foreground">Time format</Label>
          <SimpleSelect
            ariaLabel="time format"
            value={prefs.time}
            onValueChange={(v) => update(prefs.date, v as TimePref, prefs.timeZone)}
            options={TIME_PREFS.map((o) => ({ value: o.value, label: o.label }))}
          />
          <Label className="font-normal text-muted-foreground">Timezone</Label>
          <SimpleSelect
            ariaLabel="timezone"
            value={prefs.timeZone}
            onValueChange={(v) => update(prefs.date, prefs.time, v)}
            options={[
              { value: "system", label: "System timezone" },
              ...timeZoneOptions().map((z) => ({ value: z, label: z })),
            ]}
          />
        </div>
        <p className="text-xs text-muted-foreground/70">
          Preview: {formatDateTime(new Date().toISOString())}. Saved on the
          server — every browser and device shows the same formats.
          Timestamps themselves are always stored in UTC.
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

