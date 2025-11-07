# triage_agent.py (Script 3: AI Critic & Sales Qualifier)
import os
import re
import json
import time
import random
import gspread
import google.generativeai as genai
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium_stealth import stealth

# -------------------- CONFIG --------------------
# --- SECURITY: Load API Key Safely ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GOOGLE_SHEETS_CREDENTIALS = "service_account.json"
SPREADSHEET_NAME = "Sports Scraper"

# --- INPUT SHEET ---
INPUT_SHEET = "Discovered Entities" 

# --- ✅ OUTPUT SHEETS CONFIGURATION ---
OUTPUT_SHEETS = {
    "P1": "P1 - HOT Leads (<30k Followers)",
    "P2": "P2 - Web Leads (>30k Followers)",
    "P3": "P3 - Redesign Leads (Bad Website)",
    "P4": "P4 - Good Website (Low Priority)",
    "P5": "P5 - Rejects (No Presence)"
}
OUTPUT_HEADERS = ["Entity Name", "Type", "Official Website", "phone", "Contacts", "Socials", "Address", "Source URL", "Notes"]

# --- Use an isolated profile directory ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SELENIUM_PROFILE_DIR = os.path.join(PROJECT_DIR, "SeleniumProfile") 

SCROLL_PAUSES = (0.8, 1.8)
LONG_PAUSE = (2.5, 5.5)

# -------------------- SETUP --------------------
def safe_print(*args, **kwargs):
    """Prevents print errors in some environments."""
    try: print(*args, **kwargs)
    except: pass

# Configure Gemini AI
if not GEMINI_API_KEY:
    safe_print("❌ FATAL ERROR: GEMINI_API_KEY environment variable not set.")
    exit()
try:
    genai.configure(api_key=GEMINI_API_KEY)
    safe_print("✅ Gemini AI configured.")
except Exception as e:
     safe_print(f"❌ FATAL ERROR: Configuring Gemini failed: {e}")
     exit()

# Configure Google Sheets
try:
    gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)
    sh = gc.open(SPREADSHEET_NAME)

    try:
        input_sheet = sh.worksheet(INPUT_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        safe_print(f"❌ FATAL ERROR: Input sheet '{INPUT_SHEET}' not found!")
        exit()
    
    # --- ✅ CREATE SHEETS AND HEADERS IF THEY DON'T EXIST ---
    output_worksheets = {}
    for key, sheet_name in OUTPUT_SHEETS.items():
        try:
            output_worksheets[key] = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            safe_print(f" - Sheet '{sheet_name}' not found. Creating it...")
            output_worksheets[key] = sh.add_worksheet(title=sheet_name, rows="2000", cols=len(OUTPUT_HEADERS) + 1)
            # Add headers
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

# -------------------- UTILITIES --------------------
def safe_parse_json_from_text(text: str):
    """Attempts to robustly parse JSON found within text."""
    if not text: return None
    try: return json.loads(text)
    except: pass
    patterns = [r'```json\s*(\{.*?\})\s*```', r'(\{.*?\})']
    for p in patterns:
        m = re.search(p, text, re.DOTALL)
        if m:
            blob = m.group(1)
            try: return json.loads(blob)
            except Exception: continue
    safe_print("   - Warning: Could not parse JSON from AI response.")
    return None

def random_human_pause(short=False):
    """Adds a randomized delay to mimic human browsing speed."""
    if short: time.sleep(random.uniform(*SCROLL_PAUSES))
    else: time.sleep(random.uniform(*LONG_PAUSE))

# -------------------- SELENIUM HELPERS --------------------
def make_driver():
    """Configures and launches the Selenium WebDriver."""
    options = webdriver.ChromeOptions()
    safe_print(f"Using Selenium profile directory: {SELENIUM_PROFILE_DIR}")
    options.add_argument(f"--user-data-dir={SELENIUM_PROFILE_DIR}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--start-maximized")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32",
                webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
        driver.set_page_load_timeout(45)
        return driver
    except Exception as e:
        safe_print(f"❌ FATAL ERROR: Failed to initialize WebDriver: {e}")
        safe_print(f"   - Try deleting the '{SELENIUM_PROFILE_DIR}' folder and running again.")
        safe_print("   - Ensure Chrome is fully closed (check Task Manager).")
        exit()

def human_like_scroll(driver, max_scrolls=5):
    """Simulates more human-like scrolling behavior."""
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        scrolls = random.randint(2, max_scrolls)
        for i in range(scrolls):
            fraction = (i + 1) / scrolls
            target = int(last_height * fraction * random.uniform(0.75, 1.1))
            driver.execute_script(f"window.scrollTo(0, {target});")
            random_human_pause(short=True)
            try:
                ActionChains(driver).move_by_offset(random.randint(-25,25), random.randint(-25,25)).perform()
                ActionChains(driver).move_by_offset(0,0).perform()
            except: pass
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        random_human_pause(short=True)
        driver.execute_script("window.scrollTo(0, 0);")
        random_human_pause(short=True)
    except Exception as e:
        safe_print(f"   - Scroll error: {e}")

# -------------------- AI / GEMINI PROMPTS 🧠 --------------------

def call_gemini_with_retry(model_name: str, prompt: any, is_vision=False):
    """Handles API calls with basic retry logic."""
    model = genai.GenerativeModel(model_name)
    for attempt in range(3):
        try:
            if is_vision:
                response = model.generate_content(prompt)
            else:
                response = model.generate_content(prompt)
            if response and response.text:
                return response.text
            else:
                safe_print(f"   - AI Call Warning (Attempt {attempt+1}): Empty response received.")
        except Exception as e:
            safe_print(f"   - AI Call Error (Attempt {attempt+1}): {e}")
            error_text = str(e).lower()
            if "quota" in error_text or "limit" in error_text or "429" in error_text:
                wait = 25 * (attempt + 1)
                safe_print(f"   - Rate limit likely hit, waiting {wait}s...")
                time.sleep(wait)
            elif "503" in error_text or "server error" in error_text:
                 wait = 10 * (attempt + 1)
                 safe_print(f"   - AI Server Error, waiting {wait}s...")
                 time.sleep(wait)
            else:
                time.sleep(3 * (attempt + 1))
    safe_print(f"   - AI call failed after multiple retries for model {model_name}.")
    return None

def call_gemini_critic_brain(driver, entity_name: str, website_url: str):
    """✅ "Critic Brain": Visits a site, takes a screenshot, and grades it."""
    safe_print(f"   🤖 Critic Brain: Analyzing website {website_url}...")
    try:
        driver.get(website_url)
        human_like_scroll(driver, max_scrolls=2)
        random_human_pause()
        
        screenshot_bytes = driver.get_screenshot_as_png()
        image_part = {"mime_type": "image/png", "data": screenshot_bytes}

        prompt_text = f"""
You are a world-class web design and UX critic. Analyze the provided screenshot of the homepage for "{entity_name}".

Your goal is to rate this website on a scale of 1-10 based on these 10 factors:
1. Modern Design (Looks current vs. dated)
2. Mobile Friendliness (Readable layout)
3. Visual Appeal (Professional colors, fonts)
4. Clarity (UX/UI) (Easy to understand)
5. Trust Signals (Clear logos, professional feel)
6. Load Speed (Perceived) (Looks lightweight vs. heavy)
7. Clear Call to Action
8. Branding (Logo used well)
9. Readability
10. Overall "Premium" Feel (vs. a top-tier site like bcci.tv)

Based on your 1-10 rating, assign a final tier:
- **Rating 6/10 or LOWER:** The website is poor. Assign "P3".
- **Rating 7/10 or HIGHER:** The website is good/professional. Assign "P4".

Return only a JSON object with your decision:
{{"tier": "P3"}} or {{"tier": "P4"}}
"""
        model = genai.GenerativeModel("gemini-2.5-flash") # Using user's requested model
        response_text = call_gemini_with_retry(model.model_name, [prompt_text, image_part], is_vision=True)
        parsed = safe_parse_json_from_text(response_text)
        
        if parsed and "tier" in parsed:
            return parsed["tier"]
        else:
            safe_print("   - Critic Brain failed to return valid JSON. Defaulting to P4 (Good Website).")
            return "P4" # Default to "Good" if analysis fails
            
    except Exception as e:
        safe_print(f"   - Critic Brain error visiting site: {e}")
        return "P4" # Default to "Good" if site visit fails

def call_gemini_to_verify_and_get_followers(driver, entity_name, entity_type, candidate_url):
    """✅ NEW: Visits a social link, verifies its bio, and finds follower count."""
    safe_print(f"    - 🔬 Verifying social link: {candidate_url}")
    try:
        driver.get(candidate_url)
        random_human_pause(short=True)
        page_text = driver.find_element(By.TAG_NAME, 'body').text

        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
I am looking for the official social media page for a sports entity:
- **Entity Name:** "{entity_name}"
- **Entity Type:** "{entity_type}"

I have visited a candidate URL ({candidate_url}) and scraped its text. Please analyze the text and extract two things:
1.  **is_match:** Does this bio/page text (e.g., "Official account of Gir Lions Cricket") confirm this is the **correct social media page for the sports entity "{entity_name}"**? (true/false)
2.  **follower_count:** Find the follower/subscriber count. (Return as a number, e.g., 5000 or 1200). If not found, return 0.

**Page Text:**
---
{page_text[:4000]}
---

Return only a JSON object:
{{"is_match": true, "follower_count": 5000}}
or
{{"is_match": false, "follower_count": 0}}
"""
        response_text = call_gemini_with_retry(model.model_name, prompt)
        parsed = safe_parse_json_from_text(response_text)
        
        if parsed:
            is_match = parsed.get("is_match", False)
            follower_count = parsed.get("follower_count", 0)
            
            # Ensure follower_count is a number
            try:
                follower_count = int(re.sub(r'[^\d]', '', str(follower_count)))
            except:
                follower_count = 0
                
            return is_match, follower_count
        else:
            safe_print(f"    - ❌ Verification FAILED: AI did not return valid JSON.")
            return False, 0
    except Exception as e:
        safe_print(f"    - Error during social link verification: {e}")
        return False, 0

# -------------------- MAIN WORKFLOW (TRIAGE & SORT) --------------------
def pre_flight_check(driver):
     """Pauses the script indefinitely for a one-time manual CAPTCHA solve."""
     try:
         driver.get("https://www.google.com")
         safe_print("\n" + "="*60)
         safe_print(">>> 🛑 PRE-FLIGHT CHECK: ACTION REQUIRED 🛑 <<<")
         safe_print("The browser is now open in its DEDICATED profile.")
         safe_print("Please complete the following:")
         safe_print("  1. Solve any CAPTCHA that appears.")
         safe_print("  2. IMPORTANT: Log into your PERSONAL @gmail.com account IN THIS BROWSER.")
         safe_print("  3. Wait for the main Google search page to load.")
         input(">>> Once ready, press Enter here to begin the mission...")
         safe_print("="*60 + "\n")
         safe_print("✅ Pre-flight check complete. Agent is now running autonomously.")
     except Exception as e:
         safe_print(f"Error during pre-flight check: {e}")
         time.sleep(2)

def main():
    driver = None # Initialize driver to None
    try:
        print("\n--- STARTING TRIAGE & SORTING AGENT ---")
        
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
        
        # --- Initialize driver. We need it for all cases now. ---
        driver = make_driver()
        pre_flight_check(driver)

        for i, row in enumerate(entities_to_triage):
            # Headers: ["Entity Name", "Type", "Official Website", "phone", "Contacts", "Socials", "Address", "Source URL", "Notes"]
            if len(row) < 8: # Ensure row has at least 8 columns (up to Source URL)
                safe_print(f"  Skipping malformed row: {row}")
                continue

            entity_name = row[0]
            entity_type = row[1]
            website_url = row[2]
            socials_str = row[5] # Column F
            
            safe_print(f"\n  Processing ({i+1}/{len(entities_to_triage)}): {entity_name}")
            
            # --- APPLYING YOUR NEW TRIAGE LOGIC ---
            
            if website_url == "NA":
                if socials_str != "NA":
                    # --- Case 1: "No Website" - Verify socials and check followers ---
                    social_link_to_check = socials_str.split(',')[0].strip() # Get first social link
                    
                    is_match, follower_count = call_gemini_to_verify_and_get_followers(driver, entity_name, entity_type, social_link_to_check)
                    
                    if not is_match:
                        safe_print(f"    - Decision: P5 (Reject - Social link was incorrect/unverified)")
                        rows_to_save["P5"].append(row)
                    elif follower_count < 30000:
                        safe_print(f"    - Decision: P1 (HOT Lead - {follower_count} followers)")
                        rows_to_save["P1"].append(row)
                    else:
                        safe_print(f"    - Decision: P2 (Web Lead - {follower_count} followers)")
                        rows_to_save["P2"].append(row)
                else:
                    # --- Case 3: "No Presence" ---
                    safe_print(f"    - Decision: P5 (Reject - No Presence)")
                    rows_to_save["P5"].append(row)
            else:
                # --- Case 2: "Has Website" - Run the Critic Bot ---
                assigned_tier = call_gemini_critic_brain(driver, entity_name, website_url)
                
                if assigned_tier == "P2":
                    safe_print(f"    - Decision: P3 (Redesign Lead - Site rated poorly)")
                    rows_to_save["P3"].append(row) # P3 in your logic
                else: # P4
                    safe_print(f"    - Decision: P4 (Good Website - Low Priority)")
                    rows_to_save["P4"].append(row) # P4 in your logic

            random_human_pause(short=True)
            
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
        if driver:
            safe_print("Closing driver...")
            try:
                driver.quit()
            except: pass
        safe_print("Done.")

if __name__ == "__main__":
    main()