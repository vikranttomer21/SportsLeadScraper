import configparser
import time
import requests
import random
import os
import logging
import re
from urllib.parse import urljoin, urlparse
from typing import List, Set, Dict, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- CONSTANTS ---
LOG_FILE = "discovery.log"
PROCESSED_ANCHORS_FILE = 'processed_anchors.txt'
SHEET_NAME = "Master Entity Database"
CONFIG_FILE = 'config.ini'
SERVICE_ACCOUNT_FILE = 'service_account.json'

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# --- CONFIGURATION & GOOGLE SHEETS API (Unchanged) ---
def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser(); config.read(CONFIG_FILE); return config

def setup_sheets_service() -> Optional[build]:
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        return build('sheets', 'v4', credentials=creds)
    except Exception as e: logging.error(f"Failed to set up Google Sheets service: {e}"); return None

def ensure_master_sheet_exists(service: build, spreadsheet_id: str) -> bool:
    headers = ["Entity Name", "Category", "Entity Website", "Source URL", "Source Keyword"]
    try:
        sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing_sheets = [s['properties']['title'] for s in sheet_metadata.get('sheets', [])]
        if SHEET_NAME not in existing_sheets:
            logging.info(f"Creating new sheet: '{SHEET_NAME}'")
            body = {'requests': [{'addSheet': {'properties': {'title': SHEET_NAME}}}]}
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A1",
                valueInputOption='USER_ENTERED', body={'values': [headers]}
            ).execute()
    except HttpError as e: logging.error(f"Google Sheets API error: {e}"); return False
    return True

def get_existing_entities(service: build, spreadsheet_id: str) -> Set[str]:
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A2:A"
        ).execute()
        values = result.get('values', []); return {item[0] for item in values if item}
    except HttpError: return set()

def save_entities_to_sheet(service: build, spreadsheet_id: str, entities_data: List[list]) -> None:
    try:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A1",
            valueInputOption='USER_ENTERED', body={'values': entities_data}
        ).execute()
        logging.info(f"Successfully saved {len(entities_data)} new leads.")
    except Exception as e: logging.error(f"Failed to save data to Google Sheets: {e}")

# --- CHECKPOINTING (Unchanged) ---
def load_processed_anchors() -> Set[str]:
    if not os.path.exists(PROCESSED_ANCHORS_FILE): return set()
    with open(PROCESSED_ANCHORS_FILE, 'r', encoding='utf-8') as f: return {line.strip() for line in f}

def checkpoint_anchor(url: str) -> None:
    with open(PROCESSED_ANCHORS_FILE, 'a', encoding='utf-8') as f: f.write(f"{url}\n")


# --- ✅ NEW SCRAPING LOGIC (No `requests` library) ---
def map_entities_from_source(page_source: str, base_url: str) -> Dict[str, str]:
    """
    Parses HTML content provided by Selenium to find entities and websites.
    This function NO LONGER makes its own web requests.
    """
    leads = {}
    STOP_WORDS = {
        'home', 'about', 'contact', 'sitemap', 'disclaimer', 'privacy', 'policy', 'terms', 'search',
        'copyright', 'feedback', 'help', 'faq', 'login', 'register', 'download', 'press', 'media',
        'news', 'events', 'blog', 'gallery', 'videos', 'photos', 'career', 'archive', 'ministry',
        'skip to main content', 'related resources', 'advocacy', 'rti information', 'government',
        'dashboard', 'overview', 'summary', 'annual report', 'yuva sampad', 'department', 'tender'
    }
    SOCIAL_DOMAINS = ['facebook.com', 'twitter.com', 'instagram.com', 'linkedin.com', 'youtube.com']
    
    try:
        soup = BeautifulSoup(page_source, 'html.parser')
        for item in soup.find_all(['li', 'tr']):
            link_tag = item.find('a')
            if not link_tag: continue
            
            entity_name = link_tag.get_text(strip=True)
            if not (4 < len(entity_name) < 100) or ' ' not in entity_name: continue
            if any(stop_word in entity_name.lower() for stop_word in STOP_WORDS): continue
            if '.pdf' in entity_name.lower() or 'read more' in entity_name.lower(): continue

            href = link_tag.get('href')
            if not href or href.startswith(('#', 'mailto:', 'javascript:')): continue
            
            entity_website = urljoin(base_url, href)
            parsed_uri = urlparse(entity_website)
            domain = parsed_uri.netloc
            
            if domain and all(social not in domain for social in SOCIAL_DOMAINS):
                leads[entity_name] = f"{parsed_uri.scheme}://{domain}"
    except Exception as e:
        logging.error(f"Error parsing page source for {base_url}: {e}")

    return leads

# --- ✅ NEW UNIFIED SEARCH & SCRAPE FUNCTION ---
def search_and_scrape_leads(keyword: str, config: configparser.ConfigParser, processed_anchors: Set[str]) -> Dict[str, Dict]:
    """
    Manages the entire Selenium lifecycle: searches Google, visits each result,
    scrapes it within the browser, and returns all found leads.
    """
    logging.info(f"Starting unified search and scrape for: '{keyword}'")
    
    headless_mode = config.getboolean('Selenium', 'headless'); num_results = config.getint('Selenium', 'num_search_results')
    profile_path = config.get('Selenium', 'profile_path', fallback=None); http_proxy = config.get('Proxy', 'http_proxy', fallback=None)

    options = webdriver.ChromeOptions()
    options.add_argument(f"user-agent={random.choice(USER_AGENTS)}"); options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized"); options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    if http_proxy: options.add_argument(f'--proxy-server={http_proxy}')
    if headless_mode: options.add_argument("--headless"); options.add_argument("--window-size=1920,1080")
    if profile_path: options.add_argument(f"user-data-dir={profile_path}")
    
    driver = None
    all_found_leads = {}
    
    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
        
        wait = WebDriverWait(driver, 10); short_wait = WebDriverWait(driver, 3)
        
        # --- Step 1: Search Google ---
        search_url = f"https://www.google.com/search?q={keyword.replace(' ', '+')}&num={num_results}"
        driver.get(search_url)
        # Handle consent pop-up
        try:
            reject_button = short_wait.until(EC.element_to_be_clickable((By.XPATH, "//button[div[contains(text(), 'Reject all')]]")))
            reject_button.click(); logging.info("Auto-clicked consent button.")
        except Exception: logging.info("No consent pop-up found.")
        
        # Find result URLs
        link_selector = "a > h3"
        urls_to_visit = []
        try:
            wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, link_selector)))
            h3_elements = driver.find_elements(By.CSS_SELECTOR, link_selector)
            link_elements = [elem.find_element(By.XPATH, "..") for elem in h3_elements]
            urls_to_visit = [elem.get_attribute('href') for elem in link_elements if elem.get_attribute('href')]
            urls_to_visit = list(dict.fromkeys(urls_to_visit))[:num_results]
        except Exception:
            logging.warning("Could not find results automatically. Manual intervention may be needed.")
            input(f"\n>>> ACTION REQUIRED for '{keyword}': Please handle any pop-up/CAPTCHA, then press Enter here...")
            # Retry after manual action
            h3_elements = driver.find_elements(By.CSS_SELECTOR, link_selector)
            link_elements = [elem.find_element(By.XPATH, "..") for elem in h3_elements]
            urls_to_visit = [elem.get_attribute('href') for elem in link_elements if elem.get_attribute('href')]

        logging.info(f"Found {len(urls_to_visit)} anchor sites to visit.")

        # --- Step 2: Visit Each URL and Scrape ---
        for url in urls_to_visit:
            if url in processed_anchors:
                logging.info(f"Skipping already processed anchor: {url}")
                continue
            
            try:
                logging.info(f"Navigating to: {url}")
                driver.get(url)
                time.sleep(random.uniform(2, 4)) # Wait for page to load JS elements
                
                page_source = driver.page_source
                leads = map_entities_from_source(page_source, url)
                if leads:
                    logging.info(f"Found {len(leads)} potential leads on {url}")
                    all_found_leads[url] = leads # Store leads keyed by their source URL
                
                checkpoint_anchor(url) # Mark as processed
            except Exception as e:
                logging.error(f"Failed to process {url}: {e}")
                checkpoint_anchor(url) # Mark as processed even if it fails to avoid retrying a broken link
        
        return all_found_leads
    
    except Exception as e: 
        logging.error(f"A critical error occurred in the Selenium session for '{keyword}': {e}")
        return all_found_leads
    finally:
        if driver: driver.quit()


# --- ✅ MAIN WORKFLOW (Adapted for new function) ---
def main():
    """Main function to orchestrate the lead generation process."""
    global USER_AGENTS
    config = load_config()
    
    user_agents_str = config.get('Selenium', 'user_agents', fallback='')
    USER_AGENTS = [ua.strip() for ua in user_agents_str.splitlines() if ua.strip()]
    if not USER_AGENTS: logging.critical("No user_agents found in config.ini. Exiting."); return

    sheets_service = setup_sheets_service()
    spreadsheet_id = config.get('GoogleSheets', 'spreadsheet_id')
    if not sheets_service or not spreadsheet_id or not ensure_master_sheet_exists(sheets_service, spreadsheet_id):
        logging.critical("Could not set up Google Sheets. Exiting."); return

    logging.info("Fetching existing entities to prevent duplicates...")
    existing_entities = get_existing_entities(sheets_service, spreadsheet_id)
    logging.info(f"Found {len(existing_entities)} existing entities.")
    processed_anchors = load_processed_anchors()

    SEARCH_QUERIES = {
        "Sports Federations": ["list of state sports associations Gujarat", "list of sports associations Delhi", "district level sports federations Delhi NCR"],
        "Sports Leagues": ["local football leagues Delhi NCR", "amateur cricket tournaments Gujarat", "state level kabaddi league Gujarat"],
        "Sports Franchise Teams": ["teams in Gujarat state T20 league", "Delhi state football league teams", "local basketball teams Ahmedabad"],
        "Events & IPs": ["upcoming marathons Ahmedabad 2025", "local sports events Delhi NCR", "5k runs in Gujarat"],
        "Sports Facilities / Venues": ["list of sports complexes in Ahmedabad", "cricket grounds for rent Delhi", "football turfs Surat"]
    }

    for category, keywords in SEARCH_QUERIES.items():
        for keyword in keywords:
            logging.info(f"--- Processing Category: '{category}' | Keyword: '{keyword}' ---")
            
            # This one function now does everything
            leads_by_source = search_and_scrape_leads(keyword, config, processed_anchors)
            
            for source_url, found_leads in leads_by_source.items():
                leads_to_save = []
                for name, website in found_leads.items():
                    if name not in existing_entities:
                        leads_to_save.append([name, category, website, source_url, keyword])
                        existing_entities.add(name) # Add to set to prevent duplicates in same run
                
                if leads_to_save:
                    save_entities_to_sheet(sheets_service, spreadsheet_id, leads_to_save)
                else:
                    logging.info(f"Leads found on {source_url}, but they all already exist in the database.")
            
            time.sleep(random.uniform(5, 8))

if __name__ == "__main__":
    main()