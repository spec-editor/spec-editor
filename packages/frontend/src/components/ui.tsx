"use client";

interface LoadingStateProps {
  message?: string;
}
interface ErrorBannerProps {
  message: string;
  hint?: string;
}
interface EmptyStateProps {
  message: string;
}

export function LoadingState({ message = "Loading..." }: LoadingStateProps) {
  return (
    <div className="loading-state">
      <div className="spinner" />
      <p>{message}</p>
    </div>
  );
}

export function ErrorBanner({ message, hint }: ErrorBannerProps) {
  return (
    <div className="error-state">
      <p>⚠️ {message}</p>
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

export function EmptyState({ message }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <p>{message}</p>
    </div>
  );
}
