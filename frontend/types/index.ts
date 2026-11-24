/**
 * Friendly aliases over the generated OpenAPI types (types/api.ts).
 * Regenerate with scripts/gen_api_types.sh whenever the backend schema changes.
 */
import type { components } from "./api";

type S = components["schemas"];

export type User = S["UserRead"];
export type Project = S["ProjectRead"];
export type ProjectSummary = S["ProjectSummary"];
export type ProjectCreate = S["ProjectCreate"];
export type ProjectStatus = S["ProjectStatus"];
export type ResearchDepth = S["ResearchDepth"];
export type RunStatus = S["StatusRead"];
export type Task = S["TaskRead"];
export type AgentSummary = S["AgentSummary"];
export type Execution = S["ExecutionRead"];
export type Source = S["SourceRead"];
export type Report = S["ReportRead"];
export type Dashboard = S["DashboardRead"];
export type UserSettings = S["SettingsRead"];
export type SettingsUpdate = S["SettingsUpdate"];
export type ProviderStatus = S["ProviderStatus"];

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** One live workflow event (backend app/graph/events.py WorkflowEvent). */
export interface WorkflowEvent {
  type: string;
  project_id: string;
  at: string;
  stage: string | null;
  task_key: string | null;
  agent: string | null;
  data: Record<string, unknown>;
}

export interface Verification {
  evidence_score: number;
  reliability_score: number;
  sufficient: boolean;
  reasons: string[];
  counts: {
    supported: number;
    partially_supported: number;
    unsupported: number;
    contradicted: number;
    total: number;
    usable: number;
    distinct_sources: number;
  };
  criteria?: Record<string, unknown>;
  outdated_sources?: string[];
  sources_needing_verification?: string[];
  contradictions?: { claim_id: string; contradicts: string[]; note: string }[];
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown };
  request_id?: string;
}
