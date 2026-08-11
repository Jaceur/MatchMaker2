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

interface Region {
  id: string;
  text: string;
  /** Confirmed against a real SalesNav URL, rather than taken from the public
   *  geo taxonomy. See the warning below. */
  verified?: boolean;
}

// Companies House country-of-residence -> LinkedIn geo id, keyed lowercase.
//
// ⚠️ A WRONG ID FAILS SILENTLY. LinkedIn doesn't reject an unknown geo — it
// returns an empty result set, which in a speed race is worse than no filter at
// all, because the AE concludes the person isn't on LinkedIn and moves on. So:
//
//   - unmapped residency  -> NO region filter (broader search, always finds them)
//   - mapped but wrong id -> zero results (silent, and looks like a real answer)
//
// Only `scotland` is confirmed, from a real captured URL. The rest come from
// LinkedIn's public geo taxonomy and are UNVERIFIED. Check them in two minutes:
// run `salesNavRegionCheck()` in the browser console, open the links, and any
// that return nothing for a common name has a bad id — capture the real one from
// SalesNav's own URL and correct it here.
const REGIONS: Record<string, Region> = {
  // These four cover ~80% of leads: Companies House uses "United Kingdom" and
  // "England" interchangeably for the same place, and both are common.
  "united kingdom": { id: "101165590", text: "United Kingdom" },
  "england": { id: "102299470", text: "England, United Kingdom" },
  "scotland": { id: "100752109", text: "Scotland, United Kingdom", verified: true },
  "wales": { id: "104688473", text: "Wales, United Kingdom" },
  "northern ireland": { id: "103049995", text: "Northern Ireland, United Kingdom" },
  "ireland": { id: "104738515", text: "Ireland" },
  // The next most common residencies in the live data.
  "turkey": { id: "102105699", text: "Turkey" },
  "pakistan": { id: "101022442", text: "Pakistan" },
  "saudi arabia": { id: "100459316", text: "Saudi Arabia" },
  "france": { id: "105015875", text: "France" },
  "china": { id: "102890883", text: "China" },
  "united states": { id: "103644278", text: "United States" },
  "india": { id: "102713980", text: "India" },
  "germany": { id: "101282230", text: "Germany" },
  "spain": { id: "105646813", text: "Spain" },
  "italy": { id: "103350119", text: "Italy" },
  "netherlands": { id: "102890719", text: "Netherlands" },
  "poland": { id: "105072130", text: "Poland" },
  "romania": { id: "106670623", text: "Romania" },
  "nigeria": { id: "105365761", text: "Nigeria" },
  "hong kong": { id: "103291313", text: "Hong Kong SAR" },
  "united arab emirates": { id: "104305776", text: "United Arab Emirates" },
  "singapore": { id: "102454443", text: "Singapore" },
  "australia": { id: "101452733", text: "Australia" },
  "canada": { id: "101174742", text: "Canada" },
};

// Companies House is free text, so the same country arrives spelled several ways.
const ALIASES: Record<string, string> = {
  "uk": "united kingdom",
  "u.k.": "united kingdom",
  "gb": "united kingdom",
  "great britain": "united kingdom",
  "britain": "united kingdom",
  "united kingdom of great britain and northern ireland": "united kingdom",
  "usa": "united states",
  "u.s.a.": "united states",
  "us": "united states",
  "united states of america": "united states",
  "uae": "united arab emirates",
  "republic of ireland": "ireland",
  "eire": "ireland",
  "holland": "netherlands",
  "türkiye": "turkey",
  "turkiye": "turkey",
};

function region(residence?: string | null): Region | null {
  if (!residence) return null;
  const key = String(residence).trim().toLowerCase().replace(/\.$/, "");
  return REGIONS[key] ?? REGIONS[ALIASES[key] ?? ""] ?? null;
}

/**
 * A Sales Navigator people search for this person, narrowed to the country the
 * shareholder actually lives in when we can map it.
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

/** The label we can show for a mapped residency ("England, United Kingdom"). */
export function regionLabel(residence?: string | null): string | null {
  return region(residence)?.text ?? null;
}

/**
 * One search URL per mapped region, for checking the unverified ids. Paste
 * `salesNavRegionCheck()` into the browser console, open a few, and any that
 * return nothing for a common name has the wrong id.
 */
export function salesNavRegionCheck(name = "James Smith"): Record<string, string> {
  return Object.fromEntries(
    Object.entries(REGIONS).map(([k, v]) => [
      `${k}${v.verified ? " (verified)" : ""}`, salesNavPeopleUrl(name, k),
    ]),
  );
}
