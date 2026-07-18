// Server-persisted user preferences: fetched at startup and hydrated
// into the module-level formatter cache, so the experience is
// consistent across browsers. localStorage bridges the first paint —
// the app NEVER blocks on this fetch; when the server copy lands a
// context tick re-renders consumers with the fresh values.

import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../api";
import { hydrateDateTimePrefs } from "./format";

const PrefsTick = createContext(0);

export function PrefsProvider({ children }: { children: React.ReactNode }) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    api
      .getPrefs()
      .then((p) => {
        hydrateDateTimePrefs(p);
        setTick((t) => t + 1);
      })
      .catch(() => {
        /* offline/startup race — the local cache still applies */
      });
  }, []);
  return <PrefsTick.Provider value={tick}>{children}</PrefsTick.Provider>;
}

/** Consumers that render formatted dates re-render when server prefs
 * arrive. */
export function usePrefsTick() {
  return useContext(PrefsTick);
}
