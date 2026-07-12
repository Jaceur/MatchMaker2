"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Lead, DirectorEmails, EmailVerdict } from "@/lib/types";
import { bareDomain } from "@/lib/format";
import { Button, Card, CopyButton, Spinner } from "./ui";

// "Won" retired for GDPR → "Existing Account - Already Claimed".
export const CRM_STATUS_OPTIONS = [
  "Net New",
  "Existing Lead - Unclaimed",
  "Existing Lead - Already Claimed",
  "Existing Account - Unclaimed",
  "Existing Account - Already Claimed",
  "Disqualified",
];

const salesNavSearch = (name: string) =>
  `https://www.linkedin.com/sales/search/company?keywords=${encodeURIComponent(name)}`;
const mailmeteor = (email: string) =>
  `https://mailmeteor.com/email-checker?email=${encodeURIComponent(email)}`;

// One accepted/rejected step position per director.
interface Step {
  idx: number;
  acceptedIdx: number | null;
}

function SearchLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium transition hover:border-brand hover:text-brand"
    >
      {children}
    </a>
  );
}

// One approved lead awaiting a CRM status: vet a best-guess director email
// one-at-a-time (verify via Mailmeteor, then ✓ accept or ✗ next), pick a status,
// save. Mirrors ae_dashboard._classify_card, streamlined.
export function ClassifyCard({ lead, onDone }: { lead: Lead; onDone: () => void }) {
  const [emails, setEmails] = useState<DirectorEmails[] | null>(null);
  const [steps, setSteps] = useState<Record<string, Step>>({});
  const [crm, setCrm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<DirectorEmails[]>(`/pipeline/${lead.id}/email-candidates`)
      .then((data) => {
        setEmails(data);
        setSteps(
          Object.fromEntries(data.map((d) => [d.director_name, { idx: 0, acceptedIdx: null }])),
        );
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load director emails."));
  }, [lead.id]);

  const linkedin = lead.corrected_linkedin_url || lead.linkedin_url;
  const website = lead.corrected_website_url || lead.website_url;
  const domain = bareDomain(website);

  function setStep(director: string, patch: Partial<Step>) {
    setSteps((s) => ({ ...s, [director]: { ...s[director], ...patch } }));
  }

  async function save() {
    if (!crm) {
      setError("Pick a CRM status first.");
      return;
    }
    setSaving(true);
    setError(null);
    const verdicts: EmailVerdict[] = [];
    (emails || []).forEach((d) => {
      const acceptedIdx = steps[d.director_name]?.acceptedIdx ?? null;
      d.candidates.forEach((c, i) => {
        verdicts.push({
          director_name: d.director_name,
          pattern: c.pattern,
          email: c.email,
          selected: acceptedIdx === i,
        });
      });
    });
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

      {/* Research shortcuts */}
      <div className="mt-3 flex flex-wrap gap-2">
        {linkedin && (
          <SearchLink href={salesNavSearch(lead.company_name)}>💼 LinkedIn Search</SearchLink>
        )}
        {domain && (
          <CopyButton
            text={domain}
            copiedLabel={`✓ Copied ${domain}`}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium transition hover:border-brand hover:text-brand"
          >
            🔎 Business Search
          </CopyButton>
        )}
        {!linkedin && !domain && <span className="text-sm text-muted">No LinkedIn or website</span>}
      </div>

      {/* Director emails — one suggestion at a time, most popular first */}
      <div className="mt-4">
        {emails === null ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner className="h-4 w-4" /> Loading directors…
          </div>
        ) : emails.length === 0 ? (
          <p className="text-sm text-muted">No directors found.</p>
        ) : (
          <div className="space-y-3">
            {emails.map((d) => {
              const step = steps[d.director_name] || { idx: 0, acceptedIdx: null };
              return (
                <div key={d.director_name} className="rounded-lg bg-surface-2 p-3">
                  <p className="text-sm font-medium">👤 {d.director_name}</p>

                  {d.candidates.length === 0 ? (
                    <p className="mt-1 text-xs text-muted">No website domain — can&apos;t suggest emails.</p>
                  ) : step.acceptedIdx !== null ? (
                    <div className="mt-1.5 flex items-center justify-between gap-2">
                      <a
                        href={mailmeteor(d.candidates[step.acceptedIdx].email)}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate text-sm font-medium text-success underline"
                      >
                        ✓ {d.candidates[step.acceptedIdx].email}
                      </a>
                      <button
                        type="button"
                        onClick={() => setStep(d.director_name, { acceptedIdx: null })}
                        className="shrink-0 text-xs text-muted hover:text-foreground"
                      >
                        change
                      </button>
                    </div>
                  ) : step.idx < d.candidates.length ? (
                    <div className="mt-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <a
                          href={mailmeteor(d.candidates[step.idx].email)}
                          target="_blank"
                          rel="noreferrer"
                          className="truncate text-sm text-brand underline"
                          title="Verify on Mailmeteor"
                        >
                          {d.candidates[step.idx].email}
                        </a>
                        <div className="flex shrink-0 gap-1.5">
                          <button
                            type="button"
                            aria-label="Reject, show next"
                            onClick={() => setStep(d.director_name, { idx: step.idx + 1 })}
                            className="rounded-md bg-surface px-2.5 py-1 text-sm font-medium text-danger hover:bg-danger hover:text-white"
                          >
                            ✗
                          </button>
                          <button
                            type="button"
                            aria-label="Accept this email"
                            onClick={() => setStep(d.director_name, { acceptedIdx: step.idx })}
                            className="rounded-md bg-surface px-2.5 py-1 text-sm font-medium text-success hover:bg-success hover:text-white"
                          >
                            ✓
                          </button>
                        </div>
                      </div>
                      <p className="mt-1 text-[11px] text-muted">
                        Guess {step.idx + 1} of {d.candidates.length} · tap the email to verify
                      </p>
                    </div>
                  ) : (
                    <div className="mt-1.5 flex items-center justify-between gap-2">
                      <span className="text-xs text-muted">No email accepted.</span>
                      <button
                        type="button"
                        onClick={() => setStep(d.director_name, { idx: 0 })}
                        className="text-xs text-brand hover:underline"
                      >
                        start over
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
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
