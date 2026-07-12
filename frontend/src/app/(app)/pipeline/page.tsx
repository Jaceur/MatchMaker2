"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Lead, ClassifiedLead } from "@/lib/types";
import { Button, Card, Spinner } from "@/components/ui";
import { ClassifyCard } from "@/components/ClassifyCard";

export default function PipelinePage() {
  const [pending, setPending] = useState<Lead[] | null>(null);
  const [classified, setClassified] = useState<ClassifiedLead[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enriching, setEnriching] = useState<number | null>(null);

  const loadPending = useCallback(async () => {
    try {
      setPending(await api.get<Lead[]>("/pipeline/unclassified"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load pipeline.");
    }
  }, []);

  const loadClassified = useCallback(async () => {
    try {
      setClassified(await api.get<ClassifiedLead[]>("/pipeline/classified"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load classified leads.");
    }
  }, []);

  useEffect(() => {
    loadPending();
    loadClassified();
  }, [loadPending, loadClassified]);

  // "Add to pipeline" runs the (slow) Companies House director fetch, then swaps
  // the gate card for the classify card.
  async function addToPipeline(lead: Lead) {
    setEnriching(lead.id);
    setError(null);
    try {
      const enriched = await api.post<Lead>(`/pipeline/${lead.id}/enrich-directors`);
      setPending((p) => (p || []).map((l) => (l.id === lead.id ? enriched : l)));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Director enrichment failed.");
    } finally {
      setEnriching(null);
    }
  }

  function onClassified(id: number) {
    setPending((p) => (p || []).filter((l) => l.id !== id));
    loadClassified();
  }

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold">🚀 My Pipeline</h1>
        <p className="mt-1 text-sm text-muted">
          Add approved leads to your pipeline, then set their CRM status.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>
      )}

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
          New approved leads
          {pending && pending.length > 0 ? ` (${pending.length})` : ""}
        </h2>
        {pending === null ? (
          <div className="flex justify-center py-8">
            <Spinner className="h-6 w-6" />
          </div>
        ) : pending.length === 0 ? (
          <Card className="p-6 text-center text-sm text-muted">
            Nothing waiting — approve some leads on the Swipe page.
          </Card>
        ) : (
          <div className="space-y-4">
            {pending.map((lead) =>
              lead.directors_enriched ? (
                <ClassifyCard key={lead.id} lead={lead} onDone={() => onClassified(lead.id)} />
              ) : (
                <Card key={lead.id} className="flex items-center justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{lead.company_name}</p>
                    <p className="text-sm text-muted">Ready to add to your pipeline?</p>
                  </div>
                  <Button
                    onClick={() => addToPipeline(lead)}
                    disabled={enriching === lead.id}
                    className="shrink-0"
                  >
                    {enriching === lead.id ? (
                      <>
                        <Spinner className="h-4 w-4 border-white/40 border-t-white" /> Fetching…
                      </>
                    ) : (
                      "Add to pipeline"
                    )}
                  </Button>
                </Card>
              ),
            )}
          </div>
        )}
      </section>

      <section className="mt-10">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
          Classified pipeline
          {classified && classified.length > 0 ? ` (${classified.length})` : ""}
        </h2>
        {classified === null ? (
          <div className="flex justify-center py-8">
            <Spinner className="h-6 w-6" />
          </div>
        ) : classified.length === 0 ? (
          <Card className="p-6 text-center text-sm text-muted">No classified leads yet.</Card>
        ) : (
          <Card className="divide-y divide-border">
            {classified.map((c) => (
              <div key={c.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{c.company_name}</p>
                  <p className="text-xs text-muted">
                    {c.crm_status}
                    {c.date_approved ? ` · ${c.date_approved}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 gap-3 text-sm">
                  {c.website_url && (
                    <a href={c.website_url} target="_blank" rel="noreferrer" className="text-brand underline">
                      site
                    </a>
                  )}
                  {c.linkedin_url && (
                    <a href={c.linkedin_url} target="_blank" rel="noreferrer" className="text-brand underline">
                      in
                    </a>
                  )}
                </div>
              </div>
            ))}
          </Card>
        )}
      </section>
    </div>
  );
}
