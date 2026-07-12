"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Lead, DirectorEmails, EmailVerdict } from "@/lib/types";
import { Button, Card, Spinner } from "./ui";

// "Won" retired for GDPR → "Existing Account - Already Claimed". Two claimed/
// unclaimed variants each for accounts and leads.
export const CRM_STATUS_OPTIONS = [
  "Net New",
  "Existing Lead - Unclaimed",
  "Existing Lead - Already Claimed",
  "Existing Account - Unclaimed",
  "Existing Account - Already Claimed",
  "Disqualified",
];

// One approved lead awaiting a CRM status: confirm director-email guesses, pick a
// status, save. Mirrors ae_dashboard._classify_card.
export function ClassifyCard({ lead, onDone }: { lead: Lead; onDone: () => void }) {
  const [emails, setEmails] = useState<DirectorEmails[] | null>(null);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [crm, setCrm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<DirectorEmails[]>(`/pipeline/${lead.id}/email-candidates`)
      .then(setEmails)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load director emails."));
  }, [lead.id]);

  const website = lead.corrected_website_url || lead.website_url;
  const linkedin = lead.corrected_linkedin_url || lead.linkedin_url;

  function toggle(key: string) {
    setSelected((s) => ({ ...s, [key]: !s[key] }));
  }

  async function save() {
    if (!crm) {
      setError("Pick a CRM status first.");
      return;
    }
    setSaving(true);
    setError(null);
    const verdicts: EmailVerdict[] = [];
    (emails || []).forEach((d) =>
      d.candidates.forEach((c) => {
        const key = `${d.director_name}|${c.pattern}`;
        verdicts.push({
          director_name: d.director_name,
          pattern: c.pattern,
          email: c.email,
          selected: !!selected[key],
        });
      }),
    );
    try {
      await api.post(`/pipeline/${lead.id}/classify`, { crm_status: crm, email_verdicts: verdicts });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Save failed.");
      setSaving(false);
    }
  }

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold">{lead.company_name}</p>
          <p className="font-mono text-xs text-muted">{lead.crn}</p>
        </div>
        {lead.lead_score != null && (
          <span className="shrink-0 text-sm text-muted">fit {lead.lead_score}</span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-3 text-sm">
        {website && (
          <a href={website} target="_blank" rel="noreferrer" className="text-brand underline">
            🌐 Website
          </a>
        )}
        {linkedin && (
          <a href={linkedin} target="_blank" rel="noreferrer" className="text-brand underline">
            💼 LinkedIn
          </a>
        )}
        {!website && !linkedin && <span className="text-muted">No links found</span>}
      </div>

      <div className="mt-3">
        {emails === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner className="h-4 w-4" /> Loading directors…
          </div>
        ) : emails.length === 0 ? (
          <p className="text-sm text-muted">No directors found.</p>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-muted">
              Tick each email that looks right (used for outreach).
            </p>
            {emails.map((d) => (
              <div key={d.director_name}>
                <p className="text-sm font-medium">👤 {d.director_name}</p>
                {d.candidates.length === 0 ? (
                  <p className="text-xs text-muted">No website domain — can&apos;t suggest emails.</p>
                ) : (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {d.candidates.map((c) => {
                      const key = `${d.director_name}|${c.pattern}`;
                      const on = !!selected[key];
                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => toggle(key)}
                          className={`rounded-md px-2 py-1 text-xs font-medium transition ${
                            on ? "bg-success text-white" : "bg-surface-2 text-muted hover:text-foreground"
                          }`}
                        >
                          {on ? "✓ " : ""}
                          {c.email}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <select
          value={crm}
          onChange={(e) => setCrm(e.target.value)}
          className="flex-1 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--ring)]"
        >
          <option value="">CRM status…</option>
          {CRM_STATUS_OPTIONS.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        <Button onClick={save} disabled={saving} className="sm:w-28">
          {saving ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : "Save"}
        </Button>
      </div>

      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </Card>
  );
}
