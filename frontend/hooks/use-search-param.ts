"use client";

import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

/**
 * Reads a query parameter without a Suspense boundary: the server snapshot is empty and
 * React reconciles to the real value after hydration (no mismatch warning).
 */
export function useSearchParam(name: string): string | null {
  const search = useSyncExternalStore(
    subscribe,
    () => window.location.search,
    () => "",
  );
  return new URLSearchParams(search).get(name);
}
