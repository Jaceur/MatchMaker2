// Shared types for the Matchmaker API. Leads have ~40 columns and come back as
// loose records, so Lead is an index signature with the fields the UI reads
// named explicitly for autocomplete.

export type Role = "admin" | "ae";

export interface User {
  username: string;
  role: Role;
}

export interface Lead {
  id: number;
  crn: string;
  company_name: string;
  incorporation_date?: string | null;
  sic_codes?: string | null;
  status?: string | null;
  website_url?: string | null;
  linkedin_url?: string | null;
  corrected_website_url?: string | null;
  corrected_linkedin_url?: string | null;
  website_accurate?: boolean | null;
  linkedin_accurate?: boolean | null;
  lead_score?: number | null;
  confidence_score?: number | null;
  website_score?: number | null;
  linkedin_score?: number | null;
  account_type?: string | null;
  active_directors?: string | null;
  directors_enriched?: boolean | null;
  employee_count?: number | null;
  turnover?: number | null;
  cash_at_bank?: number | null;
  foreign_exchange?: number | null;
  trade_debtors?: number | null;
  trade_creditors?: number | null;
  import_activity?: boolean | null;
  export_activity?: boolean | null;
  director_change_recent?: boolean | null;
  is_nabd?: boolean | null;
  website_candidates?: SourceCandidate[] | null;
  linkedin_candidates?: SourceCandidate[] | null;
  // catch-all for the remaining columns
  [key: string]: unknown;
}

export interface SourceCandidate {
  url: string;
  title: string;
  score: number;
}

export interface ClassifiedLead {
  id: number;
  company_name: string;
  confidence_score: number | null;
  website_url: string | null;
  linkedin_url: string | null;
  crm_status: string | null;
  active_directors: string | null;
  is_nabd: boolean | null;
  date_approved: string | null;
}

export interface DirectorEmails {
  director_name: string;
  appointments?: number | null; // total companies this officer is on
  officer_url?: string | null; // Companies House officer page
  candidates: { pattern: string; email: string }[];
}

export interface EmailVerdict {
  director_name: string;
  pattern: string;
  email: string;
  selected: boolean;
}

export interface MeStats {
  pipeline_count: number;
  into_crm: number;
  points: number;
  urls_added: number;
  leads_swiped: number;
  leads_saved: number;
}

export interface LeaderboardRow {
  rank: number;
  ae: string;
  points: number;
  urls_added: number;
  leads_swiped: number;
  leads_saved: number;
}

export interface AdminSettings {
  qualify_percent: number;
  qualify_bar: number;
}

export interface AllocationRow {
  AE: string;
  Assigned: number;
  "Avg Score": number;
  "Now Pending": number;
}

export interface AdminStats {
  total: number;
  screened_out: number;
  awaiting_enrichment: number;
  qualified: number;
  awaiting_allocation: number;
  avg_qualified: number;
  scored: number;
  passing: number;
  bar: number;
  qualify_percent: number;
}

export interface AePerformance {
  ae: string;
  total_assigned: number;
  remaining: number;
  approved: number;
  sf_entry: number;
}

export interface PipelineJob {
  id: number;
  job_type: string;
  requested: number;
  status: string;
  sourced: number;
  to_enrich: number;
  enriched: number;
  message: string | null;
  requested_by: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface Analytics {
  totals: { decided: number; approved: number; approval_rate: number };
  sic: { sic: string; label: string; total: number; approved: number; rate: number }[];
  feature_correlations: { feature: string; corr: number; n: number }[];
  crm_breakdown: {
    crm_status: string;
    count: number;
    avg_cash: number | null;
    avg_staff: number | null;
    avg_fx: number | null;
    avg_turnover: number | null;
    avg_score: number | null;
  }[];
  score_calibration: { band: string; decided: number; approved: number; rate: number }[];
  score_factors: {
    band: string;
    decided: number;
    rate: number;
    avg_cash: number | null;
    avg_staff: number | null;
    avg_fx: number | null;
    avg_turnover: number | null;
    avg_debtors: number | null;
    avg_creditors: number | null;
  }[];
  coverage: { field: string; populated: number; total: number; pct: number }[];
}

export interface PipelineHealth {
  status_counts: Record<string, number>;
  screening: { qualified: boolean; is_holdout: boolean; n: number }[];
  screen_reasons: { screen_reason: string | null; n: number }[];
  qualify_bar: number;
}
