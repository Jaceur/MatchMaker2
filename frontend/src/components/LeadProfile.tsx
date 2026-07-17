"use client";

import type { Lead, SicDetail } from "@/lib/types";
import { Chip, CopyButton } from "./ui";
import { formatMoney, formatDate, companyAge, accountTier } from "@/lib/format";

const ClipboardIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);
const CheckIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

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

// Nature of business: one line per SIC code, "01110 — Growing of cereals…".
// Falls back to the raw comma-separated codes for leads served by an API old
// enough not to send sic_detail.
function SicList({ lead }: { lead: Lead }) {
  const detail: SicDetail[] = lead.sic_detail?.length
    ? lead.sic_detail
    : (lead.sic_codes ?? "")
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean)
        .map((code) => ({ code, description: null, section: null }));

  if (detail.length === 0) return null;

  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted">Nature of business</div>
      <ul className="mt-1 space-y-0.5">
        {detail.map((s) => (
          <li key={s.code} className="flex gap-1.5 text-xs leading-snug">
            <span className="shrink-0 font-mono font-medium text-foreground">{s.code}</span>
            {s.description && (
              <>
                <span className="text-muted">—</span>
                <span className="text-muted">{s.description}</span>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
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
          {/* Holdout leads show "??" instead of their score: they're the random
              sample that bypasses the bar to test the filter, so showing a low
              number would bias the very verdict we're trying to read honestly.
              The tooltip nudges without hinting which way. */}
          <div
            className="flex h-14 w-14 flex-col items-center justify-center rounded-full bg-white/15 ring-2 ring-white/40"
            title={lead.is_holdout ? "Score hidden — judge this one on its merits." : undefined}
          >
            <span className="text-lg font-bold leading-none">
              {lead.is_holdout ? "??" : lead.lead_score ?? "—"}
            </span>
            <span className="text-[9px] uppercase tracking-wide text-white/75">fit</span>
          </div>
        </div>
        <div className="mt-3 flex items-start gap-1.5">
          <h2 className="text-xl font-bold leading-tight">{lead.company_name}</h2>
          <CopyButton
            text={lead.company_name}
            copiedLabel={<CheckIcon />}
            className="mt-0.5 shrink-0 rounded-md p-1 text-white/70 transition hover:bg-white/15 hover:text-white"
          >
            <ClipboardIcon />
          </CopyButton>
        </div>
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

        <SicList lead={lead} />
      </div>
    </div>
  );
}
