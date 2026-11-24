"use client";

import { FileText, Play, RotateCcw, Square } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ActivityFeed } from "@/components/run/activity-feed";
import { AgentTable, ExecutionTable, SourceList, TaskBoard, VerificationSummary } from "@/components/run/panels";
import { StageTrack } from "@/components/run/stage-track";
import { StatusBadge, agentLabel, isActiveStatus } from "@/components/status";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm";
import { Notice, PageHeader, Panel, PanelHeader, Skeleton } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApi } from "@/hooks/use-api";
import { useRunEvents } from "@/hooks/use-run-events";
import { useSearchParam } from "@/hooks/use-search-param";
import { formatCost, formatDuration, formatNumber, formatTokens } from "@/lib/format";
import { ApiError, research } from "@/services/api";
import type { RunStatus, Verification } from "@/types";

const ERROR_HELP: Record<string, string> = {
  budget_exceeded: "The run reached its token or cost budget. Raise the budget in Settings or on a new project, then re-run.",
  configuration_error: "An API key is missing or was rejected by the provider. Check the server's configuration.",
  insufficient_evidence: "No claim could be verified against the sources found. Try a narrower or differently worded objective.",
  quality_check_failed: "The report didn't meet the minimum quality bar (cited findings and recommendations). Re-run to try again.",
  planning_failed: "The planner couldn't produce a valid plan. Rephrase the objective with more concrete detail.",
  all_tasks_failed: "Every research task failed. This is usually a provider outage; re-run in a few minutes.",
  plan_rejected: "You rejected the plan, so no research was run.",
};

function splitError(message: string | null | undefined): { code: string | null; text: string } {
  if (!message) return { code: null, text: "" };
  const m = message.match(/^([a-z_]+): ([\s\S]*)$/);
  return m ? { code: m[1], text: m[2] } : { code: null, text: message };
}

function useElapsed(status: RunStatus | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  const active = isActiveStatus(status?.status);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  if (!status?.started_at) return null;
  if (!active) return status.duration_seconds ?? null;
  return (now - new Date(status.started_at).getTime()) / 1000;
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-5 py-3.5">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="mt-0.5 text-lg font-medium tabular-nums">{value}</dd>
    </div>
  );
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const [actionError, setActionError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const urlStartError = useSearchParam("start_error");
  const [dismissed, setDismissed] = useState(false);
  const startError = dismissed ? undefined : (urlStartError ?? undefined);

  const statusQuery = useApi(`status:${id}`, () => research.status(id));
  const status = statusQuery.data;
  const active = isActiveStatus(status?.status);
  const project = useApi(`project:${id}`, () => research.get(id));

  // Poll as a backstop while active; the event stream drives most refreshes.
  const reloadStatus = statusQuery.reload; // stable identity (useCallback)
  const reloadProject = project.reload;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void reloadStatus(), 5000);
    return () => window.clearInterval(t);
  }, [active, reloadStatus]);

  const streamOn = Boolean(status && status.run > 0);
  const streamKey = `${status?.run ?? 0}:${active ? "live" : "rest"}`;
  const { events, state } = useRunEvents(id, streamOn, streamKey, () => void reloadStatus());

  const lastEvent = events.at(-1);
  const refreshKey = `${status?.status}:${events.filter((e) => ["task_completed", "task_failed", "verification", "report"].includes(e.type)).length}`;
  // Only the visible tab fetches, and only after meaningful progress (refreshKey).
  const [tab, setTab] = useState("agents");
  const hasRun = Boolean(status && status.run > 0);
  const agents = useApi(hasRun && tab === "agents" ? `agents:${id}:${refreshKey}` : null, () => research.agents(id));
  const sources = useApi(hasRun && tab === "sources" ? `sources:${id}:${refreshKey}` : null, () => research.sources(id));
  const executions = useApi(hasRun && tab === "executions" ? `execs:${id}:${refreshKey}` : null, () =>
    research.executions(id),
  );

  useEffect(() => {
    if (lastEvent && ["task_started", "task_completed", "task_failed", "task_skipped", "plan"].includes(lastEvent.type)) {
      void reloadStatus();
    }
  }, [lastEvent, reloadStatus]);

  const retries = useMemo(() => events.filter((e) => e.type === "task_retry").length, [events]);
  const elapsed = useElapsed(status);
  const verification = (status?.summary?.verification ?? null) as Verification | null;
  const iterations = Number(status?.summary?.iterations ?? 0);
  const warnings = (status?.summary?.warnings ?? []) as string[];

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      setActionError(undefined);
      setDismissed(true);
      if (urlStartError) window.history.replaceState(null, "", window.location.pathname);
      try {
        await fn();
        await reloadStatus();
        await reloadProject();
      } catch (err) {
        setActionError((err as ApiError).message);
      } finally {
        setBusy(false);
      }
    },
    [reloadStatus, reloadProject, urlStartError],
  );

  if (statusQuery.error && !status) {
    return (
      <Notice tone="error" title={statusQuery.error.status === 404 ? "Project not found" : "Couldn't load this project"}>
        {statusQuery.error.status === 404
          ? "It may have been removed, or it belongs to another account."
          : statusQuery.error.message}
      </Notice>
    );
  }
  if (!status) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const err = splitError(status.error_message);
  const restartable = ["draft", "completed", "failed", "cancelled", "interrupted"].includes(status.status);
  const startLabel =
    status.status === "draft" ? "Start research" : status.status === "interrupted" ? "Resume run" : "Run again";
  const hasReport = project.data?.has_report || status.status === "completed";

  return (
    <>
      <PageHeader
        title={project.data?.title ?? "Research project"}
        description={
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge status={status.status} />
            {status.run > 1 && <span className="text-sm text-ink-faint">Run {status.run}</span>}
          </div>
        }
        actions={
          <>
            {hasReport && (
              <Button asChild variant="secondary">
                <Link href={`/research/${id}/report`}>
                  <FileText aria-hidden />
                  View report
                </Link>
              </Button>
            )}
            {active && (
              <ConfirmDialog
                trigger={
                  <Button variant="danger" disabled={busy}>
                    <Square aria-hidden />
                    Cancel run
                  </Button>
                }
                title="Cancel this run?"
                description="Agents stop at their next step. Tasks and sources collected so far are kept, and you can run the project again later."
                confirmLabel="Cancel run"
                onConfirm={() => act(() => research.cancel(id))}
              />
            )}
            {restartable && (
              <Button disabled={busy} onClick={() => act(() => research.start(id))}>
                {status.status === "draft" ? <Play aria-hidden /> : <RotateCcw aria-hidden />}
                {startLabel}
              </Button>
            )}
          </>
        }
      />

      <div className="space-y-6">
        {(startError || actionError) && (
          <Notice tone="error" title="The run didn't start">
            {startError ?? actionError}
          </Notice>
        )}
        {status.status === "failed" && (
          <Notice tone="error" title={`Run failed${err.code ? `: ${err.code.replaceAll("_", " ")}` : ""}`}>
            <p>{err.text}</p>
            {err.code && ERROR_HELP[err.code] && <p className="mt-1">{ERROR_HELP[err.code]}</p>}
          </Notice>
        )}
        {status.status === "interrupted" && (
          <Notice tone="warning" title="This run was interrupted">
            The server restarted while it was running. Resume to continue from the last completed step without
            repeating finished work.
          </Notice>
        )}
        {status.status === "completed" && warnings.length > 0 && (
          <Notice tone="warning" title="Completed with warnings">
            <ul className="list-disc pl-4">
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </Notice>
        )}

        {status.status === "awaiting_approval" && status.plan && (
          <Panel className="border-partial/40">
            <PanelHeader
              title="Review the research plan"
              description="Nothing has been searched yet. Approve to start the agents, or reject to stop here."
            />
            <ol className="divide-y divide-line">
              {(status.plan.tasks as { task_id: string; agent: string; description: string; dependencies: string[] }[]).map(
                (t) => (
                  <li key={t.task_id} className="px-5 py-3">
                    <p className="text-sm">
                      <span className="mr-2 font-mono text-xs text-ink-faint">{t.task_id}</span>
                      <span className="font-medium">{agentLabel(t.agent)}</span>
                      {t.dependencies.length > 0 && (
                        <span className="ml-2 text-xs text-ink-faint">after {t.dependencies.join(", ")}</span>
                      )}
                    </p>
                    <p className="mt-0.5 text-sm text-ink-muted">{t.description}</p>
                  </li>
                ),
              )}
            </ol>
            <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3.5">
              <Button variant="ghost" disabled={busy} onClick={() => act(() => research.approve(id, false, "Rejected in review"))}>
                Reject plan
              </Button>
              <Button disabled={busy} onClick={() => act(() => research.approve(id, true))}>
                Approve and start
              </Button>
            </div>
          </Panel>
        )}

        {status.run > 0 && (
          <Panel className="px-5 py-5">
            <StageTrack status={status.status} stage={status.stage} iterations={iterations} />
          </Panel>
        )}

        <Panel>
          <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
            <Figure label="Elapsed" value={formatDuration(elapsed)} />
            <Figure label="Tasks done" value={`${status.tasks.completed} of ${status.tasks.total}`} />
            <Figure label="Sources" value={formatNumber(status.sources)} />
            <Figure label="Tokens" value={formatTokens(status.total_tokens)} />
            <Figure label="Retries" value={formatNumber(retries)} />
            <Figure label="Est. cost" value={formatCost(status.estimated_cost)} />
          </dl>
        </Panel>

        {status.run === 0 ? (
          <Panel>
            <PanelHeader title="Objective" />
            <p className="max-w-prose px-5 py-4 font-serif text-[1.02rem] leading-relaxed">{project.data?.objective}</p>
          </Panel>
        ) : (
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Panel>
              <PanelHeader title="Tasks" description={`${status.tasks.total} planned`} />
              <TaskBoard tasks={status.task_list} />
            </Panel>
            <Panel>
              <ActivityFeed events={events} state={state} />
            </Panel>
          </div>
        )}

        {status.run > 0 && (
          <Panel>
            <Tabs value={tab} onValueChange={setTab} className="pt-3">
              <TabsList className="px-5">
                <TabsTrigger value="agents">Agents</TabsTrigger>
                <TabsTrigger value="sources">Sources ({status.sources})</TabsTrigger>
                <TabsTrigger value="verification">Verification</TabsTrigger>
                <TabsTrigger value="executions">Executions</TabsTrigger>
              </TabsList>
              <TabsContent value="agents">
                <AgentTable agents={agents.data ?? []} />
              </TabsContent>
              <TabsContent value="sources">
                <SourceList sources={sources.data?.items ?? []} />
              </TabsContent>
              <TabsContent value="verification">
                {verification ? (
                  <VerificationSummary v={verification} />
                ) : (
                  <p className="px-5 py-8 text-sm text-ink-muted">Verification runs after the research tasks finish.</p>
                )}
              </TabsContent>
              <TabsContent value="executions">
                <ExecutionTable executions={executions.data?.items ?? []} />
              </TabsContent>
            </Tabs>
          </Panel>
        )}
      </div>
    </>
  );
}
