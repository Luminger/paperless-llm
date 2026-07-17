// Server-persisted user preferences: fetched once at startup and
// hydrated into the local cache, so the experience is consistent
// across browsers. localStorage only bridges the first paint.

import { useEffect, useState } from "react";
import { api } from "../api";
import { hydrateDateTimePrefs } from "./format";

export function PrefsProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    api
      .getPrefs()
      .then((p) => hydrateDateTimePrefs(p))
      .catch(() => {
        /* offline/startup race — the local cache still applies */
      })
      .finally(() => setReady(true));
  }, []);
  if (!ready) return null;
  return <>{children}</>;
}
