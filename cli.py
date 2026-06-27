"""Terminal control panel for running the pipeline by hand.

Run with `python cli.py`. This is the offline counterpart to the Admin
Dashboard — handy for sourcing/enriching/wiping without the Streamlit UI.
"""
from sourcing import fetch_and_store_random_batch
from pipeline import run_pipeline
from leads import clear_database


def main_menu():
    while True:
        print("\n" + "="*35 + "\n MATCHMAKER 2.0 - CONTROL PANEL\n" + "="*35)
        print("1. Source: Pull 100 companies from a random day\n2. Enrich: Screen + enrich a batch of 100 sourced leads\n3. Enrich: Screen + enrich ALL sourced leads\n4. Clear:  Wipe the working pool (approved pipeline is kept)\n5. Exit")
        choice = input("\nEnter your choice (1-5): ").strip()

        if choice == '1': fetch_and_store_random_batch()
        elif choice == '2': run_pipeline(limit=100)
        elif choice == '3':
            if input("WARNING: This will screen + enrich ALL 'sourced' leads. Continue? (y/n): ").strip().lower() == 'y': run_pipeline(limit=None)
        elif choice == '4':
            if input("This wipes the working pool (sourced/ready/passed); approved pipeline is preserved. Continue? (y/n): ").strip().lower() == 'y': clear_database()
        elif choice == '5': break
        else: print("Invalid choice. Please enter 1, 2, 3, 4, or 5.")


if __name__ == "__main__":
    main_menu()
