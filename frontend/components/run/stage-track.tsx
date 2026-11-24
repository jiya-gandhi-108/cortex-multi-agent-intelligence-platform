import { Check } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ProjectStatus } from "@/types";

const STAGES = [
  { key: "plan", label: "Plan" },
  { key: "research", label: "Research" },
  { key: "verify", label: "Verify" },
  { key: "report", label: "Report" },
] as const;

function position(status: ProjectStatus, stage: string | null): number {
  switch (status) {
    case "draft":
      return -1;
    case "queued":
    case "planning":
    case "awaiting_approval":
      return 0;
    case "running":
      return stage === "follow_up_research" ? 1 : 1;
    case "verifying":
      return 2;
    case "reporting":
      return 3;
    case "completed":
      return 4;
    default:
      return -2; // failed/cancelled/interrupted: derive from stage below
  }
}

function fromStage(stage: string | null): number {
  if (!stage) return 0;
  if (/plan/.test(stage)) return 0;
  if (/research|collect|follow/.test(stage)) return 1;
  if (/verif/.test(stage)) return 2;
  if (/report|quality/.test(stage)) return 3;
  return 0;
}

/** The workflow is a real sequence, so it is shown as one (with a loop note for follow-ups). */
export function StageTrack({
  status,
  stage,
  iterations,
}: {
  status: ProjectStatus;
  stage: string | null;
  iterations?: number;
}) {
  let current = position(status, stage);
  const halted = current === -2;
  if (halted) current = fromStage(stage);

  return (
    <ol className="grid grid-cols-4 gap-2" aria-label="Workflow stages">
      {STAGES.map((s, i) => {
        const done = i < current;
        const active = i === current && !halted && status !== "completed";
        const stopped = i === current && halted;
        return (
          <li key={s.key} className="min-w-0" aria-current={active ? "step" : undefined}>
            <div
              className={cn(
                "h-1 rounded-full bg-raised",
                done && "bg-supported",
                active && "bg-iris",
                stopped && (status === "failed" ? "bg-unsupported" : "bg-partial"),
              )}
            />
            <div className="mt-2 flex items-center gap-1.5 text-sm">
              {done && <Check className="size-3.5 text-supported" aria-hidden />}
              {active && <span className="live-dot size-1.5 rounded-full bg-iris" aria-hidden />}
              <span className={cn(done || active || stopped ? "text-ink" : "text-ink-faint")}>{s.label}</span>
              {s.key === "research" && iterations ? (
                <span className="text-xs text-ink-faint">+{iterations} follow-up</span>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
