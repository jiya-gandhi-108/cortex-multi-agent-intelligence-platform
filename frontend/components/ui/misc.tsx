import { AlertCircle, Info } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/** A ruled section, not a floating card: hierarchy comes from rules and spacing. */
export function Panel({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return <section className={cn("min-w-0 rounded-[var(--radius-panel)] border border-line bg-surface", className)} {...props} />;
}

export function PanelHeader({
  title,
  description,
  action,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 border-b border-line px-5 py-3.5", className)}>
      <div className="min-w-0">
        <h2 className="font-sans text-base font-semibold tracking-normal">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-ink-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded bg-raised", className)} />;
}

export function Notice({
  tone = "info",
  title,
  children,
  className,
}: {
  tone?: "info" | "error" | "warning";
  title?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  const styles = {
    info: "border-line-strong bg-raised text-ink",
    error: "border-unsupported/40 bg-unsupported-soft text-ink",
    warning: "border-partial/40 bg-partial-soft text-ink",
  }[tone];
  const Icon = tone === "info" ? Info : AlertCircle;
  const iconColor = { info: "text-ink-muted", error: "text-unsupported", warning: "text-partial" }[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={cn("flex gap-3 rounded-[var(--radius-control)] border p-3.5 text-sm", styles, className)}>
      <Icon className={cn("mt-0.5 size-4 shrink-0", iconColor)} aria-hidden />
      <div className="min-w-0 space-y-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className="text-ink-muted">{children}</div>}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-3 px-5 py-10">
      <p className="font-serif text-lg">{title}</p>
      {children && <div className="max-w-prose text-sm text-ink-muted">{children}</div>}
      {action}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 max-w-3xl">
        <h1 className="text-3xl">{title}</h1>
        {description && <div className="mt-2 text-ink-muted">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}
