"use client";

import { useRef, useState } from "react";
import {
  motion,
  AnimatePresence,
  useMotionValue,
  useTransform,
  animate,
} from "motion/react";
import type { Lead, SourceCandidate } from "@/lib/types";
import { bareDomain } from "@/lib/format";
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
const LI_PREFIX = "https://www.linkedin.com/company/";

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

const linkedinSlug = (url: string) => url.match(/\/company\/([^/?]+)/)?.[1] ?? url;
const withScheme = (url: string) => (/^https?:\/\//i.test(url) ? url : `https://${url}`);

// A dropdown of the top-N scored search candidates, so the AE picks the right one
// (no pasting) — and we learn which they chose. "None" / "Other" fall back to
// hand entry. For LinkedIn the /company/ prefix is fixed; only the slug is typed.
function SourcePicker({
  icon,
  label,
  kind,
  candidates,
  current,
  onChange,
}: {
  icon: string;
  label: string;
  kind: "website" | "linkedin";
  candidates?: SourceCandidate[] | null;
  current: string | null;
  onChange: (url: string | null) => void;
}) {
  const isLi = kind === "linkedin";
  const display = (url: string) => (isLi ? linkedinSlug(url) : bareDomain(url));

  const cands = (candidates ?? []).filter((c) => c.url);
  const curLower = current?.toLowerCase();
  const options = cands.map((c) => c.url);
  if (current && !options.some((u) => u.toLowerCase() === curLower)) options.unshift(current);

  const defaultMode = current
    ? options.find((u) => u.toLowerCase() === curLower) ?? current
    : options[0] ?? "__none__";

  const [mode, setMode] = useState(defaultMode);
  const [otherText, setOtherText] = useState("");

  function resolve(m: string, txt: string): string | null {
    if (m === "__none__") return null;
    if (m === "__other__") {
      const t = txt.trim();
      if (!t) return null;
      return isLi ? LI_PREFIX + t.replace(/^.*\/company\//, "").replace(/\/+$/, "") : withScheme(t);
    }
    return m;
  }

  function change(m: string, txt: string) {
    setMode(m);
    setOtherText(txt);
    onChange(resolve(m, txt));
  }

  const selectedUrl = mode !== "__none__" && mode !== "__other__" ? mode : null;

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label className="text-xs font-medium text-muted">
          {icon} {label}
        </label>
        {selectedUrl && (
          <a href={withScheme(selectedUrl)} target="_blank" rel="noreferrer" className="text-xs text-brand">
            open ↗
          </a>
        )}
      </div>
      {isLi && (
        <div className="truncate rounded-t-lg border border-b-0 border-border bg-surface-2 px-3 pt-1.5 font-mono text-[11px] text-muted">
          {LI_PREFIX}
        </div>
      )}
      <select
        value={mode}
        onChange={(e) => change(e.target.value, otherText)}
        className={`w-full border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)] ${
          isLi ? "rounded-b-lg border-t-0" : "rounded-lg"
        }`}
      >
        {options.map((u) => (
          <option key={u} value={u}>
            {display(u)}
          </option>
        ))}
        <option value="__none__">None found</option>
        <option value="__other__">Other — type it</option>
      </select>
      {mode === "__other__" &&
        (isLi ? (
          <div className="mt-2 flex items-center rounded-lg border border-border bg-surface-2 text-sm focus-within:ring-2 focus-within:ring-[var(--ring)]">
            <span className="pl-3 text-muted">/company/</span>
            <input
              autoFocus
              value={otherText}
              onChange={(e) => change("__other__", e.target.value)}
              placeholder="company-slug"
              className="flex-1 bg-transparent px-1 py-2 outline-none"
            />
          </div>
        ) : (
          <input
            autoFocus
            value={otherText}
            onChange={(e) => change("__other__", e.target.value)}
            placeholder="https://…"
            className="mt-2 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
          />
        ))}
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
  const [webUrl, setWebUrl] = useState<string | null>(scrapedWeb);
  const [liUrl, setLiUrl] = useState<string | null>(scrapedLi);
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
    onApprove({
      website_valid: webUrl === scrapedWeb,
      linkedin_valid: liUrl === scrapedLi,
      corrected_website_url: webUrl && webUrl !== scrapedWeb ? webUrl : null,
      corrected_linkedin_url: liUrl && liUrl !== scrapedLi ? liUrl : null,
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

        <LeadProfile lead={lead} />

        {/* Pick the right source from the search candidates, then decide */}
        <div className="space-y-3 px-5 pb-5 pt-3">
          <div className="space-y-2.5">
            <SourcePicker
              icon="🌐"
              label="Website"
              kind="website"
              candidates={lead.website_candidates}
              current={scrapedWeb}
              onChange={setWebUrl}
            />
            <SourcePicker
              icon="💼"
              label="LinkedIn"
              kind="linkedin"
              candidates={lead.linkedin_candidates}
              current={scrapedLi}
              onChange={setLiUrl}
            />
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
