const numberFmt = new Intl.NumberFormat("en-US");

export function formatNumber(value: number | null | undefined): string {
  return value == null ? "–" : numberFmt.format(value);
}

export function formatTokens(value: number | null | undefined): string {
  if (value == null) return "–";
  if (value < 10_000) return numberFmt.format(value);
  return `${(value / 1000).toFixed(value < 100_000 ? 1 : 0)}k`;
}

/** Costs arrive as decimal strings. Zero usually means "no price configured". */
export function formatCost(value: string | number | null | undefined): string {
  if (value == null) return "–";
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (!Number.isFinite(n)) return "–";
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "–";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "–";
  return new Date(value).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "–";
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "";
  return new Date(value).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "–";
  const diff = (Date.now() - new Date(value).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return formatDate(value);
}

export function percent(value: number | null | undefined, digits = 0): string {
  return value == null ? "–" : `${(value * 100).toFixed(digits)}%`;
}
