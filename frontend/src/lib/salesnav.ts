// LinkedIn Sales Navigator deep links.
//
// SalesNav encodes its search as a nested DSL in the `query` parameter:
//
//   (spellCorrectionEnabled:true,
//    filters:List((type:REGION,values:List((id:100752109,
//                                           text:Scotland%2C%20United%20Kingdom,
//                                           selectionType:INCLUDED)))),
//    keywords:Reginald%20Barstow)
//
// Two things about the encoding, both load-bearing:
//
//  1. Values are encoded TWICE — once inside the DSL, then again when the whole
//     DSL goes into the URL. That's why a space shows up as %2520 in a real
//     SalesNav URL rather than %20.
//  2. The structural characters `(` `)` must stay literal. `encodeURIComponent`
//     leaves those alone while escaping `:` and `,`, which is exactly the shape
//     LinkedIn produces — so a plain double `encodeURIComponent` reproduces it.
//
// We deliberately drop `recentSearchParam` and `sessionId` from the captured
// URL: both are personal to whoever ran that search (history logging and their
// session), so they'd be meaningless — at best — coming from someone else.

// LinkedIn geo IDs, keyed by the country-of-residence string Companies House
// gives us. These are opaque LinkedIn identifiers and CANNOT be guessed: a wrong
// id doesn't error, it silently returns an empty result set, which is worse than
// no filter. So this map only contains ids captured from a real SalesNav search.
//
// TO ADD ONE: run the search in Sales Navigator with the region filter applied,
// then copy `id` and `text` out of the URL's REGION filter and paste them here.
//
// Worth doing next, by volume: "United Kingdom" (~1,300/week) and "England"
// (~630/week) — Companies House uses both for the same place — then Turkey,
// Pakistan, Saudi Arabia, France, China, United States.
export const LINKEDIN_REGIONS: Record<string, { id: string; text: string }> = {
  "Scotland": { id: "100752109", text: "Scotland, United Kingdom" },
};

function region(residence?: string | null) {
  if (!residence) return null;
  const key = String(residence).trim();
  return LINKEDIN_REGIONS[key]
    ?? LINKEDIN_REGIONS[key.replace(/^the\s+/i, "")]
    ?? null;
}

/**
 * A Sales Navigator people search for this person, optionally narrowed to the
 * region they live in.
 *
 * An unmapped residence just means no region filter — a slightly broader search
 * beats a filter built on a guessed id, which would return nothing at all.
 */
export function salesNavPeopleUrl(name: string, residence?: string | null): string {
  const enc = encodeURIComponent;
  const parts = ["spellCorrectionEnabled:true"];

  const geo = region(residence);
  if (geo) {
    parts.push(
      `filters:List((type:REGION,values:List((id:${geo.id},` +
      `text:${enc(geo.text)},selectionType:INCLUDED))))`,
    );
  }
  parts.push(`keywords:${enc(name.trim())}`);

  // The second encode — see note 1 above.
  return `https://www.linkedin.com/sales/search/people?query=${enc(`(${parts.join(",")})`)}`;
}

/** Whether we'd be able to narrow this search by where the person lives. */
export function hasRegion(residence?: string | null): boolean {
  return region(residence) !== null;
}
