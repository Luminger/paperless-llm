// Auth shell: /api/auth/me decides what the user sees. Mode `none`
// and a valid proxy/cookie identity render the app; paperless mode
// without a session renders the login page. Any 401 from the API
// re-checks (the request wrapper dispatches pllm:unauthorized).

import { createContext, useContext, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuthMe } from "../api";
import { keys } from "./keys";
import { LoadingState } from "@/components/app/states";
import Login from "../pages/Login";

const AuthContext = createContext<AuthMe>({ mode: "none", user: null });

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
  if (me.user == null) {
    if (me.mode === "paperless") return <Login />;
    if (me.mode === "proxy") {
      return (
        <div className="mx-auto max-w-md px-4 py-16 text-sm text-muted-foreground">
          <h1 className="mb-2 text-lg font-semibold text-foreground">
            Not signed in
          </h1>
          <p>
            This instance trusts an authenticating reverse proxy, but no
            identity header arrived. Access it through the proxy.
          </p>
        </div>
      );
    }
  }
  return <AuthContext.Provider value={me}>{children}</AuthContext.Provider>;
}
