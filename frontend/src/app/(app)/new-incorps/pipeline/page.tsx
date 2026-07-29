"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { api, ApiError } from "@/lib/api";
import type { ClaimedLead } from "@/lib/types";
import { Card, Spinner } from "@/components/ui";

// The tickable outreach steps (free-form on the backend, so easy to extend).
const STEPS: { key: string; label: string }[] = [
  { key: "connection_request", label: "Connection req" },
  { key: "inmail", label: "InMail" },
  { key: "follow_up", label: "Follow-up" },
];

const linkedinSearch = (name: string) =>
  `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(name)}`;

export default function NewIncorpPipelinePage() {
  const [rows, setRows] = useState<ClaimedLead[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    api
      .get<ClaimedLead[]>("/new-incorps/pipeline")
      .then(setRows)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load pipeline."));
  }, []);
  useEffect(() => load(), [load]);

  function toggleStep(cn: string, key: string, value: boolean) {
    // Optimistic — flip locally, then persist.
    setRows((prev) =>
      prev?.map((r) =>
        r.company_number === cn ? { ...r, steps: { ...(r.steps || {}), [key]: value } } : r,
      ) ?? prev,
    );
    api.post(`/new-incorps/pipeline/${cn}/step`, { step: key, value }).catch(() => load());
  }

  async function archive(cn: string, outcome: "success" | "removed") {
    setBusy((b) => new Set(b).add(cn));
    try {
      await api.post(`/new-incorps/pipeline/${cn}/archive`, { outcome });
      setRows((prev) => prev?.filter((r) => r.company_number !== cn) ?? prev);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed.");
    } finally {
      setBusy((b) => {
        const n = new Set(b);
        n.delete(cn);
        return n;
      });
    }
  }

  return (
    <div>
      <header className="mb-5">
        <h1 className="text-2xl font-bold">📋 New-incorp pipeline</h1>
        <p className="mt-1 text-sm text-muted">
          Companies you&apos;ve claimed. Tick outreach steps as you go; <b>Success</b> or{" "}
          <b>Remove</b> archives the lead and clears it from here.
        </p>
      </header>

      {error && <div className="mb-3 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>}

      {rows === null ? (
        <div className="flex justify-center py-16"><Spinner className="h-6 w-6" /></div>
      ) : rows.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted">
          Nothing claimed yet. Claim companies from the New Incorps stream and they land here.
        </Card>
      ) : (
        <ul className="space-y-2">
          <AnimatePresence initial={false}>
            {rows.map((r) => {
              const dir = [r.lead?.director_first_name, r.lead?.director_last_name].filter(Boolean).join(" ").trim();
              const steps = r.steps || {};
              return (
                <motion.li
                  key={r.company_number}
                  layout
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.98 }}
                  transition={{ type: "spring", stiffness: 500, damping: 34 }}
                >
                  <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
                    {/* Identity */}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">{r.company_name || "(no name)"}</p>
                      <p className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                        <span className="font-mono">{r.company_number}</span>
                        {dir && (
                          <>
                            <span>· 👤 {dir}</span>
                            <a href={linkedinSearch(dir)} target="_blank" rel="noreferrer" className="text-brand underline">
                              in ↗
                            </a>
                          </>
                        )}
                      </p>
                    </div>

                    {/* Steps */}
                    <div className="flex items-center gap-3">
                      {STEPS.map((s) => (
                        <label key={s.key} className="flex cursor-pointer select-none items-center gap-1.5 text-xs">
                          <input
                            type="checkbox"
                            checked={!!steps[s.key]}
                            onChange={(e) => toggleStep(r.company_number, s.key, e.target.checked)}
                            className="h-3.5 w-3.5 accent-[var(--brand)]"
                          />
                          {s.label}
                        </label>
                      ))}
                    </div>

                    {/* Terminal actions */}
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        disabled={busy.has(r.company_number)}
                        onClick={() => archive(r.company_number, "success")}
                        className="rounded-md bg-success/10 px-2.5 py-1 text-xs font-medium text-success transition hover:bg-success/20 disabled:opacity-50"
                      >
                        ✓ Success
                      </button>
                      <button
                        type="button"
                        disabled={busy.has(r.company_number)}
                        onClick={() => archive(r.company_number, "removed")}
                        className="rounded-md px-2.5 py-1 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-danger disabled:opacity-50"
                      >
                        ✕ Remove
                      </button>
                    </div>
                  </Card>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </div>
  );
}
