// App-level error boundary (AUDIT FP-M2): a render-time crash shows a
// recoverable message instead of a white screen.

import { Component, type ReactNode } from "react";

export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-xl px-4 py-16 text-center">
          <p className="text-sm font-medium">Something went wrong.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {String(this.state.error)}
          </p>
          <button
            className="mt-4 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground"
            onClick={() => {
              this.setState({ error: null });
              window.location.reload();
            }}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
