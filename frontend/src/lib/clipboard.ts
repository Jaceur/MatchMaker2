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

// Salesforce requires a Last Name, and a company whose only shareholder is
// another company often has no individual on file at all — Companies House has
// a corporate PSC and no named person. "Unknown" is what goes in the field then.
//
// It also fixes a subtler bug that affected every nameless lead: writing an
// EMPTY string may not create a Win+V clipboard-history entry at all, which
// silently shortens the sequence from 5 to 4 and shifts every following paste
// up a field. A placeholder keeps the positions aligned.
export const SF_UNKNOWN_LAST_NAME = "Unknown";

export function lastNameOrUnknown(lastName?: string | null): string {
  return (lastName || "").trim() || SF_UNKNOWN_LAST_NAME;
}

// The Salesforce fields, ordered so Win+V (newest-first) reads down the form:
// First name, Last name, Company, Phone.
//
// Title used to be a fifth entry, hardcoded to "Director" — dropped 2026-08-11
// because it carried no information (every one of these people is a director by
// definition) and cost a paste. Four entries, one fewer keystroke per lead.
export function salesforceFields(firstName: string, lastName: string, company: string): string[] {
  return [fakeMobile(), company || "", lastNameOrUnknown(lastName), firstName || ""];
}
