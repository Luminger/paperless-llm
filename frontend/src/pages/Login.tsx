// Login: credentials are validated against
// paperless itself — no user store of our own.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "../api";
import { errorMessage } from "../lib/errors";
import { keys } from "../lib/keys";

export default function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => api.login(username, password),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.auth() }),
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-lg">
            paperless<span className="text-primary">-llm</span>
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Sign in with your paperless account.
          </p>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              login.mutate();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="login-user">Username</Label>
              <Input
                id="login-user"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="login-pass">Password</Label>
              <Input
                id="login-pass"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {login.error && (
              <p className="text-sm text-destructive">
                {errorMessage(login.error)}
              </p>
            )}
            <Button
              type="submit"
              className="w-full"
              disabled={login.isPending || !username || !password}
            >
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
