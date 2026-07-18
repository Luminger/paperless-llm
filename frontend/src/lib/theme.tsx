// Light/dark theme, defaulting to the system preference.
// The resolved theme is applied as a `dark` class on <html> (shadcn's
// class-based custom variant).

import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";
const STORAGE_KEY = "pllm.theme";

const ThemeContext = createContext<{
  theme: Theme;
  /** Resolved: is dark mode active right now (incl. system)? */
  dark: boolean;
  setTheme: (t: Theme) => void;
}>({ theme: "system", dark: false, setTheme: () => {} });

function resolveDark(theme: Theme): boolean {
  return (
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches)
  );
}

function apply(theme: Theme): boolean {
  const dark = resolveDark(theme);
  document.documentElement.classList.toggle("dark", dark);
  return dark;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem(STORAGE_KEY) as Theme) || "system",
  );
  const [dark, setDark] = useState(() => resolveDark(theme));

  useEffect(() => {
    setDark(apply(theme));
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setDark(apply("system"));
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = (t: Theme) => {
    localStorage.setItem(STORAGE_KEY, t);
    setThemeState(t);
  };

  return (
    <ThemeContext.Provider value={{ theme, dark, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
