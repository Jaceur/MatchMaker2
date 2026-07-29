// Clipboard-history helpers for the "Copy 5 & claim" flow (Win+V paste into
// Salesforce). The New Incorps stream tiles have their own copies of these; this
// module is used by the High-Value Archive table.

// A placeholder UK mobile in Ofcom's reserved fictional range (07700 900000–
// 900999 → +447700900xxx). Valid format, never a real person's line.
export const fakeMobile = () =>
  `+447700900${String(Math.floor(Math.random() * 1000)).padStart(3, "0")}`;

// Write values to the clipboard in sequence so each becomes its own Win+V entry.
// A gap is required — too fast and Windows merges them into one entry. Oldest-
// first; Win+V lists newest-first.
export async function copyToClipboardHistory(values: string[]): Promise<void> {
  for (const v of values) {
    await navigator.clipboard.writeText(v);
    await new Promise((r) => setTimeout(r, 250));
  }
}

// The 5 Salesforce fields, ordered so Win+V (newest-first) reads down the form:
// First name, Last name, Title (Director), Company, Phone.
export function salesforceFields(firstName: string, lastName: string, company: string): string[] {
  return [fakeMobile(), company || "", "Director", lastName || "", firstName || ""];
}
