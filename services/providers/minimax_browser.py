"""
MiniMax Agent Browser Chat Service
Sends messages through the page's own UI, leveraging the page's axios
signing interceptor for authentication.
"""

import json
import time
import os
from typing import Optional, Dict

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from services.providers.chrome_utils import get_chromedriver_service

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "LLM_Agent", "minimax_chrome")


class MiniMaxBrowser:
    """Send messages to MiniMax Agent via browser UI"""

    def __init__(self):
        self.driver = None
        self._logged_in = False

    def start(self):
        service = get_chromedriver_service()
        opts = Options()
        opts.binary_location = CHROME_PATH
        opts.add_argument("--start-maximized")
        opts.add_argument(f"--user-data-dir={USER_DATA_DIR}")
        opts.add_argument("--profile-directory=Default")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        self.driver = webdriver.Chrome(service=service, options=opts)
        self.driver.set_script_timeout(180)

    def check_login(self) -> bool:
        token = self.driver.execute_script("return localStorage.getItem('_token') || '';")
        self._logged_in = bool(token)
        return self._logged_in

    def get_token(self) -> str:
        return self.driver.execute_script("return localStorage.getItem('_token') || '';")

    def send_message(self, content: str, model_id: str = "MiniMax-M3", wait_seconds: int = 120) -> Dict:
        """
        Send a message via the page's UI and wait for the response.
        The page's own axios interceptor handles all signing.
        """
        try:
            # Navigate to home
            self.driver.get("https://agent.minimaxi.com/")
            time.sleep(5)
            
            # Wait for textarea to appear (SPA loading)
            wait = WebDriverWait(self.driver, 30)
            textarea = wait.until(EC.presence_of_element_located((By.TAG_NAME, "textarea")))
            
            # Clear and type message
            textarea.clear()
            textarea.send_keys(content)
            time.sleep(0.5)
            textarea.send_keys(Keys.ENTER)
            
            # Wait for response to complete
            # The page shows a "stop" button while generating, and it disappears when done
            # We wait for the response to stabilize
            time.sleep(5)  # Initial wait for request to start
            
            # Poll until response is complete (no more streaming indicators)
            stable_count = 0
            last_text = ""
            for _ in range(wait_seconds):
                time.sleep(1)
                current_text = self._get_latest_response()
                if current_text and current_text == last_text:
                    stable_count += 1
                    if stable_count >= 5:  # Stable for 5 seconds
                        break
                else:
                    stable_count = 0
                last_text = current_text
            
            return {
                "success": True,
                "reply": last_text,
            }
            
        except Exception as e:
            return {"error": str(e)}

    def _get_latest_response(self) -> str:
        """Extract the latest assistant message from the page"""
        try:
            return self.driver.execute_script("""
                // Find all message blocks - the last assistant message is the reply
                var blocks = document.querySelectorAll('[class*="message"]');
                var lastReply = '';
                blocks.forEach(function(b) {
                    var text = b.textContent || '';
                    if (text.length > 10) lastReply = text.trim();
                });
                
                // Also try markdown content
                var mdBlocks = document.querySelectorAll('[class*="markdown"], [class*="reply"], [class*="assistant"]');
                mdBlocks.forEach(function(b) {
                    var text = b.textContent || '';
                    if (text.length > 10) lastReply = text.trim();
                });
                
                return lastReply;
            """) or ""
        except:
            return ""

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
