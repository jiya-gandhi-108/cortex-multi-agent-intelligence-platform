import { Check, CircleDashed, X } from "lucide-react";
import Link from "next/link";

import { CitationChip } from "@/components/evidence";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";

const STEPS = [
  {
    title: "Plan",
    body: "A planning agent breaks your objective into concrete tasks, decides which can run in parallel, and can wait for your approval before spending anything.",
  },
  {
    title: "Research",
    body: "Research and competitor agents write search queries, fetch the pages themselves and extract findings. Every finding keeps a link to the page it came from.",
  },
  {
    title: "Verify",
    body: "A verification agent checks each claim against the excerpts it cites. Unsupported claims are dropped; thin evidence triggers another research round.",
  },
  {
    title: "Report",
    body: "The report is written from verified claims only, with inline citations, a sourced competitor table, and the limitations stated plainly.",
  },
];

const CAPABILITIES = [
  ["Market and industry research", "Market structure, trends, segments and regulation, from sources you can open and check."],
  ["Competitor intelligence", "Products, positioning and pricing. A price appears only when a retrieved page states it; otherwise it reads Unknown."],
  ["Evidence you can audit", "Each claim is marked supported, partly supported or unsupported, with an evidence score whose formula is documented."],
  ["Live progress", "Watch agents plan, search and verify as it happens, including retries and token use."],
  ["Your approval when it matters", "Review the research plan before any searching starts, and cancel a run at any point."],
  ["Reports you can take with you", "Read in the browser, or download as Markdown or PDF."],
];

function ExampleExcerpt() {
  return (
    <figure className="rounded-[var(--radius-panel)] border border-line bg-surface">
      <figcaption className="flex items-center justify-between border-b border-line px-5 py-3 text-sm text-ink-muted">
        <span>Key findings, illustrative example</span>
        <span className="font-mono text-xs text-ink-faint">v1</span>
      </figcaption>
      <div className="space-y-5 px-5 py-5 font-serif text-[1.02rem] leading-relaxed">
        <p className="flex gap-3">
          <Check className="mt-1.5 size-4 shrink-0 text-supported" aria-label="Supported" />
          <span>
            Clinic software buyers in smaller cities cite onboarding and local-language support as the main
            barriers to switching vendors. <CitationChip citationKey="S3f9a2c" />{" "}
            <CitationChip citationKey="S81d0e4" />
          </span>
        </p>
        <p className="flex gap-3">
          <CircleDashed className="mt-1.5 size-4 shrink-0 text-partial" aria-label="Partly supported" />
          <span>
            Most vendors price per practitioner per month; one publishes an annual-only tier.{" "}
            <CitationChip citationKey="S5b27c9" />
          </span>
        </p>
        <p className="flex gap-3 text-ink-faint line-through decoration-ink-faint/60">
          <X className="mt-1.5 size-4 shrink-0 text-unsupported no-underline" aria-label="Unsupported" />
          <span>The segment will double within two years.</span>
        </p>
      </div>
      <p className="border-t border-line px-5 py-3 text-sm text-ink-muted">
        The struck-out claim had no support in its cited source, so it never reaches the report.
      </p>
    </figure>
  );
}

export default function LandingPage() {
  return (
    <div className="min-h-dvh">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 sm:px-8">
        <Logo />
        <nav className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/login">Sign in</Link>
          </Button>
          <Button asChild size="sm">
            <Link href="/register">Create account</Link>
          </Button>
        </nav>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-5 pb-20 pt-14 sm:px-8 lg:grid-cols-[1.05fr_1fr] lg:pt-24">
          <div>
            <h1 className="max-w-[15ch] text-[length:clamp(2.4rem,5.4vw,3.2rem)] leading-[1.06]">
              Research that shows its sources.
            </h1>
            <p className="mt-6 max-w-[54ch] text-lg leading-relaxed text-ink-muted">
              Give Cortex a question about a market, a competitor set or a strategic decision. A team of agents
              plans the work, searches the web, checks every claim against the pages it found, and hands you a
              cited report with its limits stated.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Button asChild size="lg">
                <Link href="/register">Start researching</Link>
              </Button>
              <Button asChild size="lg" variant="secondary">
                <Link href="/login">Sign in</Link>
              </Button>
            </div>
          </div>
          <ExampleExcerpt />
        </section>

        <section className="border-t border-line">
          <div className="mx-auto max-w-6xl px-5 py-20 sm:px-8">
            <h2 className="text-2xl">How Cortex works</h2>
            <ol className="mt-10 grid gap-10 md:grid-cols-2 lg:grid-cols-4 lg:gap-8">
              {STEPS.map((step, i) => (
                <li key={step.title} className="border-t border-line-strong pt-5">
                  <span className="font-serif text-3xl text-iris">{i + 1}</span>
                  <h3 className="mt-3 font-sans text-base font-semibold tracking-normal">{step.title}</h3>
                  <p className="mt-2 text-ink-muted">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="border-t border-line">
          <div className="mx-auto grid max-w-6xl gap-12 px-5 py-20 sm:px-8 lg:grid-cols-[1fr_2fr]">
            <div>
              <h2 className="text-2xl">What you get</h2>
              <p className="mt-3 max-w-[38ch] text-ink-muted">
                Built for analysts, founders and strategy teams who need to know where an answer came from.
              </p>
            </div>
            <dl className="grid gap-x-10 gap-y-8 sm:grid-cols-2">
              {CAPABILITIES.map(([title, body]) => (
                <div key={title}>
                  <dt className="font-medium">{title}</dt>
                  <dd className="mt-1.5 text-ink-muted">{body}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section className="border-t border-line">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-6 px-5 py-16 sm:px-8">
            <p className="max-w-[40ch] font-serif text-2xl">Ask a hard question. Get an answer you can check.</p>
            <Button asChild size="lg">
              <Link href="/register">Create your account</Link>
            </Button>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-4 px-5 py-6 text-sm text-ink-faint sm:px-8">
          <Logo className="opacity-70" />
          <span>Findings are only as complete as the public web. Every report says what it could not verify.</span>
        </div>
      </footer>
    </div>
  );
}
