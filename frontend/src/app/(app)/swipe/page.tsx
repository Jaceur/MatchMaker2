"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

interface Point {
  x: number;
  y: number;
}

export default function SwipePage() {
  const [queue, setQueue] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Approval celebration state
  const [celebrating, setCelebrating] = useState(false);
  const [tick, setTick] = useState<Point | null>(null);
  const [fly, setFly] = useState<{ from: Point; to: Point } | null>(null);
  const cardWrapRef = useRef<HTMLDivElement>(null);

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
  const peeks = queue.slice(1, 3);

  function fireApi(lead: Lead, path: "pass" | "approve", payload: PassPayload | ApprovePayload) {
    api.post(`/leads/${lead.id}/${path}`, payload).catch((err) => {
      setError(
        `Couldn't save that ${path} — the lead is back at the top. ${
          err instanceof ApiError ? err.message : ""
        }`,
      );
      setQueue((q) => (q.some((l) => l.id === lead.id) ? q : [lead, ...q]));
    });
  }

  // Pass advances instantly (no ceremony).
  function pass(lead: Lead, payload: PassPayload) {
    setError(null);
    setQueue((q) => q.filter((l) => l.id !== lead.id));
    fireApi(lead, "pass", payload);
  }

  // Approve: tick pops on the card, then a box flies into the My Pipeline nav.
  function approve(lead: Lead, payload: ApprovePayload) {
    if (celebrating) return;
    setError(null);
    fireApi(lead, "approve", payload);

    const cardRect = cardWrapRef.current?.getBoundingClientRect();
    const navRect = document
      .querySelector<HTMLElement>('[data-nav="/pipeline"]')
      ?.getBoundingClientRect();
    const anchor: Point | null = cardRect
      ? { x: cardRect.left + cardRect.width / 2, y: cardRect.top + 120 }
      : null;

    setCelebrating(true);
    if (anchor) setTick(anchor);

    window.setTimeout(() => {
      setTick(null);
      setQueue((q) => q.filter((l) => l.id !== lead.id)); // advance the deck
      setCelebrating(false);
      if (anchor && navRect) {
        setFly({ from: anchor, to: { x: navRect.left + navRect.width / 2, y: navRect.top + navRect.height / 2 } });
        window.setTimeout(() => setFly(null), 650);
      }
    }, 800);
  }

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold">🔥 Triage</h1>
        <p className="mt-1 text-sm text-muted">Review each lead, then Pass or Approve.</p>
      </header>

      {error && (
        <div className="mb-4 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>
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
          <div ref={cardWrapRef} className="relative mx-auto w-full max-w-[420px]">
            {peeks.map((lead, i) => {
              const depth = i + 1;
              return (
                <motion.div
                  key={lead.id}
                  aria-hidden
                  className="pointer-events-none absolute inset-x-0 top-0"
                  initial={false}
                  animate={{ x: depth * 16, scale: 1 - depth * 0.04, opacity: depth === 1 ? 0.55 : 0.3 }}
                  transition={{ type: "spring", stiffness: 320, damping: 32 }}
                  style={{ zIndex: 10 - depth }}
                >
                  <Card className="overflow-hidden">
                    <LeadProfile lead={lead} />
                  </Card>
                </motion.div>
              );
            })}

            <motion.div
              key={current.id}
              className="relative z-20"
              initial={{ x: 16, scale: 0.96, opacity: 0.4 }}
              animate={{ x: 0, scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 320, damping: 30 }}
            >
              <SwipeCard
                lead={current}
                busy={celebrating}
                onPass={(p) => pass(current, p)}
                onApprove={(p) => approve(current, p)}
              />
            </motion.div>
          </div>

          <p className="mt-4 text-center text-sm text-muted">
            {queue.length} lead{queue.length === 1 ? "" : "s"} left to review
          </p>
        </>
      )}

      {/* Approval tick */}
      {tick && (
        <div
          className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-1/2"
          style={{ left: tick.x, top: tick.y }}
        >
          <motion.div
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: "spring", stiffness: 400, damping: 14 }}
            className="flex h-20 w-20 items-center justify-center rounded-full bg-success text-white shadow-lg"
          >
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </motion.div>
        </div>
      )}

      {/* Box flying into My Pipeline */}
      {fly && (
        <div
          className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-1/2"
          style={{ left: fly.from.x, top: fly.from.y }}
        >
          <motion.div
            initial={{ x: 0, y: 0, scale: 1, opacity: 1 }}
            animate={{ x: fly.to.x - fly.from.x, y: fly.to.y - fly.from.y, scale: 0.25, opacity: 0 }}
            transition={{ duration: 0.6, ease: "easeInOut" }}
            className="flex h-12 w-12 items-center justify-center rounded-xl bg-success text-white shadow-lg"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </motion.div>
        </div>
      )}
    </div>
  );
}
