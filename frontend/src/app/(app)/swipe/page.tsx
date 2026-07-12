"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Lead } from "@/lib/types";
import {
  SwipeCard,
  type PassPayload,
  type ApprovePayload,
} from "@/components/SwipeCard";
import { LeadProfile } from "@/components/LeadProfile";
import { Button, Card, Spinner } from "@/components/ui";

export default function SwipePage() {
  const [queue, setQueue] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
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
  const peeks = queue.slice(1, 3); // preloaded, rendered behind for a smooth deck

  // Optimistic advance: drop the card immediately so the next one is instant,
  // fire the API in the background, and roll the lead back if it fails.
  function act(lead: Lead, path: "pass" | "approve", payload: PassPayload | ApprovePayload) {
    setError(null);
    setQueue((q) => q.filter((l) => l.id !== lead.id));
    api.post(`/leads/${lead.id}/${path}`, payload).catch((err) => {
      setError(
        `Couldn't save that ${path} — the lead is back at the top. ${
          err instanceof ApiError ? err.message : ""
        }`,
      );
      setQueue((q) => (q.some((l) => l.id === lead.id) ? q : [lead, ...q]));
    });
  }

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold">🔥 Triage</h1>
        <p className="mt-1 text-sm text-muted">
          Review each lead, then Pass or Approve.
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
        <Card className="mx-auto max-w-[420px] p-10 text-center">
          <div className="text-4xl">🎉</div>
          <h2 className="mt-3 text-lg font-semibold">Inbox zero</h2>
          <p className="mt-1 text-sm text-muted">You&apos;ve triaged all your assigned leads.</p>
          <Button variant="outline" onClick={load} className="mt-5">
            Check for new leads
          </Button>
        </Card>
      ) : (
        <>
          <div className="relative mx-auto w-full max-w-[420px]">
            {/* Preloaded next cards, stacked behind (deepest first) */}
            {peeks
              .slice()
              .reverse()
              .map((lead, idx) => {
                const depth = peeks.length - idx; // 2 for the furthest, 1 for the nearest
                return (
                  <div
                    key={lead.id}
                    aria-hidden
                    className="pointer-events-none absolute inset-x-0 top-0"
                    style={{
                      transformOrigin: "top center",
                      transform: `scale(${1 - depth * 0.04}) translateY(${depth * 10}px)`,
                      zIndex: 10 - depth,
                      opacity: 0.6,
                    }}
                  >
                    <Card className="overflow-hidden">
                      <LeadProfile lead={lead} />
                    </Card>
                  </div>
                );
              })}

            {/* Top interactive card */}
            <div className="relative z-20">
              <SwipeCard
                key={current.id}
                lead={current}
                busy={false}
                onPass={(p) => act(current, "pass", p)}
                onApprove={(p) => act(current, "approve", p)}
              />
            </div>
          </div>

          <p className="mt-4 text-center text-sm text-muted">
            {queue.length} lead{queue.length === 1 ? "" : "s"} left to review
          </p>
        </>
      )}
    </div>
  );
}
