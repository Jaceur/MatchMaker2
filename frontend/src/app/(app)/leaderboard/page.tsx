"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { LeaderboardRow } from "@/lib/types";
import { Card, Spinner } from "@/components/ui";

const MEDAL = ["🥇", "🥈", "🥉"];

export default function LeaderboardPage() {
  const { user } = useAuth();
  const [rows, setRows] = useState<LeaderboardRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<LeaderboardRow[]>("/leaderboard")
      .then(setRows)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold">🏆 Leaderboard</h1>
        <p className="mt-1 text-sm text-muted">Ranked by activity points.</p>
      </header>

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-8 w-8" />
        </div>
      ) : error ? (
        <div className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>
      ) : rows.length === 0 ? (
        <Card className="p-8 text-center text-sm text-muted">No AEs to rank yet.</Card>
      ) : (
        <Card className="divide-y divide-border">
          {rows.map((r) => {
            const isMe = r.ae === user?.username;
            return (
              <div
                key={r.ae}
                className={`flex items-center gap-4 px-4 py-3 ${isMe ? "bg-brand/5" : ""}`}
              >
                <div className="w-8 text-center text-lg font-bold">
                  {MEDAL[r.rank - 1] ?? <span className="text-muted">{r.rank}</span>}
                </div>
                <div className="flex-1">
                  <div className="font-medium capitalize">
                    {r.ae} {isMe && <span className="text-xs text-brand">(you)</span>}
                  </div>
                  <div className="text-xs text-muted">
                    {r.leads_swiped} swipes · {r.leads_saved} saved · {r.urls_added} URLs
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-lg font-bold text-brand">{r.points}</div>
                  <div className="text-[11px] uppercase tracking-wide text-muted">pts</div>
                </div>
              </div>
            );
          })}
        </Card>
      )}
    </div>
  );
}
