import { cn } from "@/lib/utils";
import type { Verification } from "@/types";

/** A citation to a retrieved source. Citation keys are literal identifiers, hence monospace. */
export function CitationChip({
  citationKey,
  href,
  title,
  className,
}: {
  citationKey: string;
  href?: string;
  title?: string;
  className?: string;
}) {
  const classes = cn(
    "inline-flex h-5 items-center rounded-[4px] border border-iris/35 bg-iris-soft px-1.5 align-[0.1em] font-mono text-[0.7rem] leading-none text-iris-strong no-underline transition-colors hover:border-iris hover:text-ink",
    className,
  );
  return href ? (
    <a href={href} title={title ?? `Source ${citationKey}`} className={classes}>
      {citationKey}
    </a>
  ) : (
    <span title={title} className={classes}>
      {citationKey}
    </span>
  );
}

const SEGMENTS = [
  { key: "supported", label: "Supported", color: "bg-supported" },
  { key: "partially_supported", label: "Partly supported", color: "bg-partial" },
  { key: "unsupported", label: "Unsupported", color: "bg-unsupported" },
  { key: "contradicted", label: "Contradicted", color: "bg-unsupported/50" },
] as const;

/**
 * How the agents' claims fared against their sources. Proportions are of all checked
 * claims; counts are shown alongside so small samples aren't over-read.
 */
export function VerdictBar({ counts, className }: { counts: Verification["counts"]; className?: string }) {
  const total = Math.max(1, counts.total);
  return (
    <div className={className}>
      <div
        className="flex h-2.5 w-full overflow-hidden rounded-full bg-raised"
        role="img"
        aria-label={SEGMENTS.map((s) => `${counts[s.key]} ${s.label.toLowerCase()}`).join(", ")}
      >
        {SEGMENTS.map((s) =>
          counts[s.key] ? (
            <span key={s.key} className={cn("h-full", s.color)} style={{ width: `${(counts[s.key] / total) * 100}%` }} />
          ) : null,
        )}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-4">
        {SEGMENTS.map((s) => (
          <div key={s.key} className="flex items-center gap-2">
            <span className={cn("size-2 rounded-full", s.color)} aria-hidden />
            <dt className="text-ink-muted">{s.label}</dt>
            <dd className="ml-auto font-medium tabular-nums sm:ml-0">{counts[s.key]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Evidence score with its threshold marked; labelled as a heuristic, never a probability. */
export function ScoreMeter({ value, threshold = 0.6, label }: { value: number; threshold?: number; label: string }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const passed = value >= threshold;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-ink-muted">{label}</span>
        <span className={cn("font-serif text-2xl tabular-nums", passed ? "text-ink" : "text-partial")}>
          {value.toFixed(2)}
        </span>
      </div>
      <div className="relative mt-2 h-1.5 rounded-full bg-raised">
        <div className={cn("h-full rounded-full", passed ? "bg-supported" : "bg-partial")} style={{ width: `${pct}%` }} />
        <span
          className="absolute -top-1 h-3.5 w-px bg-ink-muted"
          style={{ left: `${threshold * 100}%` }}
          title={`Sufficiency threshold ${threshold}`}
        />
      </div>
    </div>
  );
}
