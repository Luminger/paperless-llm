import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ErrorBoundary } from "./components/app/ErrorBoundary";
import { AuthProvider } from "./lib/auth";
import { ConnectivityProvider } from "./components/app/ConnectionToast";
import { PrefsProvider } from "./lib/prefs";
import { ThemeProvider } from "./lib/theme";
import { TooltipProvider } from "./components/ui/tooltip";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
    <ThemeProvider>
      {/* ONE tooltip provider (AUDIT UI-U4): 300ms initial delay, quick
          successive hovers via Radix's skip-delay window. */}
      <TooltipProvider delayDuration={300}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ConnectivityProvider>
            <AuthProvider>
              {/* Prefs are a signed-in concern: fetching them before the
                  session exists would just 401. */}
              <PrefsProvider>
                <App />
              </PrefsProvider>
            </AuthProvider>
          </ConnectivityProvider>
        </BrowserRouter>
      </QueryClientProvider>
      </TooltipProvider>
    </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
);
