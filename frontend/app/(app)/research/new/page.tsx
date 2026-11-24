"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { FieldError, Hint, Input, Label, Switch, Textarea } from "@/components/ui/form";
import { Notice, PageHeader, Panel } from "@/components/ui/misc";
import { useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import { ApiError, research, settings } from "@/services/api";
import type { ResearchDepth } from "@/types";

const DEPTHS: { value: ResearchDepth; label: string; body: string }[] = [
  { value: "quick", label: "Quick", body: "Up to 4 tasks, 3 sources each. A first look in a few minutes." },
  { value: "standard", label: "Standard", body: "Up to 7 tasks, 4 sources each. Balanced coverage." },
  { value: "deep", label: "Deep", body: "Up to 10 tasks, 5 sources each. Broadest evidence, most tokens." },
];

const FOCUS: { value: string; label: string }[] = [
  { value: "market_size", label: "Market size" },
  { value: "trends", label: "Trends" },
  { value: "competitors", label: "Competitors" },
  { value: "pricing", label: "Pricing" },
  { value: "customer_segments", label: "Customer segments" },
  { value: "technology", label: "Technology" },
  { value: "regulation", label: "Regulation" },
  { value: "opportunities", label: "Opportunities" },
  { value: "risks", label: "Risks" },
];

const EXAMPLE =
  "Research the Indian healthcare SaaS market, identify the major competitors, compare their products and pricing, find underserved customer segments, and recommend five business opportunities.";

export default function NewResearchPage() {
  const router = useRouter();
  const { data: prefs } = useApi("settings", settings.get);
  const [objective, setObjective] = useState("");
  const [title, setTitle] = useState("");
  const [depth, setDepth] = useState<ResearchDepth | null>(null);
  const [focus, setFocus] = useState<string[]>([]);
  const [approval, setApproval] = useState<boolean | null>(null);
  const [iterations, setIterations] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [error, setError] = useState<string>();
  const [objectiveError, setObjectiveError] = useState<string>();
  const [busy, setBusy] = useState(false);

  // Defaults come from the user's settings until they choose otherwise.
  const effectiveDepth = depth ?? prefs?.default_research_depth ?? "standard";
  const effectiveApproval = approval ?? prefs?.require_plan_approval ?? false;
  const serverMaxTokens = prefs?.server_limits?.max_tokens_per_project;
  const serverMaxIterations = prefs?.server_limits?.max_research_iterations;

  async function submit(e: React.FormEvent, startNow: boolean) {
    e.preventDefault();
    if (objective.trim().length < 20) {
      setObjectiveError("Describe the objective in at least 20 characters so the planner has enough to work with.");
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const project = await research.create({
        objective: objective.trim(),
        title: title.trim() || null,
        depth: effectiveDepth,
        focus_areas: focus,
        limits: {
          require_plan_approval: effectiveApproval,
          max_research_iterations: iterations === "" ? null : Number(iterations),
          max_tokens: maxTokens === "" ? null : Number(maxTokens),
        },
      });
      if (startNow) {
        try {
          await research.start(project.id);
        } catch (err) {
          // Project exists; show the reason on its page (e.g. missing API keys).
          const e = err as ApiError;
          router.push(`/research/${project.id}?start_error=${encodeURIComponent(e.message)}`);
          return;
        }
      }
      router.push(`/research/${project.id}`);
    } catch (err) {
      setError((err as ApiError).message);
      setBusy(false);
    }
  }

  const toggleFocus = (value: string) =>
    setFocus((prev) => (prev.includes(value) ? prev.filter((f) => f !== value) : [...prev, value]));

  return (
    <>
      <PageHeader
        title="New research"
        description="Describe what you need to know. Cortex turns it into a plan you can watch run."
      />
      <form onSubmit={(e) => submit(e, true)} className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-8">
          {error && <Notice tone="error" title="The project wasn't created">{error}</Notice>}

          <div className="space-y-2">
            <Label htmlFor="objective">Research objective</Label>
            <Textarea
              id="objective"
              rows={7}
              maxLength={4000}
              value={objective}
              onChange={(e) => {
                setObjective(e.target.value);
                if (e.target.value.trim().length >= 20) setObjectiveError(undefined);
              }}
              placeholder={EXAMPLE}
              aria-invalid={Boolean(objectiveError)}
              aria-describedby="objective-hint objective-error"
              className="font-serif text-[1.02rem]"
            />
            <div className="flex justify-between gap-4">
              <Hint id="objective-hint">
                Name the market, the geography and the decisions you need to make.{" "}
                {!objective && (
                  <button type="button" className="text-iris-strong hover:underline" onClick={() => setObjective(EXAMPLE)}>
                    Use the example
                  </button>
                )}
              </Hint>
              <span className="shrink-0 text-xs text-ink-faint tabular-nums">{objective.length}/4000</span>
            </div>
            <FieldError id="objective-error">{objectiveError}</FieldError>
          </div>

          <div className="space-y-2">
            <Label htmlFor="title">Title</Label>
            <Input
              id="title"
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Optional. Defaults to the first line of the objective."
            />
          </div>

          <fieldset className="space-y-3">
            <legend className="text-sm font-medium">Depth</legend>
            <div className="grid gap-3 sm:grid-cols-3">
              {DEPTHS.map((d) => (
                <label
                  key={d.value}
                  className={cn(
                    "cursor-pointer rounded-[var(--radius-control)] border border-line p-3.5 transition-colors hover:border-line-strong has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-iris",
                    effectiveDepth === d.value && "border-iris bg-iris-soft",
                  )}
                >
                  <input
                    type="radio"
                    name="depth"
                    value={d.value}
                    checked={effectiveDepth === d.value}
                    onChange={() => setDepth(d.value)}
                    className="sr-only"
                  />
                  <span className="font-medium">{d.label}</span>
                  <span className="mt-1 block text-sm text-ink-muted">{d.body}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="space-y-3">
            <legend className="text-sm font-medium">Focus areas</legend>
            <Hint>Optional. Leave empty and the planner covers what the objective needs.</Hint>
            <div className="flex flex-wrap gap-2">
              {FOCUS.map((f) => {
                const on = focus.includes(f.value);
                return (
                  <button
                    key={f.value}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleFocus(f.value)}
                    className={cn(
                      "h-8 rounded-full border px-3 text-sm transition-colors",
                      on ? "border-iris bg-iris-soft text-ink" : "border-line text-ink-muted hover:border-line-strong hover:text-ink",
                    )}
                  >
                    {f.label}
                  </button>
                );
              })}
            </div>
          </fieldset>
        </div>

        <aside className="space-y-6">
          <Panel className="space-y-5 p-5">
            <h2 className="font-sans text-base font-semibold tracking-normal">Execution limits</h2>
            <div className="flex items-start justify-between gap-4">
              <div>
                <Label htmlFor="approval">Approve the plan first</Label>
                <Hint className="mt-0.5">Pause after planning so you can review tasks before any searching.</Hint>
              </div>
              <Switch id="approval" checked={effectiveApproval} onCheckedChange={setApproval} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="iterations">Follow-up research rounds</Label>
              <Input
                id="iterations"
                type="number"
                min={0}
                max={serverMaxIterations ?? 5}
                inputMode="numeric"
                placeholder={`Default ${prefs?.max_research_iterations ?? serverMaxIterations ?? 2}`}
                value={iterations}
                onChange={(e) => setIterations(e.target.value)}
              />
              <Hint>Extra rounds when verification finds thin evidence.</Hint>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="tokens">Token budget</Label>
              <Input
                id="tokens"
                type="number"
                min={5000}
                step={1000}
                inputMode="numeric"
                placeholder={serverMaxTokens ? `Up to ${serverMaxTokens.toLocaleString()}` : "Server default"}
                value={maxTokens}
                onChange={(e) => setMaxTokens(e.target.value)}
              />
              <Hint>The run stops with a clear error rather than exceed this.</Hint>
            </div>
          </Panel>
          <div className="flex flex-col gap-2">
            <Button type="submit" size="lg" disabled={busy}>
              {busy ? "Starting…" : "Start research"}
            </Button>
            <Button type="button" variant="ghost" disabled={busy} onClick={(e) => submit(e, false)}>
              Save without starting
            </Button>
          </div>
        </aside>
      </form>
    </>
  );
}
