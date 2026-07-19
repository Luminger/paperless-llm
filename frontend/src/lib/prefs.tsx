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

// ----- document panel side (left/right) -------------------------------
// Server-persisted like every pref; localStorage bridges the first
// paint so the panel doesn't jump sides after the fetch lands.

export type DocPanelSide = "left" | "right";
const SIDE_KEY = "pllm.pref.docPanelSide";

let panelSide: DocPanelSide =
  localStorage.getItem(SIDE_KEY) === "left" ? "left" : "right";
let sideVersion = 0;
const sideSubs = new Set<() => void>();

const setSideLocal = (side: DocPanelSide) => {
  if (side === panelSide) return;
  panelSide = side;
  localStorage.setItem(SIDE_KEY, side);
  sideVersion += 1;
  for (const fn of sideSubs) fn();
};

export function hydrateDocPanelSide(prefs: { doc_panel_side?: string }) {
  if (prefs.doc_panel_side === "left" || prefs.doc_panel_side === "right")
    setSideLocal(prefs.doc_panel_side);
}

/** Flip locally (instant) and persist server-side (fire-and-forget —
 * the local state is already right; other browsers catch up on their
 * next prefs fetch). */
export function setDocPanelSide(side: DocPanelSide) {
  setSideLocal(side);
  api.putPrefs({ doc_panel_side: side }).catch(() => {});
}

export function useDocPanelSide(): DocPanelSide {
  useSyncExternalStore(
    (fn) => {
      sideSubs.add(fn);
      return () => sideSubs.delete(fn);
    },
    () => sideVersion,
  );
  return panelSide;
}

export function PrefsProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    api
      .getPrefs()
      .then((p) => {
        hydrateDateTimePrefs(p);
        hydrateDocPanelSide(p);
      })
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
