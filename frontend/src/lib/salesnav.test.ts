/* Tests for the Sales Navigator deep link. Node strips the types itself, so
   there's no build step and no test runner to install:

       node frontend/src/lib/salesnav.test.ts        (from the project root)

   What this really guards is the encoding. SalesNav's query DSL is double-
   encoded and the parentheses must stay literal — get either wrong and the link
   opens an empty search rather than failing loudly, which is the worst kind of
   bug in a tool people use in a hurry.
*/
import { hasRegion, regionLabel, salesNavPeopleUrl, salesNavRegionCheck } from "./salesnav.ts";

let failures = 0;
const check = (label: string, cond: boolean) => {
  console.log(`${cond ? "PASS" : "FAIL"}  ${label}`);
  if (!cond) failures++;
};

// A real URL captured from Sales Navigator, minus the personal
// recentSearchParam/sessionId. If this ever stops matching, the encoding broke.
const CAPTURED =
  "https://www.linkedin.com/sales/search/people?query=(spellCorrectionEnabled%3Atrue%2C" +
  "filters%3AList((type%3AREGION%2Cvalues%3AList((id%3A100752109%2C" +
  "text%3AScotland%252C%2520United%2520Kingdom%2CselectionType%3AINCLUDED))))%2C" +
  "keywords%3AReginald%2520Barstow)";

check("reproduces a real captured SalesNav URL byte-for-byte",
      salesNavPeopleUrl("Reginald Barstow", "Scotland") === CAPTURED);

// The residency has to actually drive the filter — not one hardcoded region.
const uk = salesNavPeopleUrl("Jane Smith", "United Kingdom");
const england = salesNavPeopleUrl("Jane Smith", "England");
check("United Kingdom and England resolve to DIFFERENT geo ids", uk !== england);
check("a third country resolves differently again",
      salesNavPeopleUrl("Jane Smith", "Turkey") !== uk);

// Companies House is free text: same country, many spellings.
check("case and whitespace tolerated", hasRegion("  ENGLAND  "));
check("alias: UK", regionLabel("UK") === "United Kingdom");
check("alias: Great Britain", regionLabel("Great Britain") === "United Kingdom");
check("alias: USA", regionLabel("USA") === "United States");
check("alias: Türkiye", regionLabel("Türkiye") === "Turkey");

// THE IMPORTANT ONE. An unknown country must fall back to a name-only search.
// Inventing a geo id would return zero results, and the AE would read that as
// "not on LinkedIn" rather than "we guessed the country id wrong".
const unmapped = salesNavPeopleUrl("Jane Smith", "Ruritania");
check("unmapped residency adds NO region filter", !unmapped.includes("REGION"));
check("unmapped residency still searches by name", unmapped.includes("Jane%2520Smith"));
check("missing residency is handled", !salesNavPeopleUrl("Jane Smith").includes("REGION"));
check("hasRegion is honest about what it can filter",
      !hasRegion("Ruritania") && !hasRegion(null) && !hasRegion(""));

// The verification helper must cover every mapped region, or ids go unchecked.
check("region check list covers the whole map",
      Object.keys(salesNavRegionCheck()).length >= 20);

console.log(failures ? `\n${failures} FAILURE(S)` : "\nAll SalesNav checks passed.");
// exitCode rather than exit(): process.exit() tears down mid-flush on Windows
// and libuv prints an assertion failure after an otherwise clean run.
process.exitCode = failures ? 1 : 0;
