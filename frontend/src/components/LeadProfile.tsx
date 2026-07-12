"use client";

import type { Lead } from "@/lib/types";
import { Chip } from "./ui";
import {
  formatMoney,
  formatDate,
  companyAge,
  accountTier,
  directorList,
} from "@/lib/format";

// Company initials for the hero monogram, skipping legal-suffix noise.
const STOP = new Set([
  "ltd", "limited", "plc", "llp", "the", "and", "co", "company", "group", "holdings", "uk",
]);
function initials(name: string): string {
  const words = name
    .split(/\s+/)
    .filter((w) => w && !STOP.has(w.toLowerCase().replace(/[^a-z]/gi, "")));
  const picked = (words.length ? words : name.split(/\s+/)).slice(0, 2);
  const letters = picked.map((w) => w[0]?.toUpperCase() || "").join("");
  return letters || name.slice(0, 2).toUpperCase();
}

function Stat({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="rounded-lg bg-surface-2 px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-sm font-semibold">{value}</div>
    </div>
  );
}

// Portrait "Tinder profile" for a lead: a coloured hero (monogram + fit score +
// name) over a compact, gridded body. Shared by Swipe & Pipeline.
export function LeadProfile({ lead }: { lead: Lead }) {
  const tier = accountTier(lead.account_type);
  const directors = directorList(lead.active_directors);
  const financials: [string, string | null][] = [
    ["Turnover", formatMoney(lead.turnover)],
    ["Cash", formatMoney(lead.cash_at_bank)],
    ["Staff", lead.employee_count ? String(lead.employee_count) : null],
    ["FX", formatMoney(lead.foreign_exchange)],
    ["Debtors", formatMoney(lead.trade_debtors)],
    ["Creditors", formatMoney(lead.trade_creditors)],
  ];
  const hasFinancials = financials.some(([, v]) => v);

  return (
    <div>
      {/* Hero */}
      <div
        className="px-5 pb-4 pt-5 text-white"
        style={{ background: "linear-gradient(135deg, var(--brand), #6d28d9)" }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white/15 text-lg font-bold ring-1 ring-white/30">
            {initials(lead.company_name)}
          </div>
          <div className="flex h-14 w-14 flex-col items-center justify-center rounded-full bg-white/15 ring-2 ring-white/40">
            <span className="text-lg font-bold leading-none">{lead.lead_score ?? "—"}</span>
            <span className="text-[9px] uppercase tracking-wide text-white/75">fit</span>
          </div>
        </div>
        <h2 className="mt-3 text-xl font-bold leading-tight">{lead.company_name}</h2>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-white/75">
          <span className="font-mono">{lead.crn}</span>
          {formatDate(lead.incorporation_date) && <span>· Inc. {formatDate(lead.incorporation_date)}</span>}
          {companyAge(lead.incorporation_date) && <span>· {companyAge(lead.incorporation_date)}</span>}
        </div>
      </div>

      {/* Body */}
      <div className="space-y-3 px-5 pb-1 pt-3.5">
        {(tier || lead.import_activity || lead.export_activity || lead.director_change_recent) && (
          <div className="flex flex-wrap gap-2">
            {tier && <Chip tone={tier === "Small" ? "brand" : "warning"}>{tier} co</Chip>}
            {lead.import_activity && <Chip tone="brand">Imports</Chip>}
            {lead.export_activity && <Chip tone="brand">Exports</Chip>}
            {lead.director_change_recent && <Chip tone="warning">New director</Chip>}
          </div>
        )}

        {hasFinancials && (
          <div className="grid grid-cols-3 gap-2">
            {financials.map(([label, value]) => (
              <Stat key={label} label={label} value={value} />
            ))}
          </div>
        )}

        {lead.sic_codes && (
          <div className="text-sm text-muted">
            <span className="font-medium text-foreground">SIC:</span> {lead.sic_codes}
          </div>
        )}

        {directors.length > 0 && (
          <div>
            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Directors</div>
            <div className="flex flex-wrap gap-1.5">
              {directors.map((d) => (
                <Chip key={d}>👤 {d}</Chip>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
