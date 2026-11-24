"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/services/api";

interface ApiState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => Promise<void>;
}

interface Entry<T> {
  key: string;
  data?: T;
  error?: ApiError;
}

/**
 * Minimal data hook: fetch when `key` changes, optional polling, manual reload.
 * Pass `key = null` to skip. Results are stored with the key that produced them, so
 * `loading` is derived (no state resets inside effects) and stale responses are ignored.
 * Data from the previous key stays visible while the next one loads.
 */
export function useApi<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  options: { refreshMs?: number } = {},
): ApiState<T> {
  const [entry, setEntry] = useState<Entry<T>>();
  const fetcherRef = useRef(fetcher);
  const keyRef = useRef(key);

  useEffect(() => {
    fetcherRef.current = fetcher;
    keyRef.current = key;
  });

  const load = useCallback(async (requested: string) => {
    try {
      const data = await fetcherRef.current();
      if (keyRef.current === requested) setEntry({ key: requested, data });
    } catch (err) {
      if (keyRef.current === requested) {
        const error = err instanceof ApiError ? err : new ApiError(0, "error", String(err));
        setEntry((prev) => ({ key: requested, data: prev?.data, error }));
      }
    }
  }, []);

  useEffect(() => {
    if (key === null) return;
    void load(key);
    if (!options.refreshMs) return;
    const id = window.setInterval(() => void load(key), options.refreshMs);
    return () => window.clearInterval(id);
  }, [key, options.refreshMs, load]);

  const reload = useCallback(async () => {
    if (keyRef.current !== null) await load(keyRef.current);
  }, [load]);

  return {
    data: entry?.data,
    error: entry?.key === key ? entry.error : undefined,
    loading: key !== null && entry?.key !== key,
    reload,
  };
}
