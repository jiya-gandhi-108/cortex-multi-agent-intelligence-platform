import { Check, CircleDashed, CircleSlash, Hourglass, PauseCircle, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ProjectStatus } from "@/types";

type Tone = "idle" | "active" | "waiting" | "good" | "bad" | "muted";

const STATUS: Record<ProjectStatus, { label: string; tone: Tone }> = {
  draft: { label: "Not started", tone: "idle" },
  queued: { label: "Queued", tone: "active" },
  planning: { label: "Planning", tone: "active" },
  awaiting_approval: { label: "Needs approval", tone: "waiting" },
  running: { label: "Researching", tone: "active" },
  verifying: { label: "Verifying", tone: "active" },
  reporting: { label: "Writing report", tone: "active" },
  completed: { label: "Completed", tone: "good" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "muted" },
  interrupted: { label: "Interrupted", tone: "waiting" },
};

const TONE_CLASS: Record<Tone, string> = {
  idle: "text-ink-muted border-line-strong",
  active: "text-iris-strong border-iris/40 bg-iris-soft",
  waiting: "text-partial border-partial/40 bg-partial-soft",
  good: "text-supported border-supported/40 bg-supported-soft",
  bad: "text-unsupported border-unsupported/40 bg-unsupported-soft",
  muted: "text-ink-faint border-line",
};

export function isActiveStatus(status: ProjectStatus | undefined): boolean {
  return status ? STATUS[status].tone === "active" : false;
}

export function statusLabel(status: ProjectStatus): string {
  return STATUS[status]?.label ?? status;
}

export function StatusBadge({ status, className }: { status: ProjectStatus; className?: string }) {
  const meta = STATUS[status] ?? { label: status, tone: "idle" as Tone };
  const Icon = {
    idle: CircleDashed,
    active: null,
    waiting: status === "interrupted" ? PauseCircle : Hourglass,
    good: Check,
    bad: X,
    muted: CircleSlash,
  }[meta.tone];
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium",
        TONE_CLASS[meta.tone],
        className,
      )}
    >
      {Icon ? (
        <Icon className="size-3" aria-hidden />
      ) : (
        <span className="live-dot size-1.5 rounded-full bg-current" aria-hidden />
      )}
      {meta.label}
    </span>
  );
}

const TASK_TONE: Record<string, string> = {
  pending: "border-line-strong",
  running: "border-iris bg-iris live-dot",
  completed: "border-supported bg-supported",
  failed: "border-unsupported bg-unsupported",
  skipped: "border-ink-faint",
  cancelled: "border-ink-faint",
};

export function TaskDot({ status }: { status: string }) {
  return <span className={cn("inline-block size-2.5 shrink-0 rounded-full border-2", TASK_TONE[status])} aria-hidden />;
}

export const AGENT_LABEL: Record<string, string> = {
  planner_agent: "Planner",
  research_agent: "Research",
  competitor_agent: "Competitor intelligence",
  market_analysis_agent: "Market analysis",
  verification_agent: "Verification",
  report_agent: "Report",
};

export function agentLabel(name: string | null | undefined): string {
  return name ? (AGENT_LABEL[name] ?? name) : "Workflow";
}
