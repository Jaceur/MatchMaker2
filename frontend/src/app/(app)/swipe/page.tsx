"use client";

import { useCallback, useEffect, useState } from "react";
import { motion } from "motion/react";
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
            {/* Preloaded next cards — offset right + faded so the deck is visible.
                When the top card leaves, each springs forward to its new depth. */}
            {peeks.map((lead, i) => {
              const depth = i + 1; // 1 = nearest, 2 = furthest
              return (
                <motion.div
                  key={lead.id}
                  aria-hidden
                  className="pointer-events-none absolute inset-x-0 top-0"
                  initial={false}
                  animate={{
                    x: depth * 16,
                    scale: 1 - depth * 0.04,
                    opacity: depth === 1 ? 0.55 : 0.3,
                  }}
                  transition={{ type: "spring", stiffness: 320, damping: 32 }}
                  style={{ zIndex: 10 - depth }}
                >
                  <Card className="overflow-hidden">
                    <LeadProfile lead={lead} />
                  </Card>
                </motion.div>
              );
            })}

            {/* Top interactive card — enters from the deck (right + faded) to front */}
            <motion.div
              key={current.id}
              className="relative z-20"
              initial={{ x: 16, scale: 0.96, opacity: 0.4 }}
              animate={{ x: 0, scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 320, damping: 30 }}
            >
              <SwipeCard
                lead={current}
                busy={false}
                onPass={(p) => act(current, "pass", p)}
                onApprove={(p) => act(current, "approve", p)}
              />
            </motion.div>
          </div>

          <p className="mt-4 text-center text-sm text-muted">
            {queue.length} lead{queue.length === 1 ? "" : "s"} left to review
          </p>
        </>
      )}
    </div>
  );
}
