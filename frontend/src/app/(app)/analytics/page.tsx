"use client";

import { useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
  CartesianGrid,
} from "recharts";
import { api, ApiError } from "@/lib/api";
import type { Analytics } from "@/lib/types";
import { formatMoney } from "@/lib/format";
import { Card, Spinner } from "@/components/ui";

const AXIS = { fill: "var(--muted)", fontSize: 11 };
const tooltipStyle = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  fontSize: 12,
  color: "var(--foreground)",
};

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <h2 className="text-lg font-semibold">{title}</h2>
      {subtitle && <p className="mb-3 mt-0.5 text-sm text-muted">{subtitle}</p>}
      <Card className="p-4">{children}</Card>
    </section>
  );
}

function Empty({ msg }: { msg: string }) {
  return <p className="py-8 text-center text-sm text-muted">{msg}</p>;
}

export default function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Analytics>("/analytics")
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load analytics."))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }
  if (error) {
    return <div className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>;
  }
  if (!data) return null;

  const t = data.totals;

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold">📈 Analytics</h1>
        <p className="mt-1 text-sm text-muted">
          {t.decided} decided leads · {t.approved} approved ({t.approval_rate}% approval rate).
          Reflects the current pool (cleared leads excluded).
        </p>
      </header>

      {/* 1. SIC groups */}
      <Section
        title="Approval by industry group"
        subtitle="Approval rate per business grouping of the lead's primary SIC code, busiest first. Coarser than the per-code view below — each group pools many codes, so the counts are big enough to trust."
      >
        {data.sic_groups.length === 0 ? (
          <Empty msg="No decided leads with SIC codes yet." />
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(260, data.sic_groups.length * 24)}>
            <BarChart data={data.sic_groups} layout="vertical" margin={{ left: 8, right: 24 }}>
              <CartesianGrid horizontal={false} stroke="var(--border)" />
              <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={AXIS} />
              <YAxis type="category" dataKey="group" width={170} tick={{ ...AXIS, fontSize: 10 }} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, _name, item) => [
                  `${value}% (${item.payload.approved}/${item.payload.total})`,
                  "approval",
                ]}
              />
              <Bar dataKey="rate" fill="var(--brand)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </Section>

      {/* 2. SIC codes */}
      <Section
        title="Approval by SIC code (top 20)"
        subtitle="Approval rate per primary SIC code, busiest first. Hover for the description and counts."
      >
        {data.sic.length === 0 ? (
          <Empty msg="No decided leads with SIC codes yet." />
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(260, data.sic.length * 24)}>
            <BarChart data={data.sic} layout="vertical" margin={{ left: 8, right: 24 }}>
              <CartesianGrid horizontal={false} stroke="var(--border)" />
              <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={AXIS} />
              <YAxis type="category" dataKey="sic" width={64} tick={AXIS} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, _name, item) => [
                  `${value}% (${item.payload.approved}/${item.payload.total})`,
                  "approval",
                ]}
                labelFormatter={(sic) => {
                  const row = data.sic.find((s) => s.sic === String(sic));
                  return `${sic} — ${row?.label ?? ""}`;
                }}
              />
              <Bar dataKey="rate" fill="var(--brand)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </Section>

      {/* 3. Feature correlations */}
      <Section
        title="What correlates with approval"
        subtitle="Point-biserial correlation between each factor and approval. Right (green) = higher value gets approved more; left (red) = the opposite."
      >
        {data.feature_correlations.length === 0 ? (
          <Empty msg="Not enough decided leads to compute correlations yet." />
        ) : (
          <ResponsiveContainer width="100%" height={data.feature_correlations.length * 34 + 40}>
            <BarChart data={data.feature_correlations} layout="vertical" margin={{ left: 8, right: 24 }}>
              <XAxis type="number" domain={[-1, 1]} tick={AXIS} />
              <YAxis type="category" dataKey="feature" width={130} tick={AXIS} />
              <ReferenceLine x={0} stroke="var(--border)" />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, _name, item) => [`r = ${value} (n=${item.payload.n})`, "correlation"]}
              />
              <Bar dataKey="corr" radius={4}>
                {data.feature_correlations.map((c) => (
                  <Cell key={c.feature} fill={c.corr >= 0 ? "var(--success)" : "var(--danger)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </Section>

      {/* 4. CRM status factors */}
      <Section
        title="Net New vs Existing Lead vs Existing Account"
        subtitle="Average profile per CRM outcome — how the classified buckets differ."
      >
        {data.crm_breakdown.length === 0 ? (
          <Empty msg="No classified leads yet." />
        ) : (
          <div className="grid gap-6 sm:grid-cols-2">
            <CrmBars title="Avg lead score" data={data.crm_breakdown} field="avg_score" />
            <CrmBars title="Leads" data={data.crm_breakdown} field="count" />
            <CrmBars title="Avg cash" data={data.crm_breakdown} field="avg_cash" money />
            <CrmBars title="Avg staff" data={data.crm_breakdown} field="avg_staff" />
          </div>
        )}
      </Section>

      {/* 5. Score calibration */}
      <Section
        title="Does the score predict approvals?"
        subtitle="Approval rate per lead-score band. Climbing left-to-right = the score works; flat or bumpy = it doesn't."
      >
        {data.score_calibration.length === 0 ? (
          <Empty msg="Not enough decided leads with a score yet." />
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={data.score_calibration} margin={{ left: 0, right: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis dataKey="band" tick={AXIS} />
              <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={AXIS} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, _name, item) => [
                  `${value}% (${item.payload.approved}/${item.payload.decided})`,
                  "approval",
                ]}
              />
              <Bar dataKey="rate" fill="var(--brand)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </Section>

      {/* 6. Score-band factor breakdown */}
      <Section
        title="What's inside each score band"
        subtitle="Average features + approval rate per band — for digging into anomalies (e.g. why a lower band can out-approve a higher one). Small bands are noisy: watch the lead count."
      >
        {data.score_factors.length === 0 ? (
          <Empty msg="Not enough scored leads yet." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                  <th className="py-2 pr-3">Band</th>
                  <th className="py-2 pr-3">Leads</th>
                  <th className="py-2 pr-3">Approval</th>
                  <th className="py-2 pr-3">Cash</th>
                  <th className="py-2 pr-3">Staff</th>
                  <th className="py-2 pr-3">FX</th>
                  <th className="py-2 pr-3">Turnover</th>
                  <th className="py-2 pr-3">Debtors</th>
                  <th className="py-2 pr-3">Creditors</th>
                </tr>
              </thead>
              <tbody>
                {data.score_factors.map((r) => (
                  <tr key={r.band} className="border-b border-border/60">
                    <td className="py-2 pr-3 font-medium">{r.band}</td>
                    <td className="py-2 pr-3 text-muted">{r.decided}</td>
                    <td className="py-2 pr-3 font-medium">{r.rate}%</td>
                    <td className="py-2 pr-3">{formatMoney(r.avg_cash) ?? "—"}</td>
                    <td className="py-2 pr-3">{r.avg_staff ?? "—"}</td>
                    <td className="py-2 pr-3">{formatMoney(r.avg_fx) ?? "—"}</td>
                    <td className="py-2 pr-3">{formatMoney(r.avg_turnover) ?? "—"}</td>
                    <td className="py-2 pr-3">{formatMoney(r.avg_debtors) ?? "—"}</td>
                    <td className="py-2 pr-3">{formatMoney(r.avg_creditors) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* 7. Coverage */}
      <Section
        title="Enrichment coverage"
        subtitle="Share of enriched leads that have each field populated. Sparse fields are the model's blind spots."
      >
        {data.coverage.length === 0 || data.coverage[0]?.total === 0 ? (
          <Empty msg="No enriched leads yet." />
        ) : (
          <ResponsiveContainer width="100%" height={data.coverage.length * 34 + 40}>
            <BarChart data={data.coverage} layout="vertical" margin={{ left: 8, right: 24 }}>
              <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={AXIS} />
              <YAxis type="category" dataKey="field" width={110} tick={AXIS} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(value, _name, item) => [`${value}% (${item.payload.populated}/${item.payload.total})`, "coverage"]}
              />
              <Bar dataKey="pct" fill="var(--brand)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </Section>
    </div>
  );
}

function CrmBars({
  title,
  data,
  field,
  money,
}: {
  title: string;
  data: Analytics["crm_breakdown"];
  field: keyof Analytics["crm_breakdown"][number];
  money?: boolean;
}) {
  // Short labels so the axis is readable.
  const rows = data.map((d) => ({
    name: d.crm_status.replace("Existing ", "Ex. ").replace(" - ", " · "),
    value: (d[field] as number | null) ?? 0,
  }));
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted">{title}</p>
      <ResponsiveContainer width="100%" height={rows.length * 30 + 20}>
        <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16 }}>
          <XAxis type="number" tick={AXIS} hide />
          <YAxis type="category" dataKey="name" width={120} tick={{ ...AXIS, fontSize: 10 }} />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value) => [money ? formatMoney(Number(value)) ?? "—" : value, title]}
          />
          <Bar dataKey="value" fill="var(--brand)" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
