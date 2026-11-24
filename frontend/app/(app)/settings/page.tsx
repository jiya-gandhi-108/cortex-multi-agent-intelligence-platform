"use client";

import { Check } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Hint, Input, Label, Select, Switch } from "@/components/ui/form";
import { Notice, PageHeader, Panel, PanelHeader, Skeleton } from "@/components/ui/misc";
import { useApi } from "@/hooks/use-api";
import { formatDate } from "@/lib/format";
import { ApiError, auth, settings } from "@/services/api";
import type { ResearchDepth, SettingsUpdate } from "@/types";

const PROVIDER_NAMES: Record<string, string> = { groq: "Groq", gemini: "Google Gemini" };

export default function SettingsPage() {
  const router = useRouter();
  const prefs = useApi("settings", settings.get);
  const me = useApi("me", auth.me);
  // The draft remembers which server version it was edited from; when the server data
  // changes (e.g. after saving) the form re-derives from it, with no syncing effect.
  const [draft, setDraft] = useState<{ base: string; values: SettingsUpdate }>();
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  const base = prefs.data?.updated_at ?? "";
  const form: SettingsUpdate =
    draft && draft.base === base
      ? draft.values
      : prefs.data
        ? {
            preferred_provider: prefs.data.preferred_provider,
            preferred_model: prefs.data.preferred_model,
            default_research_depth: prefs.data.default_research_depth,
            max_research_iterations: prefs.data.max_research_iterations,
            max_tokens_per_project: prefs.data.max_tokens_per_project,
            max_cost_usd_per_project: prefs.data.max_cost_usd_per_project,
            require_plan_approval: prefs.data.require_plan_approval,
          }
        : {};

  const update = (patch: SettingsUpdate) => {
    setSaved(false);
    setDraft({ base, values: { ...form, ...patch } });
  };
  const num = (v: string) => (v === "" ? null : Number(v));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      await settings.update(form);
      await prefs.reload();
      setSaved(true);
    } catch (err) {
      const apiErr = err as ApiError;
      setError(apiErr.code === "validation_error" ? "Some values are outside the allowed range. Check the limits below." : apiErr.message);
    } finally {
      setSaving(false);
    }
  }

  async function signOut() {
    await auth.logout().catch(() => undefined);
    router.replace("/login");
  }

  const data = prefs.data;
  const limits = data?.server_limits ?? {};
  const providers = data?.providers ?? [];
  const selected = providers.find((p) => p.provider === form.preferred_provider);

  return (
    <>
      <PageHeader title="Settings" description="Defaults for new research projects, and your account." />
      {!data ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <form onSubmit={save} className="space-y-6">
            {error && <Notice tone="error">{error}</Notice>}
            <Panel>
              <PanelHeader title="Language model" description="Which provider runs the agents for your new projects." />
              <div className="space-y-5 px-5 py-5">
                <div className="space-y-1.5">
                  <Label htmlFor="provider">Provider</Label>
                  <Select
                    id="provider"
                    value={form.preferred_provider ?? ""}
                    onChange={(e) => update({ preferred_provider: e.target.value as "groq" | "gemini", preferred_model: null })}
                  >
                    {providers.map((p) => (
                      <option key={p.provider} value={p.provider}>
                        {PROVIDER_NAMES[p.provider] ?? p.provider}
                        {p.configured ? "" : " (not configured on this server)"}
                      </option>
                    ))}
                  </Select>
                  {selected && !selected.configured && (
                    <Hint className="text-partial">
                      This server has no API key for {PROVIDER_NAMES[selected.provider]}, so runs would fail to start.
                    </Hint>
                  )}
                  {selected?.configured && <Hint>Models: {selected.models.join(", ")}</Hint>}
                </div>
              </div>
            </Panel>

            <Panel>
              <PanelHeader
                title="Research defaults"
                description="Applied to new projects. Each project can tighten these, never exceed the server's limits."
              />
              <div className="grid gap-5 px-5 py-5 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="depth">Default depth</Label>
                  <Select
                    id="depth"
                    value={form.default_research_depth ?? "standard"}
                    onChange={(e) => update({ default_research_depth: e.target.value as ResearchDepth })}
                  >
                    <option value="quick">Quick</option>
                    <option value="standard">Standard</option>
                    <option value="deep">Deep</option>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="iter">Follow-up research rounds</Label>
                  <Input
                    id="iter"
                    type="number"
                    min={0}
                    max={limits.max_research_iterations ?? 5}
                    placeholder={`Server default ${limits.max_research_iterations ?? "–"}`}
                    value={form.max_research_iterations ?? ""}
                    onChange={(e) => update({ max_research_iterations: num(e.target.value) })}
                  />
                  <Hint>Server maximum: {limits.max_research_iterations ?? "–"}</Hint>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="tokens">Token budget per project</Label>
                  <Input
                    id="tokens"
                    type="number"
                    min={5000}
                    step={1000}
                    placeholder={`Server default ${limits.max_tokens_per_project?.toLocaleString() ?? "–"}`}
                    value={form.max_tokens_per_project ?? ""}
                    onChange={(e) => update({ max_tokens_per_project: num(e.target.value) })}
                  />
                  <Hint>Server maximum: {limits.max_tokens_per_project?.toLocaleString() ?? "–"}</Hint>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cost">Cost limit per project (USD)</Label>
                  <Input
                    id="cost"
                    type="number"
                    min={0}
                    step={0.5}
                    placeholder={`Server default $${limits.max_cost_usd_per_project ?? "–"}`}
                    value={form.max_cost_usd_per_project ?? ""}
                    onChange={(e) => update({ max_cost_usd_per_project: e.target.value === "" ? null : e.target.value })}
                  />
                  <Hint>Only enforced for models with a configured price.</Hint>
                </div>
                <div className="flex items-start justify-between gap-4 sm:col-span-2">
                  <div>
                    <Label htmlFor="approval">Approve plans before research starts</Label>
                    <Hint className="mt-0.5">New projects pause after planning until you approve.</Hint>
                  </div>
                  <Switch
                    id="approval"
                    checked={Boolean(form.require_plan_approval)}
                    onCheckedChange={(v) => update({ require_plan_approval: v })}
                  />
                </div>
              </div>
              <div className="flex items-center justify-end gap-3 border-t border-line px-5 py-3.5">
                {saved && (
                  <span className="flex items-center gap-1.5 text-sm text-supported" role="status">
                    <Check className="size-4" aria-hidden /> Saved
                  </span>
                )}
                <Button type="submit" disabled={saving}>
                  {saving ? "Saving…" : "Save settings"}
                </Button>
              </div>
            </Panel>
          </form>

          <Panel className="self-start">
            <PanelHeader title="Account" />
            <dl className="space-y-3 px-5 py-4 text-sm">
              <div>
                <dt className="text-ink-muted">Email</dt>
                <dd className="mt-0.5 break-all">{me.data?.email ?? "–"}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Member since</dt>
                <dd className="mt-0.5">{formatDate(me.data?.created_at)}</dd>
              </div>
            </dl>
            <div className="border-t border-line px-5 py-3.5">
              <Button variant="secondary" onClick={signOut} className="w-full">
                Sign out
              </Button>
            </div>
          </Panel>
        </div>
      )}
    </>
  );
}
