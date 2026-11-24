"use client";

import { useEffect, useRef, useState } from "react";

import { research } from "@/services/api";
import type { WorkflowEvent } from "@/types";

const EVENT_TYPES = [
  "status", "stage", "plan", "approval_required", "task_started", "task_retry", "task_completed",
  "task_failed", "task_skipped", "tool_call", "llm_call", "sources", "verification", "report",
  "error", "completed",
] as const;

const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted", "awaiting_approval"]);

export type StreamState = "idle" | "connecting" | "live" | "closed" | "error";

interface Connection {
  id: string;
  events: WorkflowEvent[];
  state: StreamState;
}

/**
 * Subscribes to a project's live event stream (Server-Sent Events).
 *
 * Uses the browser's EventSource against the same-origin proxy, so the session cookie is
 * sent automatically and the browser handles reconnection with Last-Event-ID. State is
 * stored per connection id (project + run), so a new run starts from an empty feed
 * without resetting state inside the effect.
 */
export function useRunEvents(
  projectId: string,
  active: boolean,
  streamKey: string,
  onStatusChange?: (status: string) => void,
) {
  // A new key opens a fresh connection (replaying the run so far): e.g. a new run, or a
  // paused run (awaiting approval / interrupted) becoming live again under the same run.
  const connectionId = `${projectId}:${streamKey}`;
  const [conn, setConn] = useState<Connection>();
  const callback = useRef(onStatusChange);

  useEffect(() => {
    callback.current = onStatusChange;
  });

  useEffect(() => {
    if (!active) return;
    const seen = new Set<string>();
    const source = new EventSource(research.eventsUrl(projectId));
    let finished = false;

    const patch = (fn: (c: Connection) => Connection) =>
      setConn((prev) => fn(prev?.id === connectionId ? prev : { id: connectionId, events: [], state: "connecting" }));

    const close = (state: StreamState) => {
      finished = true;
      source.close();
      patch((c) => ({ ...c, state }));
    };

    const handle = (message: MessageEvent<string>) => {
      if (message.lastEventId) {
        if (seen.has(message.lastEventId)) return;
        seen.add(message.lastEventId);
      }
      let event: WorkflowEvent;
      try {
        event = JSON.parse(message.data) as WorkflowEvent;
      } catch {
        return;
      }
      patch((c) => ({
        ...c,
        state: "live",
        events: c.events.length > 2000 ? [...c.events.slice(-1500), event] : [...c.events, event],
      }));
      if (event.type === "status") {
        const status = String(event.data.status ?? "");
        callback.current?.(status);
        if (TERMINAL.has(status)) close("closed");
      }
    };

    source.addEventListener("snapshot", (message) => {
      patch((c) => ({ ...c, state: "live" }));
      try {
        const snap = JSON.parse((message as MessageEvent<string>).data) as { run: number };
        if (snap.run === 0) close("closed"); // nothing has run yet
      } catch {
        /* ignore malformed snapshot */
      }
    });
    for (const type of EVENT_TYPES) source.addEventListener(type, handle as EventListener);
    source.onerror = () => {
      // The server ends the stream when a run finishes or pauses; EventSource would
      // otherwise reconnect forever.
      if (finished) return;
      if (source.readyState === EventSource.CLOSED) close("error");
      else patch((c) => ({ ...c, state: "connecting" }));
    };
    return () => source.close();
  }, [projectId, active, connectionId]);

  const current = conn?.id === connectionId ? conn : undefined;
  const state: StreamState = current?.state ?? (active ? "connecting" : "idle");
  return { events: current?.events ?? [], state };
}
