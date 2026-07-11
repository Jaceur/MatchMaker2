"use client";

import type { Lead } from "@/lib/types";
import { Chip } from "./ui";
import {
  formatMoney,
  formatDate,
  companyAge,
  accountTier,
  scoreTone,
  directorList,
} from "@/lib/format";

// Literal class strings so Tailwind's scanner compiles them (no dynamic names).
const SCORE_TEXT: Record<string, string> = {
  success: "text-success",
  brand: "text-brand",
  warning: "text-warning",
  danger: "text-danger",
};

function Stat({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="rounded-lg bg-surface-2 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-sm font-semibold">{value}</div>
    </div>
  );
}

// The read-only "Tinder/Revolut profile" for a lead. Shared by Swipe & Pipeline.
export function LeadProfile({ lead }: { lead: Lead }) {
  const tier = accountTier(lead.account_type);
  const directors = directorList(lead.active_directors);
  const financials: [string, string | null][] = [
    ["Turnover", formatMoney(lead.turnover)],
    ["Cash", formatMoney(lead.cash_at_bank)],
    ["Employees", lead.employee_count ? String(lead.employee_count) : null],
    ["FX", formatMoney(lead.foreign_exchange)],
    ["Debtors", formatMoney(lead.trade_debtors)],
    ["Creditors", formatMoney(lead.trade_creditors)],
  ];
  const hasFinancials = financials.some(([, v]) => v);

  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-xl font-bold">{lead.company_name}</h2>
          <div className="mt-0.5 font-mono text-xs text-muted">{lead.crn}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className={`text-2xl font-bold ${SCORE_TEXT[scoreTone(lead.lead_score)]}`}>
            {lead.lead_score ?? "—"}
          </div>
          <div className="text-[11px] uppercase tracking-wide text-muted">fit score</div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {companyAge(lead.incorporation_date) && <Chip>{companyAge(lead.incorporation_date)}</Chip>}
        {formatDate(lead.incorporation_date) && (
          <Chip>Inc. {formatDate(lead.incorporation_date)}</Chip>
        )}
        {tier && <Chip tone={tier === "Small" ? "brand" : "warning"}>{tier} co</Chip>}
        {lead.import_activity && <Chip tone="brand">Imports</Chip>}
        {lead.export_activity && <Chip tone="brand">Exports</Chip>}
        {lead.director_change_recent && <Chip tone="warning">Recent director change</Chip>}
      </div>

      {lead.sic_codes && (
        <div className="mt-3 text-sm text-muted">
          <span className="font-medium text-foreground">SIC:</span> {lead.sic_codes}
        </div>
      )}

      {hasFinancials && (
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {financials.map(([label, value]) => (
            <Stat key={label} label={label} value={value} />
          ))}
        </div>
      )}

      {directors.length > 0 && (
        <div className="mt-4">
          <div className="text-[11px] uppercase tracking-wide text-muted">Directors</div>
          <div className="mt-1 flex flex-wrap gap-2">
            {directors.map((d) => (
              <Chip key={d}>👤 {d}</Chip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
