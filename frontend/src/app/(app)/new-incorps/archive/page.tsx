"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ArchiveIncorp } from "@/lib/types";
import { Card, Spinner } from "@/components/ui";
import { formatMoney, companiesHouseUrl } from "@/lib/format";
import { salesforceFields, copyToClipboardHistory } from "@/lib/clipboard";

// Local YYYY-MM-DD (not UTC — matches what the AE thinks "today" is).
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export default function HighValueArchivePage() {
  const [date, setDate] = useState<string>(todayStr);
  const [rows, setRows] = useState<ArchiveIncorp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [claimed, setClaimed] = useState<Record<string, string>>({}); // optimistic, this session
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback((d: string) => {
    setRows(null);
    setError(null);
    setClaimed({});
    api
      .get<ArchiveIncorp[]>(`/new-incorps/archive?date=${d}`)
      .then(setRows)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load the archive."));
  }, []);
  useEffect(() => load(date), [date, load]);

  async function copyAndClaim(r: ArchiveIncorp) {
    setBusy(r.company_number);
    try {
      const first = r.lead?.director_first_name || "";
      const last = r.lead?.director_last_name || "";
      await copyToClipboardHistory(salesforceFields(first, last, r.company_name || ""));
      setClaimed((m) => ({ ...m, [r.company_number]: "you" }));
      const body = { ...(r.lead ?? {}), company_number: r.company_number, company_name: r.company_name };
      api.post("/new-incorps/claim", body).catch(() => { /* best-effort */ });
    } catch {
      /* clipboard blocked — leave unclaimed */
    } finally {
      setBusy(null);
    }
  }

  async function markAlready(r: ArchiveIncorp) {
    setBusy(r.company_number);
    try {
      const body = { ...(r.lead ?? {}), company_number: r.company_number, company_name: r.company_name, already: true };
      await api.post("/new-incorps/claim", body);
      setClaimed((m) => ({ ...m, [r.company_number]: "you" }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <header className="mb-5">
        <h1 className="text-2xl font-bold">💎 High-value archive</h1>
        <p className="mt-1 text-sm text-muted">
          Every high-value incorporation we&apos;ve captured, by incorporation date. Claim one to
          work it, or mark it Already Claimed — both grey it out for everyone.
        </p>
        <label className="mt-3 inline-flex items-center gap-2 text-sm">
          <span className="text-muted">Incorporated on</span>
          <input
            type="date"
            value={date}
            max={todayStr()}
            onChange={(e) => setDate(e.target.value)}
            className="rounded-lg border border-border bg-surface-2 px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
          />
        </label>
      </header>

      {error && <div className="mb-3 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>}

      {rows === null ? (
        <div className="flex justify-center py-16"><Spinner className="h-6 w-6" /></div>
      ) : rows.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted">
          No high-value incorporations captured for {date}.
        </Card>
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-3 py-2">Company</th>
                <th className="px-3 py-2">SIC</th>
                <th className="px-3 py-2 text-right">Starting capital</th>
                <th className="px-3 py-2">Owned by co.</th>
                <th className="px-3 py-2">City</th>
                <th className="px-3 py-2 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const takenBy = r.claimed_by || claimed[r.company_number];
                const working = busy === r.company_number;
                return (
                  <tr key={r.company_number} className={`border-b border-border/50 ${takenBy ? "opacity-45" : ""}`}>
                    <td className="px-3 py-2">
                      <a
                        href={companiesHouseUrl(r.company_number) ?? "#"}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium hover:text-brand hover:underline"
                        title="Open on Companies House"
                      >
                        {r.company_name || "(no name)"}
                      </a>
                      <div className="font-mono text-xs text-muted">{r.company_number}</div>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{r.sic_codes || "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {r.starting_capital != null ? formatMoney(r.starting_capital) : "—"}
                    </td>
                    <td className="px-3 py-2" title={r.lead?.owner_name || undefined}>
                      {r.corporate_owner ? "Yes" : "—"}
                    </td>
                    <td className="px-3 py-2">{r.city || "—"}</td>
                    <td className="px-3 py-2">
                      {takenBy ? (
                        <span className="block text-right text-xs text-muted">
                          🔒 taken{takenBy !== "you" ? ` — ${takenBy}` : ""}
                        </span>
                      ) : (
                        <div className="flex justify-end gap-1.5">
                          <button
                            type="button"
                            disabled={working}
                            onClick={() => copyAndClaim(r)}
                            className="whitespace-nowrap rounded-md border border-border px-2 py-1 text-xs font-medium transition hover:border-brand hover:text-brand disabled:opacity-60"
                          >
                            {working ? "…" : "📋 Copy 5 & claim"}
                          </button>
                          <button
                            type="button"
                            disabled={working}
                            onClick={() => markAlready(r)}
                            className="whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-foreground disabled:opacity-60"
                          >
                            Already claimed
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
