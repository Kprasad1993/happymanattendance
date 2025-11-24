import os
import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import requests

import json

# ... (imports remain same)

# Load environment variables
load_dotenv()

HRMS_URL = "https://hrms.happymanbusiness.com/users/sign_in"
WEBHOOK_URL = os.getenv("NOTIFICATION_WEBHOOK")

def get_users():
    """
    Parses user credentials from environment variables.
    Supports 'HRMS_ACCOUNTS' (JSON list) or legacy 'HRMS_EMAIL'/'HRMS_PASSWORD'.
    """
    accounts_json = os.getenv("HRMS_ACCOUNTS")
    if accounts_json:
        try:
            users = json.loads(accounts_json)
            if isinstance(users, list):
                return users
        except json.JSONDecodeError:
            print("Error: HRMS_ACCOUNTS is not valid JSON.")
    
    # Fallback to single user
    email = os.getenv("HRMS_EMAIL")
    password = os.getenv("HRMS_PASSWORD")
    if email and password:
        return [{"email": email, "password": password}]
    
    return []

def send_notification(message):
    # ... (same as before)
    if not WEBHOOK_URL:
        print(f"Notification (not sent): {message}")
        return
    try:
        payload = {"text": message}
        requests.post(WEBHOOK_URL, json=payload)
        print(f"Notification sent: {message}")
    except Exception as e:
        print(f"Failed to send notification: {e}")

def process_user(user, action):
    """
    Runs attendance logic for a single user.
    Returns True if successful, False otherwise.
    """
    email = user.get('email')
    password = user.get('password')
    
    print(f"Processing user: {email}...")
    
    max_retries = 1
    retry_delay = 900  # 15 minutes

    for attempt in range(max_retries + 1):
        print(f"  Attempt {attempt + 1} of {max_retries + 1}...")
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                
                # 1. Login
                print(f"  Navigating to {HRMS_URL}...")
                page.goto(HRMS_URL)
                
                if "sign_in" in page.url:
                    print("  Logging in...")
                    page.fill("input#user_email", email)
                    page.fill("input#user_password", password)
                    page.click("button.btn.btn-bd-filled")
                    
                    try:
                        page.wait_for_url("**/dashboard", timeout=15000)
                        print("  Login successful.")
                    except:
                        if page.is_visible(".alert-danger"):
                            raise Exception(f"Login failed: {page.inner_text('.alert-danger')}")
                        if "sign_in" in page.url:
                            raise Exception("Still on login page. Login failed.")

                # 2. Perform Action
                print(f"  Attempting to Mark Attendance (Action: {action})...")
                
                dashboard_btn_selector = "text=Mark Attendance"
                if page.is_visible(dashboard_btn_selector):
                    print("  Clicking 'Mark Attendance' on dashboard...")
                    page.click(dashboard_btn_selector)
                    page.wait_for_timeout(2000)
                else:
                    print("  Dashboard 'Mark Attendance' button not found.")
                    # Continue to see if modal appears anyway or if we are already done

                # 3. Handle Modal
                print("  Waiting for 'My Attendance' modal...")
                try:
                    # The log showed "locator resolved to 3 elements", likely including sidebar links.
                    # We need to be more specific to the MODAL title.
                    # Trying common bootstrap modal title classes or specific text structure.
                    # We will try to find the text "My Attendance" that is visible and likely a heading.
                    page.wait_for_selector("h4:has-text('My Attendance'), h5:has-text('My Attendance'), div.modal-header:has-text('My Attendance')", timeout=5000)
                    print("  Modal 'My Attendance' detected.")
                    
                    modal_btn_selector = "div.modal-content button:has-text('Mark Attendance')"
                    if page.is_visible(modal_btn_selector):
                        print("  Clicking 'Mark Attendance' inside modal...")
                        page.click(modal_btn_selector)
                        page.wait_for_timeout(3000)
                        print("  Clicked confirmation.")
                    else:
                        # Fallback
                        print("  Specific modal button not found. searching for any visible 'Mark Attendance' button...")
                        # We need to avoid clicking the dashboard button again.
                        # The modal button should be the one that is visible.
                        page.click("button:has-text('Mark Attendance')")
                        page.wait_for_timeout(3000)

                except Exception as e:
                    print(f"  Error handling modal: {e}")
                    # Don't fail hard if modal didn't show, maybe already marked?

                # 4. Sign Out
                print("  Signing out...")
                try:
                    # 1. Try direct link if visible (unlikely)
                    if page.is_visible("a[href*='sign_out']"):
                         page.click("a[href*='sign_out']")
                    
                    # 2. Try toggling the menu
                    else:
                        # Selectors for the profile icon/avatar in the top right
                        # Based on screenshot: likely a .dropdown-toggle or .user-avatar
                        profile_selectors = [
                            ".user-profile", 
                            ".user-avatar", 
                            ".dropdown-toggle", 
                            "img.img-circle", 
                            "header .avatar",
                            "li.dropdown > a",
                            "div.profile-user"
                        ]
                        
                        menu_opened = False
                        for selector in profile_selectors:
                            if page.is_visible(selector):
                                # Only click if we haven't found the menu yet
                                if not page.is_visible("text=Sign Out"):
                                    print(f"    Clicking profile icon: {selector}")
                                    page.click(selector)
                                    page.wait_for_timeout(1000) # Wait for animation
                                
                                if page.is_visible("text=Sign Out"):
                                    menu_opened = True
                                    break
                        
                        if menu_opened or page.is_visible("text=Sign Out"):
                            print("    Clicking 'Sign Out'...")
                            page.click("text=Sign Out")
                        else:
                            print("    Could not find 'Sign Out' button or profile menu.")
                    
                    # 3. Wait for redirect
                    page.wait_for_url("**/sign_in", timeout=10000)
                    print("  Signed out successfully.")
                except Exception as e:
                    print(f"  Warning: Failed to sign out: {e}")

                return True # Success

        except Exception as e:
            print(f"  Attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return False # Failed after retries
        finally:
            try:
                browser.close()
            except:
                pass
    return False

def run_attendance_bot(action=None):
    # 1. Check for Weekends
    # Monday is 0 and Sunday is 6
    if datetime.now().weekday() >= 5:
        print("Today is a weekend (Saturday/Sunday). Skipping attendance.")
        return

    # 2. Check for Holidays
    # Format: "YYYY-MM-DD,YYYY-MM-DD"
    holidays_env = os.getenv("HOLIDAYS", "")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if holidays_env:
        holidays = [h.strip() for h in holidays_env.split(",")]
        if today_str in holidays:
            print(f"Today ({today_str}) is a configured holiday. Skipping attendance.")
            return

    if not action:
        current_hour = datetime.now().hour
        if 8 <= current_hour < 12:
            action = 'check_in'
        elif 18 <= current_hour < 21:
            action = 'sign_out'
        else:
            print("Defaulting to check_in.")
            action = 'check_in'

    users = get_users()
    if not users:
        print("No users found. Set HRMS_EMAIL/PASSWORD or HRMS_ACCOUNTS.")
        sys.exit(1)

    print(f"Starting Attendance Bot for {len(users)} users. Action: {action}")
    
    # Log loaded users (emails only) for verification
    loaded_emails = [u.get('email', 'Unknown') for u in users]
    print(f"Loaded users: {', '.join(loaded_emails)}")
    
    failures = []
    for user in users:
        success = process_user(user, action)
        if not success:
            failures.append(user['email'])

    if failures:
        error_msg = f"Attendance Bot Failed for users: {', '.join(failures)}"
        print(error_msg)
        send_notification(error_msg)
        sys.exit(1)
    else:
        print("All users processed successfully.")

if __name__ == "__main__":
    forced_action = sys.argv[1] if len(sys.argv) > 1 else None
    run_attendance_bot(forced_action)
