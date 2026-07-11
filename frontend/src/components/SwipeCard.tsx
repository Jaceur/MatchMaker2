"use client";

import { useMemo, useRef, useState } from "react";
import { motion, useMotionValue, useTransform, animate } from "motion/react";
import type { Lead } from "@/lib/types";
import { LeadProfile } from "./LeadProfile";
import { Button, Card, Progress } from "./ui";

const PASS_REASONS = [
  "Bad Industry",
  "Too Small",
  "No Public Info",
  "Competitor",
  "Out of Business",
  "Other",
];

const SWIPE_THRESHOLD = 120;

export interface PassPayload {
  rejection_reason: string;
  website_valid: boolean;
  linkedin_valid: boolean;
  corrected_website_url: string | null;
  corrected_linkedin_url: string | null;
  dwell_time_seconds: number;
}

export interface ApprovePayload {
  website_valid: boolean;
  linkedin_valid: boolean;
  corrected_website_url: string | null;
  corrected_linkedin_url: string | null;
}

// One source (website / linkedin): a link + a correct/incorrect toggle, or a
// "not found → add URL" box. Mirrors swipe_page's validity_toggle.
function SourceRow({
  icon,
  label,
  url,
  valid,
  onValidChange,
  corrected,
  onCorrectedChange,
}: {
  icon: string;
  label: string;
  url: string | null;
  valid: boolean;
  onValidChange: (v: boolean) => void;
  corrected: string;
  onCorrectedChange: (v: string) => void;
}) {
  return (
    <div className="rounded-lg bg-surface-2 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 text-sm">
          <span className="font-medium">{icon} {label}:</span>{" "}
          {url ? (
            <a href={url} target="_blank" rel="noreferrer" className="text-brand underline">
              open
            </a>
          ) : (
            <span className="text-muted">not found</span>
          )}
        </div>
        {url && (
          <div className="flex shrink-0 gap-1">
            <button
              onClick={() => onValidChange(true)}
              className={`rounded-md px-2 py-1 text-xs font-medium ${valid ? "bg-success text-white" : "bg-surface text-muted"}`}
            >
              ✓
            </button>
            <button
              onClick={() => onValidChange(false)}
              className={`rounded-md px-2 py-1 text-xs font-medium ${!valid ? "bg-danger text-white" : "bg-surface text-muted"}`}
            >
              ✗
            </button>
          </div>
        )}
      </div>
      {(!url || !valid) && (
        <input
          value={corrected}
          onChange={(e) => onCorrectedChange(e.target.value)}
          placeholder={`Correct ${label} URL (https://...)`}
          className="mt-2 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
        />
      )}
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
  const website = lead.corrected_website_url || lead.website_url || null;
  const linkedin = lead.corrected_linkedin_url || lead.linkedin_url || null;

  const [webValid, setWebValid] = useState(true);
  const [liValid, setLiValid] = useState(true);
  const [webCorrected, setWebCorrected] = useState("");
  const [liCorrected, setLiCorrected] = useState("");
  const [reason, setReason] = useState("");
  const [reasonHint, setReasonHint] = useState(false);
  const startedAt = useRef(Date.now());

  const x = useMotionValue(0);
  const rotate = useTransform(x, [-300, 300], [-12, 12]);
  const approveOpacity = useTransform(x, [40, 160], [0, 1]);
  const passOpacity = useTransform(x, [-160, -40], [1, 0]);

  const correctedValues = useMemo(
    () => ({
      corrected_website_url: !webValid && webCorrected.trim() ? webCorrected.trim() : null,
      corrected_linkedin_url: !liValid && liCorrected.trim() ? liCorrected.trim() : null,
    }),
    [webValid, webCorrected, liValid, liCorrected],
  );

  function doApprove() {
    onApprove({ website_valid: webValid, linkedin_valid: liValid, ...correctedValues });
  }

  function doPass() {
    if (!reason) {
      setReasonHint(true);
      return;
    }
    onPass({
      rejection_reason: reason,
      website_valid: webValid,
      linkedin_valid: liValid,
      dwell_time_seconds: Math.round((Date.now() - startedAt.current) / 1000),
      ...correctedValues,
    });
  }

  function onDragEnd(_: unknown, info: { offset: { x: number } }) {
    if (info.offset.x > SWIPE_THRESHOLD) {
      doApprove();
    } else if (info.offset.x < -SWIPE_THRESHOLD) {
      if (reason) doPass();
      else {
        setReasonHint(true);
        animate(x, 0, { type: "spring", stiffness: 300, damping: 30 });
      }
    } else {
      animate(x, 0, { type: "spring", stiffness: 300, damping: 30 });
    }
  }

  return (
    <div className="relative">
      <motion.div
        style={{ x, rotate }}
        drag={busy ? false : "x"}
        dragConstraints={{ left: 0, right: 0 }}
        dragElastic={0.6}
        onDragEnd={onDragEnd}
        whileTap={{ cursor: "grabbing" }}
      >
        <Card className="relative overflow-hidden p-5">
          {/* Drag hint overlays */}
          <motion.div
            style={{ opacity: approveOpacity }}
            className="pointer-events-none absolute right-4 top-4 z-10 rotate-12 rounded-lg border-2 border-success px-3 py-1 text-lg font-black text-success"
          >
            APPROVE
          </motion.div>
          <motion.div
            style={{ opacity: passOpacity }}
            className="pointer-events-none absolute left-4 top-4 z-10 -rotate-12 rounded-lg border-2 border-danger px-3 py-1 text-lg font-black text-danger"
          >
            PASS
          </motion.div>

          <LeadProfile lead={lead} />
        </Card>
      </motion.div>

      {/* Sources + decision (outside the draggable area so inputs work) */}
      <Card className="mt-4 p-5">
        <div className="mb-3">
          <Progress
            value={lead.confidence_score ?? 0}
            label="Data confidence (website/LinkedIn)"
          />
        </div>
        <div className="space-y-2">
          <SourceRow
            icon="🌐"
            label="Website"
            url={website}
            valid={webValid}
            onValidChange={setWebValid}
            corrected={webCorrected}
            onCorrectedChange={setWebCorrected}
          />
          <SourceRow
            icon="💼"
            label="LinkedIn"
            url={linkedin}
            valid={liValid}
            onValidChange={setLiValid}
            corrected={liCorrected}
            onCorrectedChange={setLiCorrected}
          />
        </div>

        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <select
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              setReasonHint(false);
            }}
            className={`flex-1 rounded-lg border bg-surface-2 px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]
              ${reasonHint ? "border-danger" : "border-border"}`}
          >
            <option value="">Reason for passing…</option>
            {PASS_REASONS.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          <div className="flex gap-3">
            <Button variant="danger" onClick={doPass} disabled={busy} className="flex-1">
              ✗ Pass
            </Button>
            <Button variant="success" onClick={doApprove} disabled={busy} className="flex-1">
              ✓ Approve
            </Button>
          </div>
        </div>
        {reasonHint && (
          <p className="mt-2 text-xs text-danger">Pick a reason before passing.</p>
        )}
        <p className="mt-3 text-center text-xs text-muted">
          Tip: drag the card right to approve, left to pass.
        </p>
      </Card>
    </div>
  );
}
