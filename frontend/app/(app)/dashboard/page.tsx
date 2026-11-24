"use client";

import Link from "next/link";

import { StatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { EmptyState, Notice, PageHeader, Panel, PanelHeader, Skeleton } from "@/components/ui/misc";
import { useApi } from "@/hooks/use-api";
import { formatCost, formatNumber, formatTokens, relativeTime } from "@/lib/format";
import { dashboard } from "@/services/api";
import type { ProjectStatus } from "@/types";

function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="px-5 py-4">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="mt-1 font-serif text-2xl tabular-nums">{value}</dd>
      {note && <dd className="text-xs text-ink-faint">{note}</dd>}
    </div>
  );
}

export default function DashboardPage() {
  const { data, error, loading } = useApi("dashboard", dashboard.get, { refreshMs: 15_000 });

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Your research projects and what they've produced."
        actions={
          <Button asChild>
            <Link href="/research/new">New research</Link>
          </Button>
        }
      />

      {error && <Notice tone="error" title="Couldn't load your dashboard">{error.message}</Notice>}

      {loading && !data ? (
        <Skeleton className="h-24 w-full" />
      ) : data ? (
        <>
          <Panel>
            <dl className="grid grid-cols-2 divide-line sm:grid-cols-3 lg:grid-cols-5 lg:divide-x [&>div]:border-line max-lg:[&>div]:border-b">
              <Figure label="Research projects" value={formatNumber(data.total_projects)} />
              <Figure label="Completed tasks" value={formatNumber(data.completed_tasks)} />
              <Figure label="Running now" value={formatNumber(data.active_executions)} />
              <Figure label="Reports" value={formatNumber(data.total_reports)} />
              <Figure
                label="Tokens used"
                value={formatTokens(data.total_tokens)}
                note={Number(data.estimated_cost) > 0 ? `${formatCost(data.estimated_cost)} estimated` : "No cost recorded"}
              />
            </dl>
          </Panel>

          {data.total_projects === 0 ? (
            <Panel className="mt-6">
              <EmptyState
                title="Start your first research project"
                action={
                  <Button asChild>
                    <Link href="/research/new">Describe what you want to know</Link>
                  </Button>
                }
              >
                Write your objective in plain language, such as a market to size, competitors to compare or a decision
                to make. Cortex plans the work and shows you each step as it runs.
              </EmptyState>
            </Panel>
          ) : (
            <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
              <Panel>
                <PanelHeader
                  title="Recent projects"
                  action={
                    <Link href="/history" className="text-sm text-iris-strong hover:underline">
                      View all
                    </Link>
                  }
                />
                <ul className="divide-y divide-line">
                  {data.recent_projects.map((p) => (
                    <li key={String(p.id)}>
                      <Link
                        href={`/research/${p.id}`}
                        className="flex items-center justify-between gap-4 px-5 py-3.5 transition-colors hover:bg-raised"
                      >
                        <div className="min-w-0">
                          <p className="truncate font-medium">{String(p.title)}</p>
                          <p className="text-sm text-ink-faint">Updated {relativeTime(String(p.updated_at))}</p>
                        </div>
                        <StatusBadge status={p.status as ProjectStatus} />
                      </Link>
                    </li>
                  ))}
                </ul>
              </Panel>

              <Panel>
                <PanelHeader title="Recent reports" />
                {data.recent_reports.length === 0 ? (
                  <EmptyState title="No reports yet">Reports appear here when a run completes.</EmptyState>
                ) : (
                  <ul className="divide-y divide-line">
                    {data.recent_reports.map((r) => (
                      <li key={`${r.project_id}-${r.version}`}>
                        <Link
                          href={`/research/${r.project_id}/report?version=${r.version}`}
                          className="block px-5 py-3.5 transition-colors hover:bg-raised"
                        >
                          <p className="truncate font-medium">{r.project_title}</p>
                          <p className="mt-0.5 flex gap-3 text-sm text-ink-faint">
                            <span>Version {r.version}</span>
                            <span>{relativeTime(r.created_at)}</span>
                            {r.evidence_score != null && (
                              <span>Evidence score {Number(r.evidence_score).toFixed(2)}</span>
                            )}
                          </p>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
          )}
        </>
      ) : null}
    </>
  );
}
