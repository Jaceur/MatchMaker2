"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { MeStats } from "@/lib/types";
import { Button, Card, Spinner } from "@/components/ui";

export default function DashboardPage() {
  const { user } = useAuth();
  const [stats, setStats] = useState<MeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Change-password form
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pwBusy, setPwBusy] = useState(false);

  useEffect(() => {
    api
      .get<MeStats>("/me/stats")
      .then(setStats)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load."))
      .finally(() => setLoading(false));
  }, []);

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwBusy(true);
    setPwMsg(null);
    try {
      const res = await api.post<{ message: string }>("/me/change-password", {
        current_password: cur,
        new_password: next,
      });
      setPwMsg({ ok: true, text: res.message });
      setCur("");
      setNext("");
    } catch (err) {
      setPwMsg({ ok: false, text: err instanceof ApiError ? err.message : "Failed." });
    } finally {
      setPwBusy(false);
    }
  }

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold capitalize">Hi, {user?.username} 👋</h1>
        <p className="mt-1 text-sm text-muted">Your pipeline at a glance.</p>
      </header>

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-8 w-8" />
        </div>
      ) : error ? (
        <div className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>
      ) : stats ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Metric label="Pipeline" value={stats.pipeline_count} />
          <Metric label="Into CRM" value={stats.into_crm} />
          <Metric label="Points" value={stats.points} accent />
          <Metric label="Swipes" value={stats.leads_swiped} />
        </div>
      ) : null}

      <Card className="mt-8 max-w-md p-5">
        <h2 className="text-sm font-semibold">Change password</h2>
        <form onSubmit={changePassword} className="mt-3 space-y-3">
          <input
            type="password"
            value={cur}
            onChange={(e) => setCur(e.target.value)}
            placeholder="Current password"
            className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
          />
          <input
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            placeholder="New password"
            className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
          />
          {pwMsg && (
            <p className={`text-sm ${pwMsg.ok ? "text-success" : "text-danger"}`}>{pwMsg.text}</p>
          )}
          <Button type="submit" disabled={pwBusy || !cur || !next}>
            {pwBusy ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : "Update password"}
          </Button>
        </form>
      </Card>
    </div>
  );
}

function Metric({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <Card className="p-4">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${accent ? "text-brand" : ""}`}>{value}</div>
    </Card>
  );
}
