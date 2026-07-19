/* Settings → Sessions: the login sessions behind the cookies (AUDIT
 * API-F8, second half). Users see their own; admins see everyone's.
 * Revoking ends a session server-side — instantly, valid cookie
 * signature notwithstanding. The CURRENT session is not revocable
 * here (sign out instead — no one-click self-lockout). */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/app/ConfirmDialog";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type AuthSession } from "../../api";
import { keys } from "../../lib/keys";
import { useAuth } from "../../lib/auth";
import { formatAgo, formatDateTime } from "../../lib/format";

/** Best-effort "Firefox on Linux" from a user-agent string — a hint
 * for telling sessions apart, not forensics. */
export function describeAgent(ua: string): string {
  const browser = /firefox\//i.test(ua)
    ? "Firefox"
    : /edg(e|a|ios)?\//i.test(ua)
      ? "Edge"
      : /(opr|opera)\//i.test(ua)
        ? "Opera"
        : /chrom(e|ium)\//i.test(ua)
          ? "Chrome"
          : /safari\//i.test(ua)
            ? "Safari"
            : null;
  const os = /windows/i.test(ua)
    ? "Windows"
    : /android/i.test(ua)
      ? "Android"
      : /(iphone|ipad|ios)/i.test(ua)
        ? "iOS"
        : /mac os/i.test(ua)
          ? "macOS"
          : /linux/i.test(ua)
            ? "Linux"
            : null;
  if (browser && os) return `${browser} on ${os}`;
  if (browser || os) return browser ?? os!;
  return ua ? ua.slice(0, 60) : "Unknown client";
}

export function Sessions() {
  const qc = useQueryClient();
  const auth = useAuth();
  const [target, setTarget] = useState<AuthSession | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: keys.authSessions(),
    queryFn: api.listAuthSessions,
  });
  const revoke = useMutation({
    mutationFn: (sid: string) => api.revokeAuthSession(sid),
    onSuccess: () => {
      setTarget(null);
      qc.invalidateQueries({ queryKey: keys.authSessions() });
    },
  });
  const showUser = auth.role === "admin";

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Everywhere {showUser ? "anyone is" : "you are"} signed in. Revoking a
        session signs that browser out immediately
        {showUser ? " — including other users'" : ""}.
      </p>
      <ErrorNotice error={error} />
      {isLoading ? (
        <LoadingState lines={3} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState title="No live sessions." />
      ) : (
        <ul className="divide-y rounded-lg border">
          {(data ?? []).map((s) => (
            <li key={s.sid} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-sm font-medium">
                  {describeAgent(s.user_agent)}
                  {showUser && (
                    <span className="font-normal text-muted-foreground">
                      · {s.username}
                    </span>
                  )}
                  {s.current && (
                    <Badge variant="secondary" className="bg-success/15 text-success">
                      this device
                    </Badge>
                  )}
                </p>
                <p className="text-xs text-muted-foreground">
                  signed in {formatDateTime(s.created_at)} · last seen{" "}
                  {formatAgo(s.last_seen_at)}
                </p>
              </div>
              {!s.current && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setTarget(s)}
                >
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={target != null}
        onOpenChange={(open) => {
          if (!open) {
            setTarget(null);
            revoke.reset();
          }
        }}
        error={revoke.error}
        title="Revoke this session?"
        description={
          target
            ? `${describeAgent(target.user_agent)}${showUser ? ` (${target.username})` : ""} will be signed out immediately.`
            : ""
        }
        confirmLabel="Revoke the session"
        busy={revoke.isPending}
        onConfirm={() => target && revoke.mutate(target.sid)}
      />
    </div>
  );
}
