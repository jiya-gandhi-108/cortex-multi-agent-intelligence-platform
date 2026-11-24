/** Wordmark: a bracketed citation mark, the product's core idea, beside the name. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className ?? ""}`}>
      <svg viewBox="0 0 24 24" className="size-5 text-iris" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M8 4H5v16h3" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M16 4h3v16h-3" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
      </svg>
      <span className="font-serif text-lg font-semibold tracking-tight text-ink">Cortex</span>
    </span>
  );
}
