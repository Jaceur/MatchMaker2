"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Lead } from "@/lib/types";
import {
  SwipeCard,
  type PassPayload,
  type ApprovePayload,
} from "@/components/SwipeCard";
import { Button, Card, Spinner } from "@/components/ui";

export default function SwipePage() {
  const [queue, setQueue] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setQueue(await api.get<Lead[]>("/leads/pending"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load leads.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const current = queue[0];

  async function act(path: string, payload: PassPayload | ApprovePayload) {
    if (!current || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/leads/${current.id}/${path}`, payload);
      setQueue((q) => q.slice(1));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold">🔥 Triage</h1>
        <p className="mt-1 text-sm text-muted">
          Review each lead, validate the sources, then Pass or Approve.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-20">
          <Spinner className="h-8 w-8" />
        </div>
      ) : !current ? (
        <Card className="p-10 text-center">
          <div className="text-4xl">🎉</div>
          <h2 className="mt-3 text-lg font-semibold">Inbox zero</h2>
          <p className="mt-1 text-sm text-muted">
            You&apos;ve triaged all your assigned leads.
          </p>
          <Button variant="outline" onClick={load} className="mt-5">
            Check for new leads
          </Button>
        </Card>
      ) : (
        <>
          <SwipeCard
            key={current.id}
            lead={current}
            busy={busy}
            onPass={(p) => act("pass", p)}
            onApprove={(p) => act("approve", p)}
          />
          <p className="mt-4 text-center text-sm text-muted">
            {queue.length} lead{queue.length === 1 ? "" : "s"} left to review
          </p>
        </>
      )}
    </div>
  );
}
