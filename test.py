from ddgs import DDGS
import re

raw_company_name = "REVOLUT LTD"

# 1. Clean the name
company_name_clean = re.sub(
    r'\b(LTD|LIMITED|LLP|PLC|UK|HOLDINGS|GROUP|ENTERPRISES|SERVICES)\b', 
    '', 
    raw_company_name, 
    flags=re.IGNORECASE
).strip()
company_name_clean = re.sub(r'\s+', ' ', company_name_clean)

print(f"Searching DuckDuckGo for: {company_name_clean}...")

# 2. Build the query
search_query = f'{company_name_clean} UK site:linkedin.com/company/'

# 3. Search without any API keys!
try:
    with DDGS() as ddgs:
        # We ask for just the top 1 result
        results = list(ddgs.text(search_query, max_results=1))
        
        if results:
            print("\n--- SUCCESS! ---")
            print(f"Title: {results[0].get('title')}")
            print(f"Link:  {results[0].get('href')}")
        else:
            print("\n--- No results found ---")
except Exception as e:
    print(f"An error occurred: {e}")