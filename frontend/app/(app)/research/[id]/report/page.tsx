"use client";

import { ArrowLeft, Download, FileDown } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ScoreMeter, VerdictBar } from "@/components/evidence";
import { MarkdownReport, slugify } from "@/components/report/markdown-report";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/form";
import { Notice, Panel, Skeleton } from "@/components/ui/misc";
import { useApi } from "@/hooks/use-api";
import { useSearchParam } from "@/hooks/use-search-param";
import { formatDateTime } from "@/lib/format";
import { research } from "@/services/api";
import type { Verification } from "@/types";

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const urlVersion = Number(useSearchParam("version"));
  const [chosen, setChosen] = useState<number>();
  const version = chosen ?? (Number.isInteger(urlVersion) && urlVersion > 0 ? urlVersion : undefined);

  const report = useApi(`report:${id}:${version ?? "latest"}`, () => research.report(id, version));
  const latest = useApi(`report:${id}:latest-version`, () => research.report(id));
  const project = useApi(`project:${id}`, () => research.get(id));
  const sources = useApi(`report-sources:${id}`, () => research.sources(id, 100));

  const titles = useMemo(
    () => Object.fromEntries((sources.data?.items ?? []).map((s) => [s.citation_key, s.title ?? s.domain])),
    [sources.data],
  );
  const sections = useMemo(
    () =>
      (report.data?.report_content.match(/^## .+$/gm) ?? []).map((line) => {
        const label = line.slice(3).replace(/^\d+\.\s*/, "");
        return { id: slugify(line.slice(3)), label };
      }),
    [report.data],
  );

  const versions = latest.data ? Array.from({ length: latest.data.version }, (_, i) => latest.data!.version - i) : [];
  const shown = report.data?.version;
  const verification = (report.data?.verification ?? null) as Verification | null;
  const quality = report.data?.structured?.quality as { warnings?: string[] } | undefined;

  function changeVersion(v: number) {
    setChosen(v);
    window.history.replaceState(null, "", `?version=${v}`);
  }

  if (report.error) {
    return (
      <div className="space-y-4">
        <Link href={`/research/${id}`} className="inline-flex items-center gap-1.5 text-sm text-ink-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Back to the run
        </Link>
        <Notice tone={report.error.status === 404 ? "info" : "error"} title={report.error.status === 404 ? "No report yet" : "Couldn't load the report"}>
          {report.error.status === 404 ? "A report is written when a run completes. Check the run's progress." : report.error.message}
        </Notice>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <Link href={`/research/${id}`} className="inline-flex items-center gap-1.5 text-sm text-ink-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> {project.data?.title ?? "Back to the run"}
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          {versions.length > 1 && shown && (
            <Select
              aria-label="Report version"
              value={shown}
              onChange={(e) => changeVersion(Number(e.target.value))}
              className="h-9 w-auto"
            >
              {versions.map((v) => (
                <option key={v} value={v}>
                  Version {v}
                  {v === latest.data?.version ? " (latest)" : ""}
                </option>
              ))}
            </Select>
          )}
          <Button asChild variant="secondary" size="md">
            <a href={research.markdownUrl(id, shown)} download>
              <Download aria-hidden /> Markdown
            </a>
          </Button>
          <Button asChild size="md">
            <a href={research.pdfUrl(id, shown)} download>
              <FileDown aria-hidden /> PDF
            </a>
          </Button>
        </div>
      </div>

      {!report.data ? (
        <div className="space-y-4">
          <Skeleton className="h-12 w-3/4" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-12 xl:grid-cols-[minmax(0,1fr)_17rem]">
          <article>
            <p className="mb-6 text-sm text-ink-faint">
              Version {report.data.version}, written {formatDateTime(report.data.created_at)}
            </p>
            <MarkdownReport markdown={report.data.report_content} titles={titles} />
          </article>

          <aside className="space-y-6 xl:sticky xl:top-10 xl:self-start">
            {sections.length > 0 && (
              <nav aria-label="Report sections">
                <p className="mb-2 text-sm font-medium">Contents</p>
                <ol className="space-y-1 border-l border-line">
                  {sections.map((s) => (
                    <li key={s.id}>
                      <a href={`#${s.id}`} className="-ml-px block border-l border-transparent py-0.5 pl-3 text-sm text-ink-muted hover:border-iris hover:text-ink">
                        {s.label}
                      </a>
                    </li>
                  ))}
                </ol>
              </nav>
            )}
            {verification && (
              <Panel className="space-y-5 p-4">
                <p className="text-sm font-medium">Evidence behind this report</p>
                <ScoreMeter label="Evidence score" value={verification.evidence_score} />
                <VerdictBar counts={verification.counts} className="[&_dl]:grid-cols-1 [&_dl]:sm:grid-cols-1" />
                <p className="text-xs text-ink-faint">
                  Only supported and partly supported claims appear in the report. The score is a heuristic, not a
                  probability.
                </p>
              </Panel>
            )}
            {quality?.warnings && quality.warnings.length > 0 && (
              <Notice tone="warning" title="Quality notes">
                <ul className="list-disc pl-4">
                  {quality.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </Notice>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
