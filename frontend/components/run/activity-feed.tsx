"use client";

import { useMemo, useState } from "react";

import { agentLabel } from "@/components/status";
import { Switch } from "@/components/ui/form";
import type { StreamState } from "@/hooks/use-run-events";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { WorkflowEvent } from "@/types";

type Tone = "default" | "good" | "bad" | "warn" | "quiet";

function describe(e: WorkflowEvent): { text: string; tone: Tone } | null {
  const d = e.data as Record<string, unknown>;
  const task = e.task_key ? `${e.task_key} ` : "";
  switch (e.type) {
    case "status":
      return { text: `Run ${String(d.status).replace("_", " ")}${d.error ? `: ${d.error}` : ""}`, tone: d.status === "failed" ? "bad" : d.status === "completed" ? "good" : "default" };
    case "stage":
      return e.stage === "collecting" || e.stage === "quality_checked"
        ? null
        : { text: `Stage: ${String(e.stage).replaceAll("_", " ")}`, tone: "quiet" };
    case "plan":
      return { text: `Plan ready with ${(d.tasks as unknown[] | undefined)?.length ?? 0} tasks`, tone: "default" };
    case "approval_required":
      return { text: "Waiting for your approval of the plan", tone: "warn" };
    case "task_started":
      return { text: `${agentLabel(e.agent)} started ${e.task_key ?? "a task"}`, tone: "default" };
    case "task_retry":
      return { text: `${task}attempt ${d.attempt} failed, retrying in ${d.next_in_s}s: ${d.error}`, tone: "warn" };
    case "task_completed": {
      const parts = [
        d.sources_retrieved != null && `${d.sources_retrieved} sources`,
        d.findings != null && `${d.findings} findings`,
        d.competitors != null && `${d.competitors} competitors`,
        d.insights != null && `${d.insights} insights`,
        Number(d.rejected_citations) > 0 && `${d.rejected_citations} invalid citations removed`,
        Number(d.pricing_redacted) > 0 && `${d.pricing_redacted} unsourced prices set to unknown`,
      ].filter(Boolean);
      return { text: `${agentLabel(e.agent)} finished ${e.task_key ?? "a task"}${parts.length ? `: ${parts.join(", ")}` : ""}`, tone: "good" };
    }
    case "task_failed":
      return { text: `${agentLabel(e.agent)} failed ${e.task_key ?? "a task"}: ${d.error}`, tone: "bad" };
    case "task_skipped":
      return { text: `${task}skipped: ${d.reason}`, tone: "warn" };
    case "tool_call":
      if (d.tool === "web_search") {
        return { text: `Searched "${d.query}"${d.error ? ` (failed: ${d.error})` : `: ${d.results} results`}`, tone: d.error ? "warn" : "quiet" };
      }
      return { text: `${d.error ? "Couldn't read" : "Read"} ${d.url}${d.error ? ` (${d.error})` : ""}`, tone: d.error ? "warn" : "quiet" };
    case "llm_call":
      return { text: `${agentLabel(e.agent)} called ${d.model}: ${d.prompt_tokens} in, ${d.completion_tokens} out${d.repair ? " (repair)" : ""}`, tone: "quiet" };
    case "sources":
      return { text: `${task}collected ${d.count} sources`, tone: "default" };
    case "verification":
      return {
        text: `Verification: evidence score ${Number(d.score).toFixed(2)}, ${d.sufficient ? "sufficient" : "insufficient"}`,
        tone: d.sufficient ? "good" : "warn",
      };
    case "report":
      return { text: `Report written, citing ${d.cited_sources} sources`, tone: "good" };
    case "error":
      return { text: `${d.code}: ${d.message}`, tone: "bad" };
    case "completed":
      return null; // the status event that follows is authoritative
    default:
      return null;
  }
}

const TONE: Record<Tone, string> = {
  default: "bg-ink-muted",
  good: "bg-supported",
  bad: "bg-unsupported",
  warn: "bg-partial",
  quiet: "bg-line-strong",
};
const DETAIL = new Set(["tool_call", "llm_call"]);

export function ActivityFeed({ events, state }: { events: WorkflowEvent[]; state: StreamState }) {
  const [showDetail, setShowDetail] = useState(false);
  const rows = useMemo(
    () =>
      events
        .filter((e) => showDetail || !DETAIL.has(e.type))
        .map((e, i) => ({ e, i, d: describe(e) }))
        .filter((r) => r.d)
        .reverse(),
    [events, showDetail],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div className="flex items-center gap-2">
          <h2 className="font-sans text-base font-semibold tracking-normal">Activity</h2>
          {state === "live" && (
            <span className="flex items-center gap-1.5 text-xs text-iris-strong">
              <span className="live-dot size-1.5 rounded-full bg-iris" aria-hidden />
              Live
            </span>
          )}
          {state === "connecting" && <span className="text-xs text-ink-faint">Connecting…</span>}
        </div>
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          Show searches and model calls
          <Switch checked={showDetail} onCheckedChange={setShowDetail} aria-label="Show searches and model calls" />
        </label>
      </div>
      {rows.length === 0 ? (
        <p className="px-5 py-8 text-sm text-ink-muted">
          {state === "closed" || state === "idle" ? "No activity recorded for this run." : "Waiting for the first events…"}
        </p>
      ) : (
        <ol className="max-h-[32rem] flex-1 overflow-y-auto px-5 py-2" aria-live="polite" aria-relevant="additions">
          {rows.map(({ e, i, d }) => (
            <li key={i} className="flex gap-3 py-1.5 text-sm">
              <span className="w-[4.6rem] shrink-0 pt-px text-xs text-ink-faint tabular-nums">{formatTime(e.at)}</span>
              <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", TONE[d!.tone])} aria-hidden />
              <span className={cn("min-w-0 break-words", d!.tone === "quiet" ? "text-ink-muted" : "text-ink")}>
                {d!.text}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
