// Auth shell: /api/auth/me decides what the user sees. A valid cookie
// session renders the app; anything else renders the login page
// (credentials are paperless credentials - ONE auth story). Any 401
// from the API re-checks (the request wrapper dispatches
// pllm:unauthorized).

import { createContext, useContext, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuthMe } from "../api";
import { keys } from "./keys";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { Button } from "@/components/ui/button";
import Login from "../pages/Login";

const AuthContext = createContext<AuthMe>({ user: null, role: "user" });

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const { data: me, error, refetch } = useQuery({
    queryKey: keys.auth(),
    queryFn: api.getAuthMe,
    staleTime: Infinity,
    retry: 1,
  });

  useEffect(() => {
    const onUnauthorized = () =>
      qc.invalidateQueries({ queryKey: keys.auth() });
    window.addEventListener("pllm:unauthorized", onUnauthorized);
    return () => window.removeEventListener("pllm:unauthorized", onUnauthorized);
  }, [qc]);

  // AUDIT FP-M5: an HTTP-level failure (backend 500) must not be an
  // eternal skeleton — network outages are ConnectionToast's job, but
  // this one needs its own retry affordance.
  if (!me && error) {
    return (
      <div className="mx-auto max-w-xl space-y-3 px-4 py-16">
        <ErrorNotice error={error} />
        <Button size="sm" variant="secondary" onClick={() => refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (!me) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6">
        <LoadingState lines={4} />
      </div>
    );
  }
  if (me.user == null) return <Login />;
  return <AuthContext.Provider value={me}>{children}</AuthContext.Provider>;
}
