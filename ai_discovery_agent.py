# discovery_agent.py (L2 Filter Only, No Players, Expanded Delhi Missions)
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
RAW_ENTITY_SHEET_NAME = "Extracted Raw Entities" # <-- This is its only output

# --- Use an isolated profile directory ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SELENIUM_PROFILE_DIR = os.path.join(PROJECT_DIR, "SeleniumProfile")

# --- Agent Behavior Config ---
KEYWORDS_PER_MISSION = 5
MAX_SOURCE_URLS_PER_KEYWORD = 3
MAX_ENTITIES_PER_SOURCE = 15
SCROLL_PAUSES = (0.8, 1.8)
LONG_PAUSE = (2.5, 5.5)

# --- ✅ EXPANDED Stage 1 Discovery Missions (Delhi Focus) ---
DISCOVERY_MISSIONS = [
    # --- Delhi NCR Missions ---
    "Find directories of football clubs in Delhi NCR",
    "Find member clubs of the Delhi Soccer Association (DSA)",
    "Find participating teams in the Delhi Premier League (football)",
    "Find lists of cricket academies affiliated with the DDCA (Delhi & District Cricket Association)",
    "Find rosters or player lists for state-level basketball teams in Delhi",
    "Find member academies of the Delhi Basketball Association",
    "Find amateur and corporate cricket leagues in Delhi NCR",
    "List of hockey clubs and academies in Delhi",
    "Find directories of sports complexes and venues in New Delhi",
    "Find lists of badminton academies in Noida and Gurgaon",

    # --- Gujarat Missions ---
    "Find lists of cricket teams playing in Gujarat leagues",
    "Find lists of sports venues in Ahmedabad",
    "Find affiliated clubs with the Gujarat State Football Association (GSFA)",
    "Find cricket academies in Vadodara and Surat",
    "Find lists of Khel Mahakumbh sports venues in Gujarat"
]


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

    try:
        raw_entity_sheet = sh.worksheet(RAW_ENTITY_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        raw_entity_sheet = sh.add_worksheet(title=RAW_ENTITY_SHEET_NAME, rows="5000", cols="5")
    
    if raw_entity_sheet.row_count == 0 or raw_entity_sheet.cell(1, 1).value == '':
        raw_entity_sheet.append_row(["Entity Name", "Type", "Source URL"])
    safe_print(f"✅ Google Sheets connection successful. Ready to write to '{RAW_ENTITY_SHEET_NAME}'.")

except Exception as e:
    safe_print(f"❌ FATAL ERROR: GOOGLE SHEETS SETUP FAILED: {e}")
    exit()

# -------------------- UTILITIES --------------------
def safe_parse_json_from_text(text: str):
    if not text: return None
    try: return json.loads(text)
    except: pass
    patterns = [r'```json\s*(\{.*?\})\s*```', r'(\{.*?\})', r'```json\s*(\[.*?\])\s*```', r'(\[.*?\])']
    for p in patterns:
        m = re.search(p, text, re.DOTALL)
        if m:
            blob = m.group(1)
            try: return json.loads(blob)
            except Exception: continue
    safe_print("   - Warning: Could not parse JSON from AI response.")
    return None

def is_blacklisted(url: str):
    try:
        domain = urlparse(url).netloc.lower()
        return domain and any(b in domain for b in BLACKLIST_DOMAINS)
    except: return True

def random_human_pause(short=False):
    if short: time.sleep(random.uniform(*SCROLL_PAUSES))
    else: time.sleep(random.uniform(*LONG_PAUSE))

# -------------------- SELENIUM HELPERS --------------------
def make_driver():
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
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(random.randint(2, max_scrolls)):
            driver.execute_script(f"window.scrollBy(0, {random.randint(300, 700)});")
            random_human_pause(short=True)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        random_human_pause(short=True)
        driver.execute_script("window.scrollTo(0, 0);")
        random_human_pause(short=True)
    except Exception as e:
        safe_print(f"   - Scroll error: {e}")

# -------------------- AI / GEMINI PROMPTS 🧠 --------------------

def call_gemini_with_retry(model_name: str, prompt: any, is_vision=False):
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
            else:
                time.sleep(3 * (attempt + 1))
    safe_print(f"   - AI call failed after multiple retries for model {model_name}.")
    return None

def call_gemini_for_discovery_keywords(mission_objective: str):
    """Strategist Brain: Generates keywords to find LISTS/DIRECTORIES."""
    safe_print(f"  🧠 Strategist Brain: Generating discovery keywords for '{mission_objective}'")
    prompt = f"""
    You are an expert market researcher and SEO strategist.
    Your task is to brainstorm a list of {KEYWORDS_PER_MISSION} highly effective and diverse Google search queries to accomplish a specific mission.
    Mission Objective: "{mission_objective}"
    CRITICAL INSTRUCTIONS:
    1.  Think Like a User: Generate queries that a real person looking for these services would type.
    2.  Be Creative and Diverse: Provide a varied set of keywords. Do not just use simple variations.
    3.  Use Actionable Terms: Focus on local and specific terms like 'tournament', 'club', 'academy', 'championship', 'trials', and city/neighborhood names.
    4.  Avoid Jargon: Do not use corporate or abstract terms like 'non-IPL' or 'low-tier'.
    5.  Format Output: The output must be a JSON object with a single key "keywords" which is a list of strings.
    """
    response_text = call_gemini_with_retry("gemini-2.5-flash", prompt)
    parsed = safe_parse_json_from_text(response_text)
    keywords = parsed.get("keywords", []) if parsed else []
    safe_print(f"  💡 AI has generated {len(keywords)} strategic keywords.")
    return keywords

def call_gemini_to_extract_entities_from_page(page_text: str):
    """
    ✅ UPGRADED Analyst Brain:
    Filters for ONLY Level 2 and IGNORES players.
    """
    safe_print("   🤖 Analyst Brain (L2 Filter Only, No Players) activated...")
    
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    prompt = f"""
    You are an expert sports business analyst. Your goal is to analyze the following webpage text and extract specific sports organizations that are **Level 2 ONLY**.

    **Your Target Entities:**
    - Leagues
    - Teams
    - Events
    - Venues
    - Federations
    - Academies

    **Performance Levels & Rules:**
    - **Level 1 (REJECT):** Major national/international teams (e.g., 'Indian Cricket Team', 'Mumbai Indians') and major professional leagues (e.g., 'IPL', 'ISL').
    - **Level 2 (KEEP):** State-level leagues, state associations, and major state/city teams (e.g., 'Gujarat State Football League', 'Delhi Cricket Association'). These are established organizations.
    - **Level 3 (REJECT):** District-level leagues, prominent city clubs, and local academies/events.
    - **Level 4 (REJECT):** Hyper-local, "gully" teams, or school teams.
    - **PLAYERS (REJECT):** You MUST ignore all individual player names.

    **CRITICAL WORKFLOW:**
    1.  **Translate First:** If you find text in another language (like Hindi or Gujarati), first translate it to English.
    2.  **Analyze and Filter:** Analyze the translated English text.
    3.  **Extract ONLY Level 2:** Your primary job is to **IGNORE** Level 1, Level 3, Level 4, and all **Players**. Only extract entities that match your Target Entities AND are **Level 2**.
    4.  **IGNORE Garbage:** Also ignore all irrelevant text (tenders, circulars, news, navigation links).
    5.  **Format Output:** Return a strict JSON object with a list of the qualified **Level 2** entities you found.

    Example Response:
    {{"entities": [
        {{"name": "Delhi Premier League", "type": "League"}},
        {{"name": "Ahmedabad District Football Association", "type": "Federation"}}
    ]}}

    **Webpage Text to Analyze:**
    ---
    {page_text[:10000]} 
    ---
    """
    response_text = call_gemini_with_retry("gemini-2.5-flash", prompt)
    parsed = safe_parse_json_from_text(response_text)
    entities = parsed.get("entities", []) if parsed else []
    
    if entities:
        safe_print(f"    - AI Analyst found {len(entities)} QUALIFIED (L2) entities.")
    else:
        safe_print("    - AI Analyst found no qualified (L2) entities on this page.")
    return entities


# -------------------- MAIN WORKFLOW (DISCOVERY ONLY) --------------------
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

        # --- STAGE 1: ENTITY DISCOVERY ---
        print("\n--- STARTING STAGE 1: ENTITY DISCOVERY ---")
        
        existing_raw_entities = set()
        try:
            all_raw_data = raw_entity_sheet.get_all_values()[1:]
            for row in all_raw_data:
                if row: existing_raw_entities.add((row[0].lower(), row[1].lower()))
        except Exception as e:
            safe_print(f" - Warning: Could not get existing raw entities: {e}")

        safe_print(f" - Found {len(existing_raw_entities)} existing raw entities in the sheet.")
        
        new_entities_found_in_session = 0

        for mission in DISCOVERY_MISSIONS:
            keywords = call_gemini_for_discovery_keywords(mission)
            if not keywords: continue

            keywords = list(dict.fromkeys([k.strip() for k in keywords if k.strip()]))[:KEYWORDS_PER_MISSION]
            safe_print(f"Mission: {mission} -> {len(keywords)} keywords")

            for kw in keywords:
                safe_print("\nSearching for lists/directories using keyword:", kw)
                google_search_url = f"https://www.google.com/search?q={kw.replace(' ', '+')}"
                try:
                    driver.get(google_search_url)
                    human_like_scroll(driver, max_scrolls=3)
                    random_human_pause(short=True)

                    source_urls_to_process = []
                    safe_print("   - Attempting to find result links...")
                    try:
                        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "search")))
                        h3_elements = driver.find_elements(By.CSS_SELECTOR, "div#search a h3")
                        safe_print(f"   - Found {len(h3_elements)} potential link headings (h3 tags).")
                        
                        for h3 in h3_elements:
                            try:
                                link_element = h3.find_element(By.XPATH, "./..")
                                href = link_element.get_attribute("href")
                                if href and href.startswith("http") and not is_blacklisted(href):
                                    if href not in source_urls_to_process:
                                        source_urls_to_process.append(href)
                            except Exception: continue
                    except Exception as wait_err:
                         safe_print(f"    - Error waiting for or finding search results: {wait_err}")

                    safe_print(f" - Found {len(source_urls_to_process)} potential source pages to process.")
                    if not source_urls_to_process:
                        safe_print("   - No valid source URLs found for this keyword. Moving to next keyword.")
                        random_human_pause()
                        continue

                    for source_url in source_urls_to_process[:MAX_SOURCE_URLS_PER_KEYWORD]:
                        safe_print(f"   - Processing source page: {source_url}")
                        try:
                            driver.get(source_url)
                            random_human_pause()
                            page_text = driver.find_element(By.TAG_NAME, 'body').text
                        except Exception as page_load_err:
                             safe_print(f"     - Could not load page or extract text: {page_load_err}")
                             continue

                        entities_found = call_gemini_to_extract_entities_from_page(page_text)
                        
                        entities_to_save_to_sheet = []
                        for entity in entities_found[:MAX_ENTITIES_PER_SOURCE]:
                            name = entity.get("name")
                            etype = entity.get("type")
                            if name and etype:
                                if (name.lower(), etype.lower()) not in existing_raw_entities:
                                    entities_to_save_to_sheet.append([name, etype, source_url])
                                    existing_raw_entities.add((name.lower(), etype.lower()))
                                    new_entities_found_in_session += 1
                        
                        if entities_to_save_to_sheet:
                            try:
                                raw_entity_sheet.append_rows(entities_to_save_to_sheet, value_input_option='USER_ENTERED')
                                safe_print(f"    - Saved {len(entities_to_save_to_sheet)} new raw entities to sheet.")
                            except Exception as sheet_err:
                                safe_print(f"    - Error saving raw entities to sheet: {sheet_err}")
                        random_human_pause(short=True)
                except Exception as e:
                    safe_print(f"Search page processing error for keyword: {kw} - {e}")
                random_human_pause()

        safe_print(f"\n--- STAGE 1 (DISCOVERY) COMPLETE ---")
        safe_print(f"--- Found {new_entities_found_in_session} new raw entities in this session. ---")

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