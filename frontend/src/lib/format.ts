// Display helpers for lead fields.

export function formatMoney(v?: number | null): string | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  if (!isFinite(n)) return null;
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `£${(n / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}m`;
  if (abs >= 1_000) return `£${(n / 1_000).toFixed(0)}k`;
  return `£${n.toLocaleString()}`;
}

export function formatDate(v?: string | null): string | null {
  if (!v) return null;
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function companyAge(v?: string | null): string | null {
  if (!v) return null;
  const d = new Date(v);
  if (isNaN(d.getTime())) return null;
  const months = Math.max(0, Math.floor((Date.now() - d.getTime()) / (1000 * 60 * 60 * 24 * 30)));
  if (months < 12) return `${months} mo old`;
  const years = (months / 12).toFixed(months % 12 === 0 ? 0 : 1);
  return `${years} yr old`;
}

// Mirrors scoring.account_tier's small/large read of account_type.
export function accountTier(accountType?: string | null): "Small" | "Large" | null {
  if (!accountType) return null;
  const t = accountType.toLowerCase();
  if (t.includes("total-exemption") || t.includes("abridged") || t.includes("micro") || t.includes("small") || t.includes("dormant"))
    return "Small";
  if (t.includes("full") || t.includes("group") || t.includes("medium")) return "Large";
  return null;
}

export function scoreTone(score?: number | null): "success" | "brand" | "warning" | "danger" {
  const s = score ?? 0;
  if (s >= 70) return "success";
  if (s >= 50) return "brand";
  if (s >= 35) return "warning";
  return "danger";
}

export function directorList(v?: string | null): string[] {
  if (!v) return [];
  return v.split(",").map((d) => d.trim()).filter(Boolean);
}
