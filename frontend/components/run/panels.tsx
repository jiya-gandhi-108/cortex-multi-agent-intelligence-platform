"use client";

import { ExternalLink } from "lucide-react";

import { CitationChip, ScoreMeter, VerdictBar } from "@/components/evidence";
import { TaskDot, agentLabel } from "@/components/status";
import { EmptyState } from "@/components/ui/misc";
import { formatCost, formatDate, formatDuration, formatTokens } from "@/lib/format";
import type { AgentSummary, Execution, Source, Task, Verification } from "@/types";

export function TaskBoard({ tasks }: { tasks: Task[] }) {
  if (!tasks.length) {
    return <EmptyState title="No tasks yet">Tasks appear once the planner has broken down the objective.</EmptyState>;
  }
  return (
    <ul className="divide-y divide-line">
      {tasks.map((t) => (
        <li key={t.id} className="flex gap-3 px-5 py-3.5">
          <span className="pt-1.5">
            <TaskDot status={t.status} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="font-mono text-xs text-ink-faint">{t.task_key}</span>
              <span className="text-sm font-medium">{agentLabel(t.agent_name)}</span>
              <span className="text-xs text-ink-faint">{t.status}</span>
              {t.attempts > 1 && <span className="text-xs text-partial">{t.attempts} attempts</span>}
              {t.iteration > 0 && <span className="text-xs text-ink-faint">follow-up round {t.iteration}</span>}
            </div>
            <p className="mt-1 text-sm text-ink-muted">{t.task_description}</p>
            {t.dependencies.length > 0 && (
              <p className="mt-1 text-xs text-ink-faint">Waits for {t.dependencies.join(", ")}</p>
            )}
            {t.error_message && <p className="mt-1 text-sm text-unsupported">{t.error_message}</p>}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function AgentTable({ agents }: { agents: AgentSummary[] }) {
  if (!agents.length) return <EmptyState title="No agent activity yet" />;
  const maxTokens = Math.max(1, ...agents.map((a) => a.tokens));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-ink-muted">
            <th className="px-5 py-2.5 font-medium">Agent</th>
            <th className="px-3 py-2.5 text-right font-medium">Runs</th>
            <th className="px-3 py-2.5 text-right font-medium">Failed</th>
            <th className="w-[30%] px-3 py-2.5 font-medium">Tokens</th>
            <th className="px-3 py-2.5 text-right font-medium">Avg time</th>
            <th className="px-5 py-2.5 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {agents.map((a) => (
            <tr key={a.agent_name}>
              <td className="px-5 py-2.5">{agentLabel(a.agent_name)}</td>
              <td className="px-3 py-2.5 text-right tabular-nums">{a.executions}</td>
              <td className={`px-3 py-2.5 text-right tabular-nums ${a.failed ? "text-unsupported" : "text-ink-faint"}`}>
                {a.failed}
              </td>
              <td className="px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <div className="h-1.5 flex-1 rounded-full bg-raised">
                    <div className="h-full rounded-full bg-iris/70" style={{ width: `${(a.tokens / maxTokens) * 100}%` }} />
                  </div>
                  <span className="w-12 text-right tabular-nums">{formatTokens(a.tokens)}</span>
                </div>
              </td>
              <td className="px-3 py-2.5 text-right tabular-nums">
                {a.avg_ms == null ? "–" : formatDuration(a.avg_ms / 1000)}
              </td>
              <td className="px-5 py-2.5 text-right tabular-nums text-ink-muted">{formatCost(a.cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SourceList({ sources }: { sources: Source[] }) {
  if (!sources.length) return <EmptyState title="No sources yet">Sources appear as agents read pages.</EmptyState>;
  return (
    <ul className="divide-y divide-line">
      {sources.map((s) => (
        <li key={s.id} id={`source-${s.citation_key}`} className="scroll-mt-24 px-5 py-3.5 target:bg-iris-soft">
          <div className="flex flex-wrap items-center gap-2">
            <CitationChip citationKey={s.citation_key} />
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="inline-flex min-w-0 items-center gap-1 font-medium hover:text-iris-strong"
            >
              <span className="truncate">{s.title || s.domain}</span>
              <ExternalLink className="size-3.5 shrink-0 text-ink-faint" aria-hidden />
            </a>
          </div>
          <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-ink-faint">
            <span>{s.domain}</span>
            <span>{s.publication_date ? `Published ${formatDate(s.publication_date)}` : "Publication date unknown"}</span>
            <span>Retrieved {formatDate(s.retrieved_at)}</span>
            {s.task_key && <span>From {s.task_key}</span>}
            {!s.fetched && <span className="text-partial">Search snippet only</span>}
          </p>
          {s.excerpt && <p className="mt-1.5 line-clamp-2 text-sm text-ink-muted">{s.excerpt}</p>}
        </li>
      ))}
    </ul>
  );
}

export function VerificationSummary({ v }: { v: Verification }) {
  return (
    <div className="space-y-6 px-5 py-5">
      <div className="grid gap-6 sm:grid-cols-2">
        <ScoreMeter label="Evidence score (claims used in the report)" value={v.evidence_score} />
        <ScoreMeter label="Reliability (all claims the agents made)" value={v.reliability_score} />
      </div>
      <VerdictBar counts={v.counts} />
      {v.reasons.length > 0 && (
        <div>
          <p className="text-sm font-medium">Why evidence was judged insufficient</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-ink-muted">
            {v.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {(v.contradictions?.length ?? 0) > 0 && (
        <div>
          <p className="text-sm font-medium">Contradictions found</p>
          <ul className="mt-1.5 space-y-1 text-sm text-ink-muted">
            {v.contradictions!.map((c) => (
              <li key={c.claim_id}>
                <span className="font-mono text-xs">{c.claim_id}</span> {c.note}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-ink-faint">
        Scores are a documented heuristic index of evidence strength (verdict × number of independent domains ×
        source freshness), not probabilities. The marker shows the 0.60 sufficiency threshold.
      </p>
    </div>
  );
}

export function ExecutionTable({ executions }: { executions: Execution[] }) {
  if (!executions.length) return <EmptyState title="No executions recorded yet" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-ink-muted">
            <th className="px-5 py-2.5 font-medium">Agent</th>
            <th className="px-3 py-2.5 font-medium">Attempt</th>
            <th className="px-3 py-2.5 font-medium">Outcome</th>
            <th className="px-3 py-2.5 font-medium">Model</th>
            <th className="px-3 py-2.5 text-right font-medium">Tokens</th>
            <th className="px-5 py-2.5 text-right font-medium">Duration</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {executions.map((e) => (
            <tr key={e.id} className="align-top">
              <td className="px-5 py-2.5">{agentLabel(e.agent_name)}</td>
              <td className="px-3 py-2.5 tabular-nums">{e.attempt}</td>
              <td className="px-3 py-2.5">
                <span className={e.status === "failed" ? "text-unsupported" : e.status === "succeeded" ? "text-supported" : ""}>
                  {e.status}
                </span>
                {e.error_message && <p className="mt-0.5 max-w-md text-xs text-ink-muted">{e.error_message}</p>}
              </td>
              <td className="px-3 py-2.5 font-mono text-xs text-ink-muted">{e.model ?? "–"}</td>
              <td className="px-3 py-2.5 text-right tabular-nums">{formatTokens(e.token_usage)}</td>
              <td className="px-5 py-2.5 text-right tabular-nums">
                {e.execution_time == null ? "–" : formatDuration(e.execution_time / 1000)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
