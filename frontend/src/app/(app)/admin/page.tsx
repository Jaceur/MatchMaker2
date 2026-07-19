"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AdminStats, AePerformance, PipelineJob, AllocationRow, ShadowModel } from "@/lib/types";
import { Button, Card, Progress, Spinner } from "@/components/ui";

function Metric({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <Card className="p-4">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${accent ? "text-brand" : ""}`}>{value}</div>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="mb-3 text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

const activeJob = (j: PipelineJob | null) => j && (j.status === "pending" || j.status === "running");

export default function AdminPage() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [shadow, setShadow] = useState<ShadowModel | null>(null);
  const [aes, setAes] = useState<AePerformance[]>([]);
  const [job, setJob] = useState<PipelineJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const [percent, setPercent] = useState(50);
  const [sourceCount, setSourceCount] = useState(500);
  const [target, setTarget] = useState(40);
  const [busy, setBusy] = useState<string | null>(null);
  const [allocation, setAllocation] = useState<AllocationRow[] | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const loadStats = useCallback(async () => {
    const s = await api.get<AdminStats>("/admin/stats");
    setStats(s);
    setPercent(s.qualify_percent);
  }, []);
  const loadAes = useCallback(() => api.get<AePerformance[]>("/admin/ae-performance").then(setAes), []);
  const loadShadow = useCallback(() => api.get<ShadowModel>("/admin/shadow-model").then(setShadow), []);
  const loadJob = useCallback(async () => {
    const jobs = await api.get<PipelineJob[]>("/admin/pipeline-jobs");
    setJob(jobs[0] ?? null);
  }, []);

  const refreshAll = useCallback(() => {
    setError(null);
    Promise.all([loadStats(), loadAes(), loadJob(), loadShadow()]).catch((e) =>
      setError(e instanceof ApiError ? e.message : "Failed to load admin data."),
    );
  }, [loadStats, loadAes, loadJob, loadShadow]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  // Poll while a job is in flight.
  useEffect(() => {
    if (!activeJob(job)) return;
    const id = setInterval(() => {
      loadJob().catch(() => {});
      loadStats().catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [job, loadJob, loadStats]);

  async function commitPercent(p: number) {
    try {
      await api.put("/admin/settings/qualify-percent", { percent: p });
      await loadStats();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save the bar.");
    }
  }

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed.");
    } finally {
      setBusy(null);
    }
  }

  const bar = stats?.bar ?? 40;

  return (
    <div>
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">⚙️ Admin</h1>
          <p className="mt-1 text-sm text-muted">Run the pipeline and monitor the team.</p>
        </div>
        <Button variant="outline" onClick={refreshAll}>
          Refresh
        </Button>
      </header>

      {error && <div className="mb-4 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>}
      {note && <div className="mb-4 rounded-lg bg-success/10 px-3 py-2 text-sm text-success">{note}</div>}

      {/* Enrichment strength */}
      <Section title="🎚️ Enrichment strength">
        <Card className="p-5">
          <div className="flex items-center gap-4">
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={percent}
              onChange={(e) => setPercent(Number(e.target.value))}
              onPointerUp={() => commitPercent(percent)}
              onKeyUp={() => commitPercent(percent)}
              className="flex-1 accent-[var(--brand)]"
            />
            <span className="w-12 text-right text-lg font-bold">{percent}%</span>
          </div>
          <p className="mt-3 text-sm text-muted">
            Leads must score <span className="font-medium text-foreground">≥ {bar}/100</span> to reach AEs
            ({percent}% → bar {bar}).
            {stats && (
              <>
                {" "}Right now <span className="font-medium text-foreground">{stats.passing} of {stats.scored}</span>{" "}
                scored leads clear this bar.
              </>
            )}
          </p>
        </Card>
      </Section>

      {/* Gather & enrich */}
      <Section title="☁️ Gather & enrich">
        <Card className="p-5">
          {activeJob(job) ? (
            <div className="space-y-3">
              <p className="text-sm">
                Job #{job!.id} — <span className="font-medium capitalize">{job!.status}</span>
                {job!.status === "pending" && " (waiting for the Railway worker)"}
              </p>
              <Progress
                value={job!.requested ? Math.min(100, ((job!.sourced ?? 0) / job!.requested) * 100) : 0}
                label={`Sourcing ${job!.sourced ?? 0}/${job!.requested ?? 0}`}
              />
              {!!job!.to_enrich && (
                <Progress
                  value={Math.min(100, ((job!.enriched ?? 0) / job!.to_enrich) * 100)}
                  label={`Enriching ${job!.enriched ?? 0}/${job!.to_enrich}`}
                />
              )}
              <div className="flex gap-3">
                <Button variant="outline" onClick={() => loadJob()}>
                  Refresh status
                </Button>
                <Button
                  variant="danger"
                  disabled={busy === "cancel"}
                  onClick={() =>
                    run("cancel", async () => {
                      await api.post(`/admin/pipeline-jobs/${job!.id}/cancel`);
                      await loadJob();
                    })
                  }
                >
                  Cancel job
                </Button>
              </div>
            </div>
          ) : (
            <>
              {job && (
                <p className="mb-3 text-sm text-muted">
                  Last job #{job.id} ({job.status}): {job.message || "no summary"}
                </p>
              )}
              <div className="flex flex-wrap items-end gap-3">
                <label className="text-sm">
                  <span className="mb-1 block text-muted">Leads to source</span>
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    step={100}
                    value={sourceCount}
                    onChange={(e) => setSourceCount(Number(e.target.value))}
                    className="w-32 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
                  />
                </label>
                <Button
                  disabled={busy === "gather"}
                  onClick={() =>
                    run("gather", async () => {
                      await api.post("/admin/pipeline-job", { count: sourceCount });
                      await loadJob();
                    })
                  }
                >
                  {busy === "gather" ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : "🚀 Source & enrich"}
                </Button>
              </div>
              <p className="mt-2 text-xs text-muted">
                Queues a cloud job — sources new leads (random incorporation date per 100) then enriches them.
              </p>
            </>
          )}
        </Card>
      </Section>

      {/* Lead distribute */}
      <Section title="🔝 Lead distribute">
        <Card className="p-5">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm">
              <span className="mb-1 block text-muted">Target pending per AE</span>
              <input
                type="number"
                min={1}
                max={100}
                value={target}
                onChange={(e) => setTarget(Number(e.target.value))}
                className="w-32 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
              />
            </label>
            <Button
              disabled={busy === "distribute"}
              onClick={() =>
                run("distribute", async () => {
                  const rows = await api.post<AllocationRow[]>("/admin/allocation/topup", {
                    commit: true,
                    target,
                  });
                  setAllocation(rows);
                  setNote(
                    rows.length
                      ? `Topped up ${rows.length} people with ${rows.reduce((s, r) => s + r.Assigned, 0)} leads.`
                      : "Everyone's already at target, or the qualified pool is empty.",
                  );
                  await Promise.all([loadStats(), loadAes()]);
                })
              }
            >
              {busy === "distribute" ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : "⬆️ Top up the team"}
            </Button>
            {stats && (
              <span className="text-sm text-muted">Qualified pool available: {stats.awaiting_allocation}</span>
            )}
          </div>
          {allocation && allocation.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                    <th className="py-2 pr-3">AE</th>
                    <th className="py-2 pr-3">Assigned</th>
                    <th className="py-2 pr-3">Avg score</th>
                    <th className="py-2 pr-3">Now pending</th>
                  </tr>
                </thead>
                <tbody>
                  {allocation.map((r) => (
                    <tr key={r.AE} className="border-b border-border/60">
                      <td className="py-2 pr-3 font-medium capitalize">{r.AE}</td>
                      <td className="py-2 pr-3">{r.Assigned}</td>
                      <td className="py-2 pr-3">{r["Avg Score"]}</td>
                      <td className="py-2 pr-3">{r["Now Pending"]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </Section>

      {/* Pipeline health */}
      <Section title="📊 Pipeline health">
        {stats ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Metric label="Total leads" value={stats.total} />
            <Metric label="Qualified" value={stats.qualified} accent />
            <Metric label="Awaiting enrichment" value={stats.awaiting_enrichment} />
            <Metric label="Awaiting allocation" value={stats.awaiting_allocation} />
            <Metric label="Avg score (qualified)" value={`${stats.avg_qualified}/100`} />
            <Metric label="Screened out" value={stats.screened_out} />
          </div>
        ) : (
          <div className="flex justify-center py-6">
            <Spinner className="h-6 w-6" />
          </div>
        )}
      </Section>

      {/* Shadow model */}
      <Section title="🧪 Shadow model (evidence only)">
        {!shadow ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-6 w-6" />
          </div>
        ) : !shadow.model_deployed ? (
          <Card className="p-5 text-sm text-muted">
            No model scores yet. The model is committed but hasn’t scored any leads —
            it fills in as new leads pass through enrichment, or immediately after
            running <code className="rounded bg-surface-2 px-1">rescore_leads.py</code>.
          </Card>
        ) : (
          <Card className="p-5">
            <p className="mb-4 text-sm text-muted">
              The trained model runs alongside the rules but drives nothing yet —
              this is here to see whether it would rank leads better before trusting it.
              Measured on decided leads only.
            </p>
            <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Metric label="Pool scored" value={`${shadow.coverage.pct}%`} />
              <Metric label="Decided & scored" value={shadow.decided_scored} />
              {shadow.decided_scored >= 30 ? (
                <>
                  <Metric
                    label="Model AUC"
                    value={shadow.model_auc ?? "—"}
                    accent={
                      shadow.model_auc != null &&
                      shadow.rules_auc != null &&
                      shadow.model_auc > shadow.rules_auc
                    }
                  />
                  <Metric label="Rules AUC" value={shadow.rules_auc ?? "—"} />
                </>
              ) : (
                <div className="col-span-2 flex items-center rounded-lg bg-surface-2 px-4 text-xs text-muted">
                  Need ≥30 decided-and-scored leads for a meaningful comparison
                  ({shadow.decided_scored} so far).
                </div>
              )}
            </div>

            {shadow.decided_scored >= 30 && (
              <div className="grid gap-3 sm:grid-cols-2">
                <Card className="bg-surface-2 p-4">
                  <div className="text-[11px] uppercase tracking-wide text-muted">
                    Approval rate in each side’s top 20%
                  </div>
                  <div className="mt-2 flex items-end gap-6">
                    <div>
                      <div className="text-2xl font-bold text-brand">
                        {shadow.model_precision_at_top ?? "—"}%
                      </div>
                      <div className="text-xs text-muted">model-ranked</div>
                    </div>
                    <div>
                      <div className="text-2xl font-bold">
                        {shadow.rules_precision_at_top ?? "—"}%
                      </div>
                      <div className="text-xs text-muted">rules-ranked</div>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-muted">
                    Higher = better leads at the top of the pile. The question a
                    model-ranked queue would answer.
                  </p>
                </Card>

                <Card className="bg-surface-2 p-4">
                  <div className="mb-2 text-[11px] uppercase tracking-wide text-muted">
                    Approval rate by model score
                  </div>
                  {shadow.model_bands.length === 0 ? (
                    <p className="text-xs text-muted">No bands yet.</p>
                  ) : (
                    <div className="space-y-1.5">
                      {shadow.model_bands.map((b) => (
                        <div key={b.band} className="flex items-center gap-2 text-xs">
                          <span className="w-14 tabular-nums text-muted">{b.band}</span>
                          <div className="h-4 flex-1 overflow-hidden rounded bg-surface">
                            <div
                              className="h-full rounded bg-brand"
                              style={{ width: `${b.approval_rate}%` }}
                            />
                          </div>
                          <span className="w-16 text-right tabular-nums">
                            {b.approval_rate}% (n={b.n})
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  <p className="mt-2 text-xs text-muted">
                    Climbing left-to-right = the model’s score means something.
                  </p>
                </Card>
              </div>
            )}
          </Card>
        )}
      </Section>

      {/* AE performance */}
      <Section title="🧑‍💻 AE performance">
        <Card className="p-4">
          {aes.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted">No AEs yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                    <th className="py-2 pr-3">AE</th>
                    <th className="py-2 pr-3">Leads remaining</th>
                    <th className="py-2 pr-3">Total assigned</th>
                    <th className="py-2 pr-3">Approvals</th>
                    <th className="py-2 pr-3">SF entries</th>
                  </tr>
                </thead>
                <tbody>
                  {aes.map((a) => (
                    <tr key={a.ae} className="border-b border-border/60">
                      <td className="py-2 pr-3 font-medium capitalize">{a.ae}</td>
                      <td className="py-2 pr-3">{a.remaining}</td>
                      <td className="py-2 pr-3">{a.total_assigned}</td>
                      <td className="py-2 pr-3">{a.approved}</td>
                      <td className="py-2 pr-3">{a.sf_entry}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </Section>

      {/* Danger zone */}
      <Section title="🧹 Cleanup">
        <Card className="flex flex-wrap gap-3 p-5">
          <Button
            variant="outline"
            disabled={busy === "clearWorking"}
            onClick={() =>
              run("clearWorking", async () => {
                const r = await api.post<{ message: string }>("/admin/clear/working");
                setNote(r.message);
                await Promise.all([loadStats(), loadAes(), loadJob()]);
              })
            }
          >
            Clear working pool
          </Button>
          {confirmClear ? (
            <div className="flex items-center gap-2">
              <span className="text-sm text-danger">Archive + clear approved pipeline?</span>
              <Button
                variant="danger"
                disabled={busy === "clearPipeline"}
                onClick={() =>
                  run("clearPipeline", async () => {
                    const r = await api.post<{ message: string }>("/admin/clear/pipeline");
                    setNote(r.message);
                    setConfirmClear(false);
                    await Promise.all([loadStats(), loadAes(), loadJob()]);
                  })
                }
              >
                Yes, clear
              </Button>
              <Button variant="ghost" onClick={() => setConfirmClear(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button variant="outline" onClick={() => setConfirmClear(true)}>
              Clear pipeline (archive approved)
            </Button>
          )}
        </Card>
      </Section>
    </div>
  );
}
