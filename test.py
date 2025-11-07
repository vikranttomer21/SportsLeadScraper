# simple_sorter.py
import gspread
import time

# -------------------- CONFIG --------------------
GOOGLE_SHEETS_CREDENTIALS = "service_account.json"
SPREADSHEET_NAME = "Sports Scraper"

# --- INPUT SHEET ---
INPUT_SHEET = "Discovered Entities" 

# --- ✅ NEW, SIMPLER OUTPUT SHEETS ---
OUTPUT_SHEETS = {
    "P1": "P1 - Hot Leads (No Website)",
    "P2": "P2 - Leads (Have Website)",
    "P3": "P3 - Rejects (No Presence)"
}
OUTPUT_HEADERS = ["Entity Name", "Type", "Official Website", "phone", "Contacts", "Socials", "Address", "Source URL", "Notes"]

# -------------------- SETUP --------------------
def safe_print(*args, **kwargs):
    """Prevents print errors in some environments."""
    try: print(*args, **kwargs)
    except: pass

# Configure Google Sheets
try:
    gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)
    sh = gc.open(SPREADSHEET_NAME)

    try:
        input_sheet = sh.worksheet(INPUT_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        safe_print(f"❌ FATAL ERROR: Input sheet '{INPUT_SHEET}' not found!")
        exit()
    
    # --- Create Sheets and Headers if they don't exist ---
    output_worksheets = {}
    for key, sheet_name in OUTPUT_SHEETS.items():
        try:
            output_worksheets[key] = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            safe_print(f" - Sheet '{sheet_name}' not found. Creating it...")
            output_worksheets[key] = sh.add_worksheet(title=sheet_name, rows="2000", cols=len(OUTPUT_HEADERS) + 1)
            output_worksheets[key].update('A1', [OUTPUT_HEADERS])
            safe_print(f"   - Created sheet and added headers.")
            
    # --- Resumability: Load all entities that have already been triaged ---
    processed_entities = set()
    for sheet in output_worksheets.values():
        try:
            names = sheet.col_values(1)[1:] # Get all names from Column A, skip header
            for name in names:
                processed_entities.add(name.lower())
        except Exception as e:
            safe_print(f" - Warning: Could not read processed entities from a sheet: {e}")
            
    safe_print(f"✅ Google Sheets connected. Found {len(processed_entities)} already triaged entities.")
except Exception as e:
    safe_print(f"❌ FATAL ERROR: GOOGLE SHEETS SETUP FAILED: {e}")
    exit()

# -------------------- MAIN WORKFLOW (TRIAGE & SORT) --------------------
def main():
    try:
        print("\n--- STARTING SIMPLE TRIAGE & SORTING AGENT ---")
        
        all_discovered_rows = input_sheet.get_all_values()[1:] # Skip header
        print(f"  Found {len(all_discovered_rows)} total discovered entities.")

        # --- Resumability: Filter out entities we've already triaged ---
        entities_to_triage = []
        for row in all_discovered_rows:
            if row and len(row) > 0:
                entity_name = row[0].strip().lower()
                if entity_name not in processed_entities:
                    entities_to_triage.append(row)
        
        print(f"  Found {len(entities_to_triage)} new entities to triage and sort.")
        if not entities_to_triage:
            print("  No new entities to process. Exiting.")
            return

        # Prepare lists to batch-append to sheets
        rows_to_save = {key: [] for key in OUTPUT_SHEETS.keys()}
        
        for i, row in enumerate(entities_to_triage):
            # Headers: ["Entity Name", "Type", "Official Website", "phone", "Contacts", "Socials", "Address", "Source URL", "Notes"]
            if len(row) < 8: # Ensure row has at least 8 columns
                safe_print(f"  Skipping malformed row: {row}")
                continue

            entity_name = row[0]
            website_url = row[2]
            socials = row[5] # Column F
            
            safe_print(f"\n  Processing ({i+1}/{len(entities_to_triage)}): {entity_name}")
            
            # --- APPLYING YOUR NEW, SIMPLE TRIAGE LOGIC ---
            
            if website_url == "NA":
                if socials != "NA":
                    # P1: No Website, Has Socials
                    safe_print(f"    - Decision: P1 (Hot Lead - No Website, Has Socials)")
                    rows_to_save["P1"].append(row)
                else:
                    # P3: No Website, No Socials
                    safe_print(f"    - Decision: P3 (Reject - No Presence)")
                    rows_to_save["P3"].append(row)
            else:
                # P2: Has a Website
                safe_print(f"    - Decision: P2 (Lead - Has Website)")
                rows_to_save["P2"].append(row)
            
        # --- Final Step: Batch-save all sorted entities to their sheets ---
        for tier_key, rows in rows_to_save.items():
            if rows:
                sheet_name = OUTPUT_SHEETS[tier_key]
                safe_print(f"\nSaving {len(rows)} entities to '{sheet_name}'...")
                try:
                    output_worksheets[tier_key].append_rows(rows, value_input_option='USER_ENTERED')
                except Exception as sheet_err:
                    safe_print(f"  - ❌ FAILED to save to {sheet_name}: {sheet_err}")

    except KeyboardInterrupt:
        safe_print("Interrupted by user — exiting.")
    except Exception as e:
        safe_print(f"MAIN ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        safe_print("\n--- Triage complete. ---")

if __name__ == "__main__":
    main()