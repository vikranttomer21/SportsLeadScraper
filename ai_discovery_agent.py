import os
import gspread
import google.generativeai as genai
import time
import random
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium_stealth import stealth
from urllib.parse import urlparse

# --- CONFIGURATION ---
# IMPORTANT: It is highly recommended to delete any keys you have posted publicly and generate a new one.
GEMINI_API_KEY = "AIzaSyC7CEWPaUX6XfxSMNax3JELEj6Mqxc1Nio"

GOOGLE_SHEETS_CREDENTIALS = 'service_account.json'
SPREADSHEET_NAME = 'Sports Scraper'
CHROME_PROFILE_PATH = r'C:\Users\USER\AppData\Local\Google\Chrome\User Data\Profile 32'

# --- SHEET CONFIGURATION ---
INPUT_SHEET_NAME = "Master Entity Database"
OUTPUT_SHEET_NAME = "Discovered Entities"

# --- CAMPAIGN 1: PROACTIVE DISCOVERY CONFIG ---
MISSION_OBJECTIVES = [
    "Find low-tier, amateur cricket leagues in Gujarat",
    "Find community-focused, local football clubs in Delhi NCR",
    "Find small, regional sports event organizers in Gujarat",
    "Find non-professional basketball teams in Delhi NCR"
]
KEYWORDS_PER_MISSION = 5
MAX_RESULTS_PER_KEYWORD = 5

BLACKLIST_DOMAINS = [
    "google.com", "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
    "youtube.com", "wikipedia.org", "medium.com", "quora.com", "blogspot.com",
    "justdial.com", "indiamart.com", "zaubacorp.com", "sulekha.com"
]

# --- AUTHENTICATION & SETUP ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    print("✅ Gemini AI Brain configured successfully.")
    
    gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)
    sh = gc.open(SPREADSHEET_NAME)
    
    input_sheet = sh.worksheet(INPUT_SHEET_NAME)
    output_sheet = sh.worksheet(OUTPUT_SHEET_NAME)
    
    if output_sheet.row_count == 0 or output_sheet.cell(1, 1).value == '':
        output_sheet.append_row(["Entity Name", "Category", "Official Website", "Socials", "Source URL"])
        
    saved_websites = {row[2].strip() for row in output_sheet.get_all_values()[1:] if len(row) > 2 and row[2]}
    print(f"✅ Google Sheets connection successful. Found {len(saved_websites)} previously discovered entities.")

except Exception as e:
    print(f"❌ FATAL ERROR DURING SETUP: {e}")
    exit()

# --- THE AGENT'S "BRAIN" FUNCTIONS 🧠 ---

def generate_search_keywords_with_ai(mission_objective: str):
    """Strategist Brain: Generates search keywords for proactive discovery."""
    print(f"\n  🧠 Strategist Brain activated for mission: '{mission_objective}'")
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
    You are a creative marketing strategist. Your task is to generate {KEYWORDS_PER_MISSION} effective Google search queries for the following mission. The queries must be natural and avoid jargon.

    Mission Objective: "{mission_objective}"

    Return JSON: {{"keywords": ["query1", "query2", ...]}}
    """
    try:
        response = model.generate_content(prompt)
        cleaned_response = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(cleaned_response).get("keywords", [])
    except Exception as e:
        print(f"   - Strategist Brain Error: {e}")
        return []

def extract_entity_names_from_list_page(page_text: str):
    """List Processor Brain with Integrated Translator."""
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
    You are a data extraction bot and an expert multilingual translator. Your primary task is to analyze the provided text from a webpage to find sports entities (Teams, Leagues, Federations).

    **CRITICAL WORKFLOW:**
    1.  **Translate First:** If you find text in another language (like Hindi or Gujarati), first translate it to English.
    2.  **Analyze the Translation:** Analyze the translated English text to find the entity names.
    3.  **Extract English Names Only:** Your final output must only contain the English names of the entities.
    4.  **IGNORE** all irrelevant administrative jargon (tenders, circulars), and navigation links.
    5.  **Format Output:** Return a JSON object with a list of the final English names you found.

    Example Response: {{"entity_names": ["State Sports Complex", "Khel Mahakumbh Center"]}}

    **Webpage Text to Analyze:** --- {page_text[:8000]} ---
    """
    try:
        response = model.generate_content(prompt)
        cleaned_response = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(cleaned_response).get("entity_names", [])
    except Exception as e:
        print(f"   - Phase 1 Brain Error: {e}")
        return []

def find_socials_with_vision(driver):
    """Vision Brain: Uses a screenshot to find social media links."""
    print("    - 📸 Taking screenshot for visual analysis...")
    try:
        screenshot_bytes = driver.get_screenshot_as_png()
        image_part = {"mime_type": "image/png", "data": screenshot_bytes}
        
        prompt_text = """
        Analyze this screenshot of a website. Find the full URLs for the company's official social media profiles (Facebook, Instagram, Twitter, LinkedIn).
        Return a JSON object with a list of the URLs you found.

        Example Response: {{"social_urls": ["https://www.instagram.com/team_name"]}}
        """
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content([prompt_text, image_part])
        cleaned_response = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(cleaned_response).get("social_urls", [])
    except Exception as e:
        print(f"   - Vision Brain Error: {e}")
        return []

# --- THE AGENT'S "BODY" 🤖 ---

def pre_flight_check(driver):
    """Pauses the script at the start for a one-time manual CAPTCHA solve."""
    driver.get("https://www.google.com")
    print("\n" + "="*60)
    print(">>> 🛑 PRE-FLIGHT CHECK: ACTION REQUIRED 🛑 <<<")
    print("The browser is now open. Please complete the following steps:")
    print("  1. Solve any CAPTCHA that appears.")
    print("  2. IMPORTANT: Ensure you are logged into your Google account.")
    print("  3. Wait for the main Google search page to load.")
    input(">>> Once ready, press Enter here to begin the mission...")
    print("="*60 + "\n")
    print("✅ Pre-flight check complete. Agent is now running autonomously.")

def find_official_website(driver, entity_name: str):
    """Uses Google to find the most likely official website for an entity name."""
    print(f"   🕵️‍♂️ Searching for official website for '{entity_name}'...")
    try:
        search_query = f'"{entity_name}" official website'
        driver.get(f"https://www.google.com/search?q={search_query.replace(' ', '+')}")
        time.sleep(random.uniform(4, 6))
        
        links = driver.find_elements(By.CSS_SELECTOR, 'div.g a')
        for link in links:
            url = link.get_attribute('href')
            if url and not any(domain in url for domain in BLACKLIST_DOMAINS):
                if any(word.lower() in urlparse(url).netloc for word in entity_name.split()[:2]):
                    print(f"   🎯 Found likely official site: {url}")
                    return url
        return "NA"
    except Exception as e:
        print(f"   - Error finding official website: {e}")
        return "NA"

def investigate_and_save_entity(driver, entity_name, category, source_url):
    """A reusable 'detective' function that investigates an entity and saves it."""
    print(f"\n  🕵️‍♂️ Investigating: '{entity_name}'")
    
    official_website = find_official_website(driver, entity_name)
    
    if official_website == "NA" or official_website in saved_websites:
        if official_website != "NA": print("    - Website already discovered. Skipping.")
        return
    
    socials = []
    try:
        driver.get(official_website)
        time.sleep(random.uniform(4, 6))
        socials = find_socials_with_vision(driver)
    except Exception as e:
        print(f"    - Could not visit official site or find socials: {e}")
    
    socials_str = ", ".join(socials) if socials else "NA"
    output_sheet.append_row([entity_name, category, official_website, socials_str, source_url])
    saved_websites.add(official_website)
    print(f"  ✅ Saved to 'Discovered Entities': {entity_name} | {official_website} | Socials: {socials_str}")

def main():
    options = webdriver.ChromeOptions()
    options.add_argument(f"user-data-dir={CHROME_PROFILE_PATH}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32",
            webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)

    try:
        # --- ONE-TIME MANUAL AUTHENTICATION STEP ---
        pre_flight_check(driver)

        # --- CAMPAIGN 1: PROACTIVE DISCOVERY ---
        print("\n--- LAUNCHING CAMPAIGN 1: PROACTIVE DISCOVERY ---")
        for mission in MISSION_OBJECTIVES:
            keywords = generate_search_keywords_with_ai(mission)
            for keyword in keywords:
                print(f"\n  Executing proactive search for: '{keyword}'")
                driver.get(f"https://www.google.com/search?q={keyword.replace(' ', '+')}")
                time.sleep(random.uniform(4, 6))
                
                links = driver.find_elements(By.CSS_SELECTOR, 'div.g a')
                urls_to_investigate = []
                for link in links:
                    url = link.get_attribute('href')
                    if url and not any(domain in url for domain in BLACKLIST_DOMAINS):
                        urls_to_investigate.append(url)
                
                for url in list(dict.fromkeys(urls_to_investigate))[:MAX_RESULTS_PER_KEYWORD]:
                     try:
                        driver.get(url)
                        time.sleep(3)
                        entity_name_guess = driver.title.split('-')[0].split('|')[0].strip()
                        if entity_name_guess:
                            investigate_and_save_entity(driver, entity_name_guess, "Unknown", url)
                     except Exception as e:
                         print(f"    - Error investigating proactive URL {url}: {e}")

        # --- CAMPAIGN 2: REACTIVE PROCESSING ---
        print("\n\n--- LAUNCHING CAMPAIGN 2: PROCESSING MASTER DATABASE ---")
        raw_data_rows = input_sheet.get_all_values()[1:]
        print(f"  Found {len(raw_data_rows)} rows to process in '{INPUT_SHEET_NAME}'.")
        
        for i, row in enumerate(raw_data_rows):
            print(f"\n  Processing Master Database Row #{i+1}...")
            if len(row) < 4 or not row[3].strip().startswith('http'):
                print(f"    - Row #{i+1} is malformed or has no valid URL. Skipping.")
                continue
            
            category, source_url = row[1], row[3]
            print(f"    - Source URL: {source_url}")
            
            try:
                driver.get(source_url)
                time.sleep(random.uniform(3, 5))
                page_text = driver.find_element(By.TAG_NAME, 'body').text
                discovered_names = extract_entity_names_from_list_page(page_text)
                print(f"    - Phase 1 Complete: Discovered {len(discovered_names)} potential English entity names.")
                
                for name in discovered_names:
                    investigate_and_save_entity(driver, name, category, source_url)
            except Exception as e:
                print(f"    - Could not process source URL {source_url}: {e}")
                continue

    except Exception as e:
        print(f"A critical error occurred in the main process: {e}")
    finally:
        print("\n--- All missions complete. Closing down. ---")
        driver.quit()

if __name__ == '__main__':
    main()