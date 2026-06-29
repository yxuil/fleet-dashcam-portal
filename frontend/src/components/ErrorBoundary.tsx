/**
 * Render-error boundary for the application shell.
 *
 * React Query's own retry/error paths handle most async failures; this
 * boundary is the last-resort catch for synchronous render explosions
 * (e.g. an invariant failure in a hook).
 */

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

type Props = {
  children: ReactNode;
  /** Optional render-prop fallback. Defaults to a small panel. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
};

type State = {
  error: Error | null;
};

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Errors in render are rare in prod — log loudly so devs spot them.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset);
    }

    return (
      <div
        role="alert"
        className="m-6 max-w-xl rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm"
      >
        <p className="font-semibold text-destructive">Something went wrong.</p>
        <p className="mt-1 break-words text-foreground/80">{error.message}</p>
        <button
          type="button"
          onClick={() => {
            this.reset();
            window.location.reload();
          }}
          className="mt-3 inline-flex h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-accent"
        >
          Reload
        </button>
      </div>
    );
  }
}
