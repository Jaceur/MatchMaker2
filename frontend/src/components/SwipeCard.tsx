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
import { Button, Card } from "./ui";

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

type Mode = "idle" | "passing";

// An editable source on the card face: correct/add the URL before approving.
function SourceField({
  icon,
  label,
  value,
  onChange,
}: {
  icon: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const v = value.trim();
  const href = v ? (/^https?:\/\//i.test(v) ? v : `https://${v}`) : null;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label className="text-xs font-medium text-muted">
          {icon} {label}
        </label>
        {href && (
          <a href={href} target="_blank" rel="noreferrer" className="text-xs text-brand">
            open ↗
          </a>
        )}
      </div>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="none — add a URL"
        className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
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
  const [webUrl, setWebUrl] = useState(scrapedWeb ?? "");
  const [liUrl, setLiUrl] = useState(scrapedLi ?? "");
  const startedAt = useRef(Date.now());

  const x = useMotionValue(0);
  const rotate = useTransform(x, [-300, 300], [-10, 10]);
  const approveHint = useTransform(x, [40, 160], [0, 1]);
  const passHint = useTransform(x, [-160, -40], [1, 0]);

  function doPass(reason: string) {
    onPass({
      rejection_reason: reason,
      dwell_time_seconds: Math.round((Date.now() - startedAt.current) / 1000),
    });
  }

  function doApprove() {
    const finalWeb = webUrl.trim() || null;
    const finalLi = liUrl.trim() || null;
    onApprove({
      website_valid: finalWeb === scrapedWeb,
      linkedin_valid: finalLi === scrapedLi,
      corrected_website_url: finalWeb && finalWeb !== scrapedWeb ? finalWeb : null,
      corrected_linkedin_url: finalLi && finalLi !== scrapedLi ? finalLi : null,
    });
  }

  function onDragEnd(_: unknown, info: { offset: { x: number } }) {
    if (busy) return;
    if (info.offset.x > SWIPE_THRESHOLD) doApprove();
    else if (info.offset.x < -SWIPE_THRESHOLD) setMode("passing");
    else animate(x, 0, { type: "spring", stiffness: 300, damping: 30 });
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

        {/* Card face: hero + gridded body */}
        <LeadProfile lead={lead} />

        {/* Editable sources + decision */}
        <div className="space-y-3 px-5 pb-5 pt-3">
          <div className="space-y-2.5">
            <SourceField icon="🌐" label="Website" value={webUrl} onChange={setWebUrl} />
            <SourceField icon="💼" label="LinkedIn" value={liUrl} onChange={setLiUrl} />
          </div>
          <div className="flex gap-3">
            <Button variant="danger" className="flex-1" disabled={busy} onClick={() => setMode("passing")}>
              ✗ Pass
            </Button>
            <Button variant="success" className="flex-1" disabled={busy} onClick={doApprove}>
              ✓ Approve
            </Button>
          </div>
        </div>

        {/* Pass overlay (approve has no overlay — it commits inline) */}
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
        </AnimatePresence>
      </Card>
    </motion.div>
  );
}
