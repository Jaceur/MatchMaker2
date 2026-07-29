"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { API_BASE_URL, getToken, api } from "@/lib/api";
import type { Incorp } from "@/lib/types";
import { Card } from "@/components/ui";
import { formatMoney } from "@/lib/format";

const MAX_ON_SCREEN = 25;
type Status = "connecting" | "live" | "reconnecting";

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

// CHStream enrichment + why-it's-high-value, when present.
function enrichmentBits(c: Incorp): string[] {
  const bits: string[] = [];
  const name = [c.director_first_name, c.director_last_name].filter(Boolean).join(" ").trim();
  if (name) bits.push(`👤 ${name}`);
  const cap = Number(c.starting_capital);
  if (Number.isFinite(cap) && cap > 0) bits.push(`💷 ${formatMoney(cap)}`);
  if (c.corporate_owner) bits.push(`🏢 owned by ${c.owner_name || "a company"}`);
  if (c.city) bits.push(String(c.city));
  const others = Number(c.director_other_companies);
  if (Number.isFinite(others) && others > 0) bits.push(`+${others} other co${others > 1 ? "s" : ""}`);
  return bits;
}

// Ofcom reserved fictional mobile (+447700900xxx) — valid format, never a real line.
const fakeMobile = () =>
  `+447700900${String(Math.floor(Math.random() * 1000)).padStart(3, "0")}`;

// Write values to the clipboard in sequence so each becomes its own Win+V entry
// (a gap is required or Windows merges them). Oldest-first; Win+V is newest-first.
async function copyToClipboardHistory(values: string[]): Promise<void> {
  for (const v of values) {
    await navigator.clipboard.writeText(v);
    await new Promise((r) => setTimeout(r, 250));
  }
}

const keyOf = (c: Incorp) => `${c.company_number}:${c.received_at}`;

export function IncorpStream({ channel }: { channel: "all" | "high_value" }) {
  const [items, setItems] = useState<Incorp[]>([]);
  const [status, setStatus] = useState<Status>("connecting");
  const [now, setNow] = useState(() => Date.now());
  const [copying, setCopying] = useState<{ key: string; kind: "sf" | "li" } | null>(null);
  // company_number -> claimed_by, applied across the whole list (grey-out).
  const [claims, setClaims] = useState<Record<string, string>>({});
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 3000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const url = `${API_BASE_URL}/new-incorps/stream?channel=${channel}&token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    es.onopen = () => setStatus("live");
    es.onerror = () => setStatus("reconnecting");
    es.onmessage = (e) => {
      let c: Incorp;
      try { c = JSON.parse(e.data); } catch { return; }
      if (c.claimed && c.claimed_by) {
        setClaims((m) => ({ ...m, [c.company_number]: c.claimed_by as string }));
      }
      const k = keyOf(c);
      if (seen.current.has(k)) return;
      seen.current.add(k);
      setItems((prev) => [c, ...prev].slice(0, MAX_ON_SCREEN));
    };
    // Server broadcasts a claim whenever ANY AE copies a lead — grey it everywhere.
    es.addEventListener("claim", (e) => {
      try {
        const { company_number, claimed_by } = JSON.parse((e as MessageEvent).data);
        setClaims((m) => ({ ...m, [company_number]: claimed_by }));
      } catch { /* ignore */ }
    });
    return () => es.close();
  }, [channel]);

  // Claiming: persists the lead + greys it for everyone (incl. this page, via the
  // broadcast we also receive). Optimistically mark it now.
  function claimLead(c: Incorp) {
    setClaims((m) => ({ ...m, [c.company_number]: m[c.company_number] || "you" }));
    api.post("/new-incorps/claim", c).catch(() => { /* best-effort */ });
  }

  // 5 fields for a Salesforce record, one per Win+V entry (First, Last, Title,
  // Company, Phone reading down).
  async function copyForSalesforce(c: Incorp) {
    const ordered = [
      fakeMobile(), c.company_name || "", "Director",
      c.director_last_name || "", c.director_first_name || "",
    ];
    await runCopy(c, "sf", ordered);
  }

  // 2 entries for LinkedIn: the full name (top of Win+V), then company below it.
  async function copyForLinkedIn(c: Incorp) {
    const name = [c.director_first_name, c.director_last_name].filter(Boolean).join(" ").trim();
    await runCopy(c, "li", [c.company_name || "", name]); // company written first → name on top
  }

  async function runCopy(c: Incorp, kind: "sf" | "li", ordered: string[]) {
    setCopying({ key: keyOf(c), kind });
    try {
      await copyToClipboardHistory(ordered);
      claimLead(c);
    } catch {
      /* clipboard blocked — leave unclaimed */
    } finally {
      setCopying(null);
    }
  }

  const dot = status === "live" ? "bg-success" : status === "reconnecting" ? "bg-warning" : "bg-muted";
  const heading = channel === "high_value" ? "💎 High-value incorporations — live" : "✨ New incorporations — live";
  const blurb = channel === "high_value"
    ? "Capital > £25k, corporate-owned, or a London Zone-1 postcode. Newest on top, 25 max."
    : "Every UK company as it's registered. Newest on top; 25 most recent only. Nothing is saved.";

  return (
    <div>
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{heading}</h1>
          <p className="mt-1 text-sm text-muted">{blurb}</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${dot} ${status === "live" ? "animate-pulse" : ""}`} />
          <span className="capitalize text-muted">{status}</span>
        </div>
      </header>

      {items.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted">
          {status === "reconnecting" ? "Reconnecting to the stream…" : "Waiting for the next incorporation…"}
        </Card>
      ) : (
        <ul className="space-y-2">
          <AnimatePresence initial={false}>
            {items.map((c) => {
              const k = keyOf(c);
              const claimedBy = claims[c.company_number];
              const busy = copying?.key === k;
              return (
                <motion.li
                  key={k}
                  layout
                  initial={{ opacity: 0, y: -12, scale: 0.98 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.98 }}
                  transition={{ type: "spring", stiffness: 500, damping: 34 }}
                >
                  <Card className={`flex items-center justify-between gap-4 p-3.5 transition ${claimedBy ? "opacity-45" : ""}`}>
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
                      <span className="whitespace-nowrap text-xs text-muted tabular-nums">{ago(c.received_at, now)}</span>
                      {claimedBy ? (
                        <span className="whitespace-nowrap rounded-md bg-surface-2 px-2.5 py-1 text-xs text-muted">
                          🔒 taken{claimedBy !== "you" ? ` — ${claimedBy}` : ""}
                        </span>
                      ) : (
                        <div className="flex flex-col items-stretch gap-1">
                          <button
                            type="button"
                            onClick={() => copyForSalesforce(c)}
                            disabled={busy}
                            title="Copies 5 fields to clipboard history (Win+V) and claims this lead so no one else works it"
                            className="whitespace-nowrap rounded-md border border-border px-2.5 py-1 text-xs font-medium transition hover:border-brand hover:text-brand disabled:opacity-60"
                          >
                            {busy && copying?.kind === "sf" ? "Copying…" : "📋 Copy 5 & claim"}
                          </button>
                          <button
                            type="button"
                            onClick={() => copyForLinkedIn(c)}
                            disabled={busy}
                            title="Copies the full name + company to clipboard history (Win+V) and claims this lead"
                            className="whitespace-nowrap rounded-md border border-border px-2.5 py-1 text-xs font-medium transition hover:border-brand hover:text-brand disabled:opacity-60"
                          >
                            {busy && copying?.kind === "li" ? "Copying…" : "🔗 Copy LI & claim"}
                          </button>
                        </div>
                      )}
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
