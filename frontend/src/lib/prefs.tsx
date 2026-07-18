// Server-persisted user preferences: fetched at startup and hydrated
// into the module-level formatter cache, so the experience is
// consistent across browsers. localStorage bridges the first paint —
// the app NEVER blocks on this fetch.
//
// AUDIT FP-M1: consumers subscribe via useSyncExternalStore — the old
// context "tick" had zero consumers and re-rendered nothing, so a
// hydrate (or a settings save in another view) left every mounted
// timestamp stale.

import { useEffect, useSyncExternalStore } from "react";
import { api } from "../api";
import {
  dateTimePrefsVersion,
  hydrateDateTimePrefs,
  subscribeDateTimePrefs,
} from "./format";

export function PrefsProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    api
      .getPrefs()
      .then(hydrateDateTimePrefs)
      .catch(() => {
        /* offline/startup race — the local cache still applies */
      });
  }, []);
  return children;
}

/** Re-render the calling component (typically the app root, taking the
 * whole tree with it — pref changes are rare) whenever date/time prefs
 * change. */
export function useDateTimePrefsSubscription(): number {
  return useSyncExternalStore(subscribeDateTimePrefs, dateTimePrefsVersion);
}
