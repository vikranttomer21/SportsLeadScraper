import configparser
import time
import requests
import random
import os
import logging
import re
from urllib.parse import urlparse, urljoin
from PIL import Image
import pytesseract

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- IMPORTANT TESSERACT CONFIGURATION ---
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper.log", mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# --- CONFIGURATION & GOOGLE SHEETS (Shortened for brevity) ---
def load_config():
    config = configparser.ConfigParser()
    config.read('config.ini')
    return config

def setup_sheets_service():
    try:
        creds = service_account.Credentials.from_service_account_file(
            'service_account.json', scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        return build('sheets', 'v4', credentials=creds)
    except Exception as e:
        logging.error(f"Failed to set up Google Sheets service: {e}")
        return None

def ensure_sheets_exist(service, spreadsheet_id):
    sheets_to_ensure = ["Tier 1: Low Performers", "Tier 2: Mid Performers", "Tier 3: High Performers"]
    headers = [ "Name", "Website", "Email", "Phone", "Social Media Link", "Sport", "City", "Country", "Critic Score", "Source Keyword" ]
    try:
        sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing_sheets = [s['properties']['title'] for s in sheet_metadata.get('sheets', [])]
        for sheet_name in sheets_to_ensure:
            if sheet_name not in existing_sheets:
                logging.info(f"Creating sheet '{sheet_name}' with headers...")
                body = {'requests': [{'addSheet': {'properties': {'title': sheet_name}}}]}
                service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
                service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1",
                    valueInputOption='USER_ENTERED', body={'values': [headers]}
                ).execute()
    except HttpError as e: return False
    return True

def save_to_sheet(service, spreadsheet_id, sheet_name, data_row):
    try:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1",
            valueInputOption='USER_ENTERED', body={'values': [data_row]}
        ).execute()
        logging.info(f"Successfully saved lead '{data_row[0]}' to sheet: {sheet_name}")
    except Exception as e:
        logging.error(f"Failed to save data to Google Sheets: {e}")

# --- RESUMABILITY ---
PROCESSED_ENTITIES_FILE = 'processed_entities.txt'
def load_processed_entities():
    if not os.path.exists(PROCESSED_ENTITIES_FILE): return set()
    with open(PROCESSED_ENTITIES_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip().lower() for line in f)

def checkpoint_entity(entity_name):
    with open(PROCESSED_ENTITIES_FILE, 'a', encoding='utf-8') as f: f.write(f"{entity_name.lower()}\n")

# --- CORRECTED CORE SCRAPING FUNCTION ---
def get_domain_authority(api_key, domain):
    """Calls RapidAPI to get the Domain Authority score for a domain."""
    url = "https://bulk-domain-da-pa-check.p.rapidapi.com/check"
    payload = {"urls": [domain]}
    headers = {
        "content-type": "application/json",
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "bulk-domain-da-pa-check.p.rapidapi.com"
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        if data and data.get('data') and data['data'][0].get('da'):
            score = int(data['data'][0]['da'])
            logging.info(f"DA score for {domain}: {score}")
            return score
        else:
            logging.warning(f"DA score not found in API response for {domain}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"API call for DA failed for {domain}: {e}")
        return None

def scrape_entity_details(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        emails = re.findall(email_pattern, soup.get_text())
        email = emails[0] if emails else "N/A"

        phone_pattern = r'(?:\+91[\-\s]?)?[789]\d{9}'
        phones = re.findall(phone_pattern, soup.get_text())
        phone = phones[0] if phones else "N/A"

        social_platforms = ['instagram.com', 'facebook.com', 'twitter.com', 'x.com']
        social_link = "N/A"
        for link in soup.find_all('a', href=True):
            for platform in social_platforms:
                if platform in link['href']:
                    social_link = link['href']
                    return email, phone, social_link
        
        return email, phone, social_link
    except Exception as e:
        logging.error(f"Error scraping details from {url}: {e}")
        return "N/A", "N/A", "N/A"

def map_entities_from_anchor(anchor_url):
    logging.info(f"Mapping entities from anchor: {anchor_url}")
    entities = set()
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(anchor_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        section_keywords = ['affiliated clubs', 'our teams', 'league participants', 'senior division', 'premier league', 'clubs']
        
        for keyword in section_keywords:
            header = soup.find(lambda tag: tag.name in ['h2', 'h3', 'b', 'strong'] and keyword in tag.get_text(strip=True).lower())
            if header:
                logging.info(f"Found section header: '{header.get_text(strip=True)}'")
                container = header.find_next_sibling(['ul', 'ol', 'table'])
                if container:
                    items = container.find_all(['li', 'tr'])
                    for item in items:
                        text = item.get_text(strip=True)
                        if 5 < len(text) < 50:
                            entities.add(text)
        
        if not entities:
            logging.warning("Context-aware search found no entities. Falling back to generic search.")
            for item in soup.find_all(['li', 'h3']):
                text = item.get_text(strip=True)
                if 5 < len(text) < 50 and "copyright" not in text.lower():
                    entities.add(text)

        logging.info(f"Found {len(entities)} potential entities from {anchor_url}")
        return list(entities)
    except Exception as e:
        logging.error(f"Failed to map entities from {anchor_url}: {e}")
        return []

def search_and_get_url(keyword, headless_mode, num_results=1):
    logging.info(f"Starting VISUAL Selenium search for: '{keyword}'")
    options = webdriver.ChromeOptions()
    profile_path = r"C:\Users\USER\AppData\Local\Google\Chrome\User Data\Profile 31"
    options.add_argument(f"user-data-dir={profile_path}")
    if headless_mode:
        logging.error("Visual search cannot run in headless mode.")
        return []
    
    driver = None
    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_window_size(1280, 1024)
        search_url = f"https://www.google.com/search?q={keyword.replace(' ', '+')}&num={num_results}"
        driver.get(search_url)

        input(f"\n>>> ACTION REQUIRED for '{keyword}': Please solve CAPTCHA if present, then press Enter here...")

        screenshot_path = "search_screenshot.png"
        driver.save_screenshot(screenshot_path)
        ocr_data = pytesseract.image_to_data(Image.open(screenshot_path), output_type=pytesseract.Output.DICT)
        
        found_url = None
        search_term_part = keyword.split(' ')[0].lower()
        for i in range(len(ocr_data['level'])):
            text = ocr_data['text'][i].lower()
            if search_term_part in text and '.' in text and len(text) > 4:
                (x, y, w, h) = (ocr_data['left'][i], ocr_data['top'][i], ocr_data['width'][i], ocr_data['height'][i])
                logging.info(f"Visually clicking '{ocr_data['text'][i]}' at coordinates: ({x + w // 2}, {y + h // 2})")
                ActionChains(driver).move_by_offset(x + w // 2, y + h // 2).click().perform()
                time.sleep(4)
                found_url = driver.current_url
                logging.info(f"Navigated to: {found_url}")
                break
        
        return [found_url] if found_url else []
    except Exception as e:
        logging.error(f"Visual search failed for '{keyword}': {e}")
        return []
    finally:
        if driver:
            driver.quit()
        if os.path.exists("search_screenshot.png"):
            os.remove("search_screenshot.png")

# --- MAIN WORKFLOW ---
def main():
    config = load_config()
    sheets_service = setup_sheets_service()
    if not sheets_service or not ensure_sheets_exist(sheets_service, config['GoogleSheets']['spreadsheet_id']): return

    API_KEY = config['RapidAPI']['api_key']
    SPREADSHEET_ID = config['GoogleSheets']['spreadsheet_id']
    FOLLOWER_LIMIT = int(config['Settings']['social_media_follower_limit'])
    DA_LIMIT = int(config['Settings']['domain_authority_limit'])
    HEADLESS_MODE = config.getboolean('Selenium', 'headless', fallback=False)

    if HEADLESS_MODE:
        logging.error("This script must run with headless = false in config.ini")
        return
    
    processed_entities = load_processed_entities()
    logging.info(f"Loaded {len(processed_entities)} processed entities.")

    search_keywords = ["delhi football association", "gujarat state football association", "delhi cricket league", "ahmedabad sports academies"]
    exclusion_list = ["ipl", "isl", "bcci", "pro kabaddi", "wikipedia"]
    master_entity_list = {}

    # PHASE 1: MAPPING
    logging.info("--- STARTING PHASE 1: MAPPING ---")
    for keyword in search_keywords:
        anchor_sites = search_and_get_url(keyword, HEADLESS_MODE, num_results=1)
        if anchor_sites:
            entities = map_entities_from_anchor(anchor_sites[0])
            master_entity_list[keyword] = entities
            time.sleep(random.uniform(5, 10))
    
    # PHASE 2: QUALIFYING
    logging.info("--- STARTING PHASE 2: QUALIFYING ---")
    for source_keyword, entities in master_entity_list.items():
        total_entities = len(entities)
        logging.info(f"--- Qualifying {total_entities} entities found from '{source_keyword}' ---")
        for i, entity_name in enumerate(entities, 1):
            if entity_name.lower() in processed_entities:
                logging.info(f"[{i}/{total_entities}] Skipping already processed entity: '{entity_name}'")
                continue
            
            logging.info(f"[{i}/{total_entities}] Qualifying entity: '{entity_name}'")
            entity_websites = search_and_get_url(f"{entity_name} official website", HEADLESS_MODE, num_results=1)
            if not entity_websites:
                logging.warning(f"Could not find a website for '{entity_name}'. Skipping.")
                checkpoint_entity(entity_name)
                continue

            url = entity_websites[0]
            domain = urlparse(url).netloc.replace('www.', '')
            if any(ex in entity_name.lower() or ex in domain for ex in exclusion_list):
                checkpoint_entity(entity_name)
                continue

            da_score = get_domain_authority(API_KEY, domain)
            if da_score is None or da_score >= DA_LIMIT:
                logging.info(f"Skipping '{entity_name}'. DA score ({da_score}) is outside limits.")
                checkpoint_entity(entity_name)
                continue
            
            email, phone, social_link = scrape_entity_details(url)
            social_media_count = 0 # Placeholder for now
            if social_media_count >= FOLLOWER_LIMIT:
                checkpoint_entity(entity_name)
                continue

            critic_score = random.randint(30, 90)
            
            final_data = [ entity_name, url, email, phone, social_link, "Sport Placeholder", "City Placeholder", "India", critic_score, source_keyword ]
            
            sheet_to_save = ""
            if critic_score < 60: sheet_to_save = "Tier 1: Low Performers"
            elif 60 <= critic_score < 75: sheet_to_save = "Tier 2: Mid Performers"
            else: sheet_to_save = "Tier 3: High Performers"
            
            save_to_sheet(sheets_service, SPREADSHEET_ID, sheet_to_save, final_data)
            checkpoint_entity(entity_name)
            time.sleep(random.uniform(3, 6))

if __name__ == "__main__":
    main()