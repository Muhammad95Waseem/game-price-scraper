import os
import sys
from dotenv import load_dotenv

# Locate the absolute path of the directory containing this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Inject it cleanly at position 0 in Python's search path
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

# --- Now modules can be safely imported without throwing errors ---
import logging
from app.db import engine, Base
from app.scrapers.fanatical_scraper import FanaticalScraper
from app.scrapers.gog_scraper import GOGScraper
from app.scrapers.humble_scraper import HumbleBundleScraper

# Configure terminal logging output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("runner")

def main():
    logger.info("Initializing connection to Supabase and verifying database schemas...")
    # This automatically registers your tables on Supabase if they don't exist
    Base.metadata.create_all(bind=engine)
    
    scrapers = [
        FanaticalScraper(),
        GOGScraper(),
        HumbleBundleScraper()
    ]
    
    logger.info("Launching store crawlers...")
    for scraper in scrapers:
        try:
            logger.info(f"Running pipeline for: {scraper.STORE_NAME}")
            scraper.run()
        except Exception as e:
            logger.error(f"Critical execution error on {scraper.STORE_NAME}: {e}")
            
    logger.info("All scraper routines completed successfully.")

if __name__ == "__main__":
    main()