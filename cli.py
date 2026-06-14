"""Terminal control panel for running the pipeline by hand.

Run with `python cli.py`. This is the offline counterpart to the Admin
Dashboard — handy for sourcing/enriching/wiping without the Streamlit UI.
"""
from sourcing import fetch_and_store_random_batch
from enrichment import enrich_sourced_leads
from leads import clear_database


def main_menu():
    while True:
        print("\n" + "="*35 + "\n MATCHMAKER 2.0 - CONTROL PANEL\n" + "="*35)
        print("1. Source: Pull 100 companies from a random day\n2. Enrich: Process a batch of 100 leads\n3. Enrich: Process ALL un-enriched leads\n4. Clear:  Wipe entire database table\n5. Exit")
        choice = input("\nEnter your choice (1-5): ").strip()

        if choice == '1': fetch_and_store_random_batch()
        elif choice == '2': enrich_sourced_leads(limit=100)
        elif choice == '3':
            if input("WARNING: This will process ALL 'sourced' leads. Continue? (y/n): ").strip().lower() == 'y': enrich_sourced_leads(limit=None)
        elif choice == '4':
            if input("DANGER: Are you sure you want to completely WIPE the database? (y/n): ").strip().lower() == 'y': clear_database()
        elif choice == '5': break
        else: print("Invalid choice. Please enter 1, 2, 3, 4, or 5.")


if __name__ == "__main__":
    main_menu()
