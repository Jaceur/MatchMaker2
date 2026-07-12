"use client";

import { useRef, useState } from "react";
import {
  motion,
  AnimatePresence,
  useMotionValue,
  useTransform,
  animate,
} from "motion/react";
import type { Lead } from "@/lib/types";
import { LeadProfile } from "./LeadProfile";
import { Button, Card, Spinner } from "./ui";

const PASS_REASONS = [
  "Bad Industry",
  "Too Small",
  "No Public Info",
  "Competitor",
  "Out of Business",
  "Other",
];
const SWIPE_THRESHOLD = 110;

export interface PassPayload {
  rejection_reason: string;
  dwell_time_seconds: number;
}

export interface ApprovePayload {
  website_valid: boolean;
  linkedin_valid: boolean;
  corrected_website_url: string | null;
  corrected_linkedin_url: string | null;
}

type Mode = "idle" | "passing" | "approving";

/** One source shown on the face of the card: a clickable link, or "not found". */
function SourceLine({ icon, label, url }: { icon: string; label: string; url: string | null }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg bg-surface-2 px-3 py-2 text-sm">
      <span className="font-medium">
        {icon} {label}
      </span>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="max-w-[60%] truncate text-brand underline"
        >
          {url.replace(/^https?:\/\//, "")}
        </a>
      ) : (
        <span className="text-muted">not found</span>
      )}
    </div>
  );
}

/** A website/LinkedIn field in the approve overlay, with a "none found" toggle. */
function SourceInput({
  label,
  value,
  onChange,
  none,
  onToggleNone,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  none: boolean;
  onToggleNone: () => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label className="text-sm font-medium">{label}</label>
        <button
          type="button"
          onClick={onToggleNone}
          className={`rounded-md px-2 py-1 text-xs font-medium transition ${
            none ? "bg-danger text-white" : "bg-surface-2 text-muted hover:text-foreground"
          }`}
        >
          {none ? "✓ none found" : "none found"}
        </button>
      </div>
      <input
        value={none ? "" : value}
        onChange={(e) => onChange(e.target.value)}
        disabled={none}
        placeholder="https://…"
        className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)] disabled:opacity-50"
      />
    </div>
  );
}

export function SwipeCard({
  lead,
  onPass,
  onApprove,
  busy,
}: {
  lead: Lead;
  onPass: (p: PassPayload) => void;
  onApprove: (p: ApprovePayload) => void;
  busy: boolean;
}) {
  const scrapedWeb = lead.corrected_website_url || lead.website_url || null;
  const scrapedLi = lead.corrected_linkedin_url || lead.linkedin_url || null;

  const [mode, setMode] = useState<Mode>("idle");
  const startedAt = useRef(Date.now());

  // Approve-overlay source inputs (prefilled with whatever we scraped).
  const [webUrl, setWebUrl] = useState(scrapedWeb ?? "");
  const [liUrl, setLiUrl] = useState(scrapedLi ?? "");
  const [webNone, setWebNone] = useState(!scrapedWeb);
  const [liNone, setLiNone] = useState(!scrapedLi);

  const x = useMotionValue(0);
  const rotate = useTransform(x, [-300, 300], [-10, 10]);
  const approveHint = useTransform(x, [30, 140], [0, 1]);
  const passHint = useTransform(x, [-140, -30], [1, 0]);

  function openMode(m: Mode) {
    animate(x, 0, { type: "spring", stiffness: 300, damping: 30 });
    setMode(m);
  }

  function onDragEnd(_: unknown, info: { offset: { x: number } }) {
    if (busy) return;
    if (info.offset.x > SWIPE_THRESHOLD) openMode("approving");
    else if (info.offset.x < -SWIPE_THRESHOLD) openMode("passing");
    else animate(x, 0, { type: "spring", stiffness: 300, damping: 30 });
  }

  function doPass(reason: string) {
    onPass({
      rejection_reason: reason,
      dwell_time_seconds: Math.round((Date.now() - startedAt.current) / 1000),
    });
  }

  function doApprove() {
    const finalWeb = webNone ? null : webUrl.trim() || null;
    const finalLi = liNone ? null : liUrl.trim() || null;
    onApprove({
      website_valid: !webNone && finalWeb === scrapedWeb,
      linkedin_valid: !liNone && finalLi === scrapedLi,
      corrected_website_url: !webNone && finalWeb && finalWeb !== scrapedWeb ? finalWeb : null,
      corrected_linkedin_url: !liNone && finalLi && finalLi !== scrapedLi ? finalLi : null,
    });
  }

  return (
    <motion.div
      className="w-full"
      style={{ x, rotate }}
      drag={mode === "idle" && !busy ? "x" : false}
      dragConstraints={{ left: 0, right: 0 }}
      dragElastic={0.6}
      onDragEnd={onDragEnd}
      whileTap={mode === "idle" ? { cursor: "grabbing" } : undefined}
    >
      <Card className="relative overflow-hidden">
        {/* Drag hints */}
        <motion.div
          style={{ opacity: approveHint }}
          className="pointer-events-none absolute right-4 top-4 z-10 rotate-12 rounded-lg border-2 border-success px-3 py-1 text-lg font-black text-success"
        >
          APPROVE
        </motion.div>
        <motion.div
          style={{ opacity: passHint }}
          className="pointer-events-none absolute left-4 top-4 z-10 -rotate-12 rounded-lg border-2 border-danger px-3 py-1 text-lg font-black text-danger"
        >
          PASS
        </motion.div>

        {/* Card face: full-bleed hero + gridded body */}
        <LeadProfile lead={lead} />

        {/* Sources + decision */}
        <div className="space-y-3 px-5 pb-5 pt-3">
          <div className="space-y-2">
            <SourceLine icon="🌐" label="Website" url={scrapedWeb} />
            <SourceLine icon="💼" label="LinkedIn" url={scrapedLi} />
          </div>
          <div className="flex gap-3">
            <Button variant="danger" className="flex-1" disabled={busy} onClick={() => setMode("passing")}>
              ✗ Pass
            </Button>
            <Button variant="success" className="flex-1" disabled={busy} onClick={() => setMode("approving")}>
              ✓ Approve
            </Button>
          </div>
        </div>

        {/* Overlays */}
        <AnimatePresence>
          {mode === "passing" && (
            <motion.div
              key="passing"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="absolute inset-0 z-20 flex flex-col overflow-y-auto bg-surface/85 p-5 backdrop-blur-sm"
            >
              <div className="m-auto w-full space-y-4">
                <div>
                  <h3 className="text-lg font-bold text-danger">Why pass?</h3>
                  <p className="text-sm text-muted">Pick a reason — it logs and moves on.</p>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {PASS_REASONS.map((r) => (
                    <button
                      key={r}
                      type="button"
                      disabled={busy}
                      onClick={() => doPass(r)}
                      className="rounded-lg border border-border bg-surface px-3 py-2.5 text-sm font-medium transition hover:border-danger hover:text-danger disabled:opacity-50"
                    >
                      {r}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => setMode("idle")}
                  className="text-sm text-muted transition hover:text-foreground"
                >
                  ← Back
                </button>
              </div>
            </motion.div>
          )}

          {mode === "approving" && (
            <motion.div
              key="approving"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="absolute inset-0 z-20 overflow-y-auto bg-brand/10 backdrop-blur-sm"
            >
              <div className="flex min-h-full flex-col bg-surface/80 p-5">
                <div className="m-auto w-full space-y-4">
                  <div>
                    <h3 className="text-lg font-bold text-brand">Confirm the sources</h3>
                    <p className="text-sm text-muted">
                      Add the correct links — we use them to find director emails.
                    </p>
                  </div>
                  <SourceInput
                    label="🌐 Website"
                    value={webUrl}
                    onChange={setWebUrl}
                    none={webNone}
                    onToggleNone={() => setWebNone((v) => !v)}
                  />
                  <SourceInput
                    label="💼 LinkedIn"
                    value={liUrl}
                    onChange={setLiUrl}
                    none={liNone}
                    onToggleNone={() => setLiNone((v) => !v)}
                  />
                  <div className="flex gap-3 pt-1">
                    <Button variant="outline" className="flex-1" onClick={() => setMode("idle")}>
                      ← Back
                    </Button>
                    <Button variant="success" className="flex-1" disabled={busy} onClick={doApprove}>
                      {busy ? (
                        <Spinner className="h-4 w-4 border-white/40 border-t-white" />
                      ) : (
                        "✓ Approve"
                      )}
                    </Button>
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
  );
}
