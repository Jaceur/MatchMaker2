"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { API_BASE_URL, getToken } from "@/lib/api";
import type { Incorp } from "@/lib/types";
import { Card } from "@/components/ui";
import { formatMoney } from "@/lib/format";

const MAX_ON_SCREEN = 25;

type Status = "connecting" | "live" | "reconnecting";

// "3s ago" / "4m ago" — how long since we received it (the freshness/latency read).
function ago(iso: string, now: number): string {
  const secs = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

function sicList(v: Incorp["sic_codes"]): string[] {
  if (!v) return [];
  if (Array.isArray(v)) return v.filter(Boolean);
  return String(v).split(",").map((s) => s.trim()).filter(Boolean);
}

// A placeholder UK mobile in Ofcom's reserved fictional range (07700 900000–
// 900999 → +447700900xxx). Valid format, guaranteed never a real person's line —
// safe for a Salesforce phone field that just needs to be non-empty.
const fakeMobile = () =>
  `+447700900${String(Math.floor(Math.random() * 1000)).padStart(3, "0")}`;

// Write several values to the clipboard in sequence so each lands as its OWN
// entry in the Windows clipboard history (Win+V). The gap between writes is
// required — too fast and Windows collapses them into one entry. Values are
// given oldest-first; Win+V lists newest-first, so the caller orders them so the
// first field it wants ends up on top.
async function copyToClipboardHistory(values: string[]): Promise<void> {
  for (const v of values) {
    await navigator.clipboard.writeText(v);
    await new Promise((r) => setTimeout(r, 250));
  }
}

// CHStream's enrichment, when present: who to contact + quality signals.
function enrichmentBits(c: Incorp): string[] {
  const bits: string[] = [];
  const name = [c.director_first_name, c.director_last_name].filter(Boolean).join(" ").trim();
  if (name) bits.push(`👤 ${name}`);
  const cap = Number(c.starting_capital);
  if (Number.isFinite(cap) && cap > 0) bits.push(`💷 ${formatMoney(cap)}`);
  if (c.city) bits.push(String(c.city));
  const others = Number(c.director_other_companies);
  if (Number.isFinite(others) && others > 0) bits.push(`+${others} other co${others > 1 ? "s" : ""}`);
  return bits;
}

export default function NewIncorpsPage() {
  const [items, setItems] = useState<Incorp[]>([]);
  const [status, setStatus] = useState<Status>("connecting");
  const [now, setNow] = useState(() => Date.now());
  const [copyState, setCopyState] = useState<{ key: string; state: "copying" | "done" } | null>(null);
  const seen = useRef<Set<string>>(new Set());

  // One click → load the 5 Salesforce fields into clipboard history, to paste
  // each with Win+V. Ordered so Win+V (newest-first) reads down the SF form:
  // First name, Last name, Title (always Director), Company, Phone.
  async function copyForSalesforce(c: Incorp) {
    const key = `${c.company_number}:${c.received_at}`;
    // Oldest-first (First name written last → ends up on top of Win+V).
    const ordered = [
      fakeMobile(),
      c.company_name || "",
      "Director",
      c.director_last_name || "",
      c.director_first_name || "",
    ];
    setCopyState({ key, state: "copying" });
    try {
      await copyToClipboardHistory(ordered);
      setCopyState({ key, state: "done" });
      setTimeout(() => setCopyState((s) => (s?.key === key ? null : s)), 5000);
    } catch {
      setCopyState(null);
    }
  }

  // Live-update the "Xs ago" labels without touching the stream.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 3000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) return;

    const es = new EventSource(`${API_BASE_URL}/new-incorps/stream?token=${encodeURIComponent(token)}`);
    es.onopen = () => setStatus("live");
    es.onerror = () => setStatus("reconnecting"); // EventSource auto-reconnects
    es.onmessage = (e) => {
      let incorp: Incorp;
      try {
        incorp = JSON.parse(e.data);
      } catch {
        return;
      }
      // De-dupe (a reconnect replays the buffer), newest on top, cap at 25.
      const key = `${incorp.company_number}:${incorp.received_at}`;
      if (seen.current.has(key)) return;
      seen.current.add(key);
      setItems((prev) => [incorp, ...prev].slice(0, MAX_ON_SCREEN));
    };

    return () => es.close();
  }, []);

  const dot =
    status === "live" ? "bg-success" : status === "reconnecting" ? "bg-warning" : "bg-muted";

  return (
    <div>
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">✨ New incorporations — live</h1>
          <p className="mt-1 text-sm text-muted">
            Every UK company as it&apos;s registered. Newest on top; the {MAX_ON_SCREEN} most recent
            only — older ones drop off. Nothing is saved.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${dot} ${status === "live" ? "animate-pulse" : ""}`} />
          <span className="capitalize text-muted">{status}</span>
        </div>
      </header>

      {items.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted">
          {status === "reconnecting"
            ? "Reconnecting to the stream…"
            : "Waiting for the next new incorporation…"}
        </Card>
      ) : (
        <ul className="space-y-2">
          <AnimatePresence initial={false}>
            {items.map((c) => (
              <motion.li
                key={`${c.company_number}:${c.received_at}`}
                layout
                initial={{ opacity: 0, y: -12, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.98 }}
                transition={{ type: "spring", stiffness: 500, damping: 34 }}
              >
                <Card className="flex items-center justify-between gap-4 p-3.5">
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{c.company_name || "(no name)"}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
                      <span className="font-mono">{c.company_number}</span>
                      {c.date_of_creation && <span>· inc. {c.date_of_creation}</span>}
                      {sicList(c.sic_codes).length > 0 && (
                        <span className="font-mono">· SIC {sicList(c.sic_codes).join(", ")}</span>
                      )}
                    </p>
                    {enrichmentBits(c).length > 0 && (
                      <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-foreground/80">
                        {enrichmentBits(c).map((b, i) => (
                          <span key={i}>{i > 0 && <span className="text-muted">· </span>}{b}</span>
                        ))}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    <span className="whitespace-nowrap text-xs text-muted tabular-nums">
                      {ago(c.received_at, now)}
                    </span>
                    {(() => {
                      const key = `${c.company_number}:${c.received_at}`;
                      const st = copyState?.key === key ? copyState.state : null;
                      return (
                        <button
                          type="button"
                          onClick={() => copyForSalesforce(c)}
                          disabled={st === "copying"}
                          title="Copies First name, Last name, Title (Director), Company and a placeholder phone as 5 separate items — paste each into Salesforce with Win+V"
                          className="whitespace-nowrap rounded-md border border-border px-2.5 py-1 text-xs font-medium transition hover:border-brand hover:text-brand disabled:opacity-60"
                        >
                          {st === "copying" ? "Copying…" : st === "done" ? "✓ press Win+V" : "📋 Copy 5 for SF"}
                        </button>
                      );
                    })()}
                  </div>
                </Card>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </div>
  );
}
