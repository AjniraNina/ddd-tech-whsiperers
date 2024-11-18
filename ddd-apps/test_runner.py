import logging
import os
import tempfile
import time  # Add this
import random  # Add this
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class TestRunner:
    def __init__(self):
        self.chrome_options = Options()
        self.chrome_options.add_argument("--headless")
        self.chrome_options.add_argument("--no-sandbox")
        self.chrome_options.add_argument("--disable-dev-shm-usage")
        self.chrome_options.add_argument("--disable-gpu")
        self.chrome_options.add_argument("--window-size=1920,1080")
        self.chrome_options.add_argument("--log-level=DEBUG")

    def test_page(self, content: str) -> (bool, str):
        if not content or not content.strip():
            return False, "Empty content provided"

        driver = None
        test_page_path = None
        try:
            # Create a temporary page name for testing
            test_page_name = f"test_{int(time.time())}_{random.randint(1000, 9999)}"
            pages_dir = os.path.join(os.getcwd(), "templates", "pages")
            if not os.path.exists(pages_dir):
                os.makedirs(pages_dir)

            # Save the test page
            test_page_path = os.path.join(pages_dir, f"{test_page_name}.html")
            with open(test_page_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.debug(f"Created temporary page: {test_page_path}")

            # Initialize the Chrome driver in headless mode
            driver = webdriver.Chrome(options=self.chrome_options)
            driver.set_script_timeout(10)

            # Load the page through the Flask server
            driver.get(f"http://localhost:5000/pages/{test_page_name}")
            # Implicitly wait to ensure the page has time to load
            driver.implicitly_wait(10)

            # Validate DOCTYPE
            doctype_declared = content.strip().lower().startswith("<!doctype html>")
            if not doctype_declared:
                logger.debug("Missing or incorrect DOCTYPE declaration.")
                return False, "Missing or incorrect DOCTYPE declaration"

            # Check for essential HTML tags
            required_tags = [
                "<html",
                "</html>",
                "<head>",
                "</head>",
                "<body>",
                "</body>",
            ]
            for tag in required_tags:
                if tag not in content.lower():
                    logger.debug(f"Missing required tag: {tag}")
                    return False, f"Missing required tag: {tag}"

            # Check for the meta charset and viewport tags
            if '<meta charset="utf-8">' not in content.lower():
                logger.debug("Missing meta charset declaration.")
                return False, "Missing meta charset declaration"
            if '<meta name="viewport"' not in content.lower():
                logger.debug("Missing viewport meta tag.")
                return False, "Missing viewport meta tag"

            # Check for JavaScript errors in the console
            logs = driver.get_log("browser")
            errors = [log for log in logs if log["level"].upper() == "SEVERE"]
            if errors:
                logger.debug(f"JavaScript errors: {errors}")
                return False, f"JavaScript errors: {errors}"

            # Everything passed
            logger.debug("Page passed all tests.")
            return True, None

        except TimeoutException:
            logger.debug("Page load timeout occurred.")
            return False, "Page load timeout"
        except Exception as e:
            logger.error(f"Test error: {str(e)}")
            return False, str(e)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception as e:
                    logger.error(f"Error quitting driver: {e}")
            if test_page_path and os.path.exists(test_page_path):
                try:
                    os.unlink(test_page_path)
                    logger.debug(f"Deleted temporary page: {test_page_path}")
                except Exception as e:
                    logger.error(f"Error deleting temporary page {test_page_path}: {e}")
