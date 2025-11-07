# enrichment_agent.py (Text-Only, Multi-Pass)
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
RAW_ENTITY_SHEET_NAME = "Extracted Raw Entities" # <-- INPUT
FINAL_OUTPUT_SHEET_NAME = "Discovered Entities" # <-- OUTPUT

# --- Use an isolated profile directory ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SELENIUM_PROFILE_DIR = os.path.join(PROJECT_DIR, "SeleniumProfile") 

SCROLL_PAUSES = (0.8, 1.8)
LONG_PAUSE = (2.5, 5.5)

BLACKLIST_DOMAINS = [
    "google.com", "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
    "youtube.com", "wikipedia.org", "medium.com", "quora.com", "blogspot.com",
    "justdial.com", "indiamart.com", "zaubacorp.com", "sulekha.com",
    "amazon.", "flipkart."
]

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

    raw_entity_sheet = sh.worksheet(RAW_ENTITY_SHEET_NAME)
    output_sheet = sh.worksheet(FINAL_OUTPUT_SHEET_NAME)

    headers = ["Entity Name", "Type", "Official Website", "phone", "Contacts", "Socials", "Address", "Source URL", "Notes"]
    if output_sheet.row_count == 0 or output_sheet.cell(1, 1).value == '':
        output_sheet.update('A1', [headers])

    # Load already discovered websites AND names for robust duplicate checking
    output_values = output_sheet.get_all_values()[1:]
    saved_websites = {row[2].strip().lower() for row in output_values if len(row) > 2 and row[2] and row[2] != 'NA'}
    saved_names = {row[0].strip().lower() for row in output_values if len(row) > 0 and row[0]}
    safe_print(f"✅ Google Sheets connected. Found {len(saved_names)} already enriched entities.")
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

def is_blacklisted(url: str):
    """Checks if a URL belongs to a blacklisted domain."""
    try:
        domain = urlparse(url).netloc.lower()
        return domain and any(b in domain for b in BLACKLIST_DOMAINS)
    except: return True

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

def human_like_scroll(driver, max_scrolls=3):
    """Simulates more human-like scrolling behavior."""
    try:
        for _ in range(random.randint(1, max_scrolls)):
            driver.execute_script(f"window.scrollBy(0, {random.randint(200, 500)});")
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
            if is_vision: # This is kept for flexibility, though we aren't using it
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
            else:
                time.sleep(3 * (attempt + 1))
    safe_print(f"   - AI call failed after multiple retries for model {model_name}.")
    return None

def call_gemini_for_website_keyword(entity_name: str, entity_type: str, data_to_find="official website"):
    """Creates a smarter, more specific search query for website or missing data."""
    safe_print(f"   🧠 Generating search keyword for: {entity_name} ({data_to_find})")
    prompt = f"""
Generate the single best Google search query to find the **{data_to_find}** for the following sports entity:
Name: "{entity_name}"
Type: "{entity_type}"

Instructions:
- Use the entity name and type to create a specific query.
- Example for 'official website': 'Gir Lions cricket team official website'
- Example for 'socials': 'Gir Lions team instagram'
- Example for 'phone': 'Bhavnagar Blasters contact mobile number'

Return only the single, best search query as a plain string.
"""
    response_text = call_gemini_with_retry("gemini-2.5-flash", prompt)
    if response_text:
        return response_text.strip().replace('"', '')
    else:
        return f'"{entity_name}" {entity_type} {data_to_find}' # Fallback

def call_gemini_to_censor_links(entity_name: str, entity_type: str, candidates: list):
    """The "AI Censor" - picks the best link from a list of Google results."""
    safe_print(f"   🤖 AI Censor: Analyzing {len(candidates)} potential links for '{entity_name}'...")
    candidate_list_str = ""
    for i, candidate in enumerate(candidates):
        candidate_list_str += f"  {i+1}. Title: \"{candidate['title']}\", URL: \"{candidate['url']}\"\n"
    prompt = f"""
You are an expert web detective. I am looking for the official website for:
- **Entity Name:** "{entity_name}"
- **Entity Type:** "{entity_type}"

Analyze the following Google search results. Pick the ONE URL that is the true, official homepage for this **specific entity**.

**Candidates:**
{candidate_list_str}

**CRITICAL RULES:**
1.  **BE SPECIFIC:** Reject parent league sites (like gujaratcricketleague.com) if looking for a specific team (like "Bhavnagar Blasters").
2.  **REJECT GENERIC SITES:** Do not pick Wikipedia, Facebook, JustDial, or news articles.
3.  **CHECK FOR NAME MATCH:** The domain name (e.g., 'girlions.com') should ideally match the entity name ('Gir Lions').
4.  **NA IS ACCEPTABLE:** If no link is the specific official homepage, you MUST return "NA".

Return JSON: {{"best_url": "https://the-chosen-url.com"}} or {{"best_url": "NA"}}
"""
    response_text = call_gemini_with_retry("gemini-2.5-flash", prompt)
    parsed = safe_parse_json_from_text(response_text)
    if parsed and "best_url" in parsed:
        return parsed["best_url"]
    else:
        safe_print("   - AI Censor failed to return valid JSON. Defaulting to NA.")
        return "NA"

def call_gemini_to_enrich_website_text(page_text: str, entity_name: str):
    """✅ UPGRADED: AI Brain to extract all contact info from a page's TEXT."""
    safe_print("    - 🤖 Analyst Brain (Text Enrichment) activated...")
    
    prompt = f"""
You are an expert data extractor. Analyze the following webpage text for an entity named "{entity_name}".
Your goal is to extract the primary contact information.

**Webpage Text:**
---
{page_text[:12000]}
---

**Instructions:**
1.  **Find Phone (Mobile):** Find all phone numbers. **Prioritize and return only mobile numbers** (10 digits starting with 9, 8, 7, or 6 in India). If no mobile numbers are found, return "NA".
2.  **Find Contacts (Emails):** Find all contact email addresses (e.g., info@, contact@, media@).
3.  **Find Socials:** Find all full social media URLs (Facebook, Instagram, Twitter, LinkedIn) from the text.
4.  **Find Address:** Find the main physical address or headquarters location.
5.  **Format Output:** Return strict JSON with the found data. Use "NA" or [] if not found.

Example Response:
{{
  "phone": "+91 98765 43210",
  "contacts": ["contact@team.com", "media@team.com"],
  "socials": ["https://www.instagram.com/team_name"],
  "address": "123 Stadium Road, Ahmedabad, Gujarat"
}}
"""
    try:
        response_text = call_gemini_with_retry("gemini-2.5-flash", prompt)
        return safe_parse_json_from_text(response_text)
    except Exception as e:
        safe_print(f"     - Enrichment Brain Error: {e}")
        return None

def find_missing_data_via_google(driver, entity_name, entity_type, data_to_find: str):
    """✅ UPGRADED: Finds and *verifies* missing data from Google search snippets."""
    search_keyword = call_gemini_for_website_keyword(entity_name, f"{entity_type} {data_to_find}")
    if not search_keyword: return []

    safe_print(f"   🕵️‍♂️ (Fallback) Searching for '{data_to_find}' using query: '{search_keyword}'")
    try:
        driver.get(f"https://www.google.com/search?q={search_keyword.replace(' ', '+')}")
        random_human_pause()
        
        # Get all text from the search results page (snippets)
        page_text = driver.find_element(By.TAG_NAME, 'body').text
        if not page_text:
            return []
        
        # --- NEW AI Call: Extract data from snippets ---
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
I searched Google for "{search_keyword}". Below are the search result snippets from the page.
My goal is to find the **{data_to_find}** for "{entity_name}".

Scan the snippets. Find all candidate URLs and their text.
Analyze the snippets and **verify** if they are the official link for the **"{entity_name}" sports entity**.
For example, for "Gir Lions", reject links for "Gir Forest".

Return strict JSON of **VERIFIED** data: {{"found_data": ["https://verified-link.com", "+911234567890", ...]}} or {{"found_data": []}}

**Search Snippets:**
---
{page_text[:8000]}
---
"""
        response_text = call_gemini_with_retry(model.model_name, prompt)
        parsed = safe_parse_json_from_text(response_text)
        return parsed.get("found_data", []) if parsed else []

    except Exception as e:
        safe_print(f"   - Error during targeted Google search: {e}")
        return []

# -------------------- CORE AGENT LOGIC 🤖 --------------------

def find_official_website_via_search(driver, entity_name: str, entity_type: str):
    """Uses AI to find AND CENSOR search results."""
    search_keyword = call_gemini_for_website_keyword(entity_name, entity_type, "official website")
    if not search_keyword: return "NA"

    safe_print(f"   🕵️‍♂️ Searching Google for website using query: '{search_keyword}'")
    try:
        driver.get(f"https://www.google.com/search?q={search_keyword.replace(' ', '+')}")
        random_human_pause()

        candidates = []
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "search")))
            h3_elements = driver.find_elements(By.CSS_SELECTOR, "div#search a h3")
            
            for h3 in h3_elements[:5]: # Analyze top 5
                try:
                    title = h3.text
                    link_element = h3.find_element(By.XPATH, "./ancestor::a")
                    url = link_element.get_attribute("href")
                    if url and url.startswith("http") and not is_blacklisted(url):
                        candidates.append({"title": title, "url": url})
                except Exception: continue
        except Exception as wait_err:
             safe_print(f"    - Error finding search results: {wait_err}")
             return "NA"

        if not candidates:
            safe_print("   - No suitable website links found in search results.")
            return "NA"

        best_url = call_gemini_to_censor_links(entity_name, entity_type, candidates)
        
        if best_url != "NA":
            safe_print(f"   🎯 AI Censor selected official site: {best_url}")
            return best_url
        else:
            safe_print("   - AI Censor determined no link was official.")
            return "NA"
            
    except Exception as e:
        safe_print(f"   - Error during website search: {e}")
        return "NA"

def enrich_and_save_entity(driver, entity_name, entity_type, source_url):
    """✅ UPGRADED: Finds contacts/socials via text, with Google search as fallback."""
    safe_print(f"\n  Enriching Entity: '{entity_name}' (Type: {entity_type})")

    official_website = find_official_website_via_search(driver, entity_name, entity_type)

    cleaned_website = "NA"
    if official_website != "NA":
        try:
            parsed = urlparse(official_website)
            cleaned_website = f"{parsed.scheme}://{parsed.netloc}".lower() if parsed.scheme and parsed.netloc else "NA"
        except: 
            pass

    if cleaned_website != "NA" and cleaned_website in saved_websites:
        safe_print(f"    - Website already enriched/saved ({cleaned_website}). Skipping.")
        return

    # --- Initialize all data points ---
    enriched_data = {
        "phone": "NA",
        "contacts": [],
        "socials": [],
        "address": "NA",
        "notes": ""
    }

    # --- Pass 1: Scrape the official website ---
    if official_website != "NA":
        try:
            safe_print(f"    - Pass 1: Visiting official site for text analysis: {official_website}")
            driver.get(official_website)
            human_like_scroll(driver, max_scrolls=3)
            random_human_pause()
            
            page_text = driver.find_element(By.TAG_NAME, 'body').text
            extracted_info = call_gemini_to_enrich_website(page_text, entity_name)
            
            if extracted_info:
                enriched_data["phone"] = extracted_info.get("phone", "NA")
                enriched_data["contacts"] = extracted_info.get("contacts", [])
                enriched_data["socials"] = extracted_info.get("socials", [])
                enriched_data["address"] = extracted_info.get("address", "NA")
                safe_print(f"    - Pass 1 Results: Phone: {enriched_data['phone']}, Socials: {len(enriched_data['socials'])}")
                
        except Exception as e:
            safe_print(f"    - Could not visit official site or extract info: {e}")
            enriched_data["notes"] = "Site visit failed. "
    else:
        enriched_data["notes"] = "No official website found. "

    # --- Pass 2: Fallback Google Search for MISSING data ---
    if not enriched_data["socials"]:
        safe_print("    - Pass 2: No socials found. Starting targeted Google search...")
        social_links = find_missing_data_via_google(driver, entity_name, entity_type, "socials")
        if social_links:
            safe_print(f"    - Pass 2 Found Socials: {social_links}")
            enriched_data["socials"] = social_links

    if enriched_data["phone"] == "NA":
        safe_print("    - Pass 2: No mobile phone found. Starting targeted Google search...")
        phone_numbers = find_missing_data_via_google(driver, entity_name, entity_type, "phone")
        if phone_numbers:
            safe_print(f"    - Pass 2 Found Phone: {phone_numbers[0]}")
            enriched_data["phone"] = phone_numbers[0]
            
    # Add other fallback searches here if needed (e.g., for "contacts")

    # --- Save the final combined data ---
    socials_str = ", ".join(enriched_data["socials"]) if enriched_data["socials"] else "NA"
    contacts_str = ", ".join(enriched_data["contacts"]) if enriched_data["contacts"] else "NA"
    address_str = enriched_data["address"]
    phone_str = enriched_data["phone"]
    
    row = [entity_name, entity_type, official_website, phone_str, contacts_str, socials_str, address_str, source_url, enriched_data["notes"]]
    try:
        output_sheet.append_row(row)
        if cleaned_website != "NA":
            saved_websites.add(cleaned_website)
        safe_print(f"  ✅ Enriched and Saved: {entity_name} | {official_website}")
    except Exception as e:
        safe_print(f"  - Error saving enriched data to sheet: {e}")

# -------------------- MAIN WORKFLOW (ENRICHMENT ONLY) --------------------
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
    driver = make_driver()
    try:
        pre_flight_check(driver)

        # --- STAGE 2: ENTITY ENRICHMENT ---
        print("\n\n--- STARTING STAGE 2: ENTITY ENRICHMENT ---")
        
        raw_data_rows = raw_entity_sheet.get_all_values()[1:]
        print(f"  Found {len(raw_data_rows)} total raw entities to process.")

        # --- ✅ Resumability Logic ---
        unique_entities_map = {}
        for row in raw_data_rows:
            if row and len(row) >= 3:
                name = row[0].strip().lower()
                if name not in unique_entities_map: # Keep the first one found
                    unique_entities_map[name] = {"name": row[0], "type": row[1], "source_url": row[2]}
        
        entities_to_enrich = []
        for name_lower, entity_data in unique_entities_map.items():
            if name_lower not in saved_names:
                entities_to_enrich.append(entity_data)
        
        print(f"  Found {len(entities_to_enrich)} new unique entities to enrich.")
        
        for entity_data in entities_to_enrich:
             enrich_and_save_entity(
                 driver,
                 entity_data["name"],
                 entity_data["type"],
                 entity_data["source_url"]
             )
             saved_names.add(entity_data["name"].lower()) # Add to set to prevent re-processing in this session
             random_human_pause()

    except KeyboardInterrupt:
        safe_print("Interrupted by user — exiting.")
    except Exception as e:
        safe_print(f"MAIN ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        safe_print("Closing driver...")
        try:
            if 'driver' in locals() and driver:
                driver.quit()
        except: pass
        safe_print("Done.")

if __name__ == "__main__":
    main()