// Auth shell: /api/auth/me decides what the user sees. A valid cookie
// session renders the app; anything else renders the login page
// (credentials are paperless credentials - ONE auth story). Any 401
// from the API re-checks (the request wrapper dispatches
// pllm:unauthorized).

import { createContext, useContext, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuthMe } from "../api";
import { keys } from "./keys";
import { LoadingState } from "@/components/app/states";
import Login from "../pages/Login";

const AuthContext = createContext<AuthMe>({ user: null });

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const { data: me } = useQuery({
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
