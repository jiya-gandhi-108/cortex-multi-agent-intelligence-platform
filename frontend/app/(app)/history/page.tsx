"use client";

import { FileText, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { StatusBadge, isActiveStatus } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/form";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/misc";
import { useApi } from "@/hooks/use-api";
import { formatDate, formatDuration } from "@/lib/format";
import { ApiError, research } from "@/services/api";
import type { ProjectStatus } from "@/types";

const PAGE = 20;
const FILTERS: { value: "" | ProjectStatus; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "completed", label: "Completed" },
  { value: "running", label: "Researching" },
  { value: "awaiting_approval", label: "Needs approval" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "interrupted", label: "Interrupted" },
  { value: "draft", label: "Not started" },
];

export default function HistoryPage() {
  const router = useRouter();
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState<"" | ProjectStatus>("");
  const [rowError, setRowError] = useState<string>();
  const [rerunning, setRerunning] = useState<string>();
  const list = useApi(`history:${offset}:${filter}`, () =>
    research.list({ limit: PAGE, offset, status: filter || undefined }),
  );

  async function rerun(id: string) {
    setRerunning(id);
    setRowError(undefined);
    try {
      await research.start(id);
      router.push(`/research/${id}`);
    } catch (err) {
      setRowError((err as ApiError).message);
      setRerunning(undefined);
    }
  }

  const data = list.data;
  return (
    <>
      <PageHeader
        title="History"
        description="Every research project you've created, newest first."
        actions={
          <Select
            aria-label="Filter by status"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value as "" | ProjectStatus);
              setOffset(0);
            }}
            className="h-9 w-auto"
          >
            {FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </Select>
        }
      />
      {(list.error || rowError) && (
        <Notice tone="error" className="mb-6">
          {rowError ?? list.error?.message}
        </Notice>
      )}
      <Panel>
        {!data ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : data.items.length === 0 ? (
          <EmptyState
            title={filter ? "No projects with this status" : "No research yet"}
            action={
              !filter && (
                <Button asChild>
                  <Link href="/research/new">New research</Link>
                </Button>
              )
            }
          >
            {filter ? "Choose another status, or show all projects." : "Projects you create appear here with their runs and reports."}
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-ink-muted">
                  <th className="px-5 py-3 font-medium">Project</th>
                  <th className="px-3 py-3 font-medium">Status</th>
                  <th className="px-3 py-3 font-medium">Created</th>
                  <th className="px-3 py-3 text-right font-medium">Duration</th>
                  <th className="px-3 py-3 font-medium">Report</th>
                  <th className="px-5 py-3">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.items.map((p) => (
                  <tr key={p.id} className="hover:bg-raised/50">
                    <td className="max-w-md px-5 py-3">
                      <Link href={`/research/${p.id}`} className="font-medium hover:text-iris-strong">
                        {p.title}
                      </Link>
                      <p className="text-xs text-ink-faint">
                        {p.depth} depth{p.run_count > 1 ? `, ${p.run_count} runs` : ""}
                      </p>
                    </td>
                    <td className="px-3 py-3">
                      <StatusBadge status={p.status} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-ink-muted">{formatDate(p.created_at)}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-ink-muted">{formatDuration(p.duration_seconds)}</td>
                    <td className="px-3 py-3">
                      {p.has_report ? (
                        <Link href={`/research/${p.id}/report`} className="inline-flex items-center gap-1.5 text-iris-strong hover:underline">
                          <FileText className="size-3.5" aria-hidden /> Open
                        </Link>
                      ) : (
                        <span className="text-ink-faint">None</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right">
                      {!isActiveStatus(p.status) && p.status !== "awaiting_approval" && p.status !== "draft" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={rerunning === p.id}
                          onClick={() => rerun(p.id)}
                        >
                          <RotateCcw aria-hidden />
                          {p.status === "interrupted" ? "Resume" : "Re-run"}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > PAGE && (
          <div className="flex items-center justify-between border-t border-line px-5 py-3 text-sm text-ink-muted">
            <span>
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
            </span>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
                Previous
              </Button>
              <Button variant="secondary" size="sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
                Next
              </Button>
            </div>
          </div>
        )}
      </Panel>
    </>
  );
}
