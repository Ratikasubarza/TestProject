import os
import openai
import gspread
import json
import requests
import signal
import sys
from jira import JIRA
from google.oauth2.service_account import Credentials 
from transformers import pipeline
from multiprocessing import resource_tracker
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
import time
import base64


# Configuration
JIRA_SERVER = "https://finacceljira.atlassian.net"
JIRA_API_TOKEN = ''
JIRA_EMAIL = ''
GEMINI_API_KEY = ""
GOOGLE_CREDS_FILE = ''
SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = ""
CONFLUENCE_URL = "https://finacceljira.atlassian.net/wiki"
CONFLUENCE_USERNAME = ""  
CONFLUENCE_API_TOKEN = ""
CONFLUENCE_PAGE_ID = ""  # ID halaman Confluence yang ingin diambil

def fetch_jira_requirement(jira_id):
    """Fetch the requirement from a JIRA ticket."""
    url = f"{JIRA_SERVER}/rest/api/3/issue/{jira_id}"
    headers = {
        "Accept": "application/json"
    }
    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    response = requests.get(url, headers=headers, auth=auth)

    if response.status_code == 200:
        issue_data = response.json()

        # Extract full description
        description = issue_data["fields"].get("description")
        
        if isinstance(description, dict) and "content" in description:
            # Jira Cloud uses a new format (Atlassian Document Format - ADF)
            full_description = parse_jira_description(description)
        else:
            # Jira Server/DC may return plain text
            full_description = description if description else "No description available"

        return full_description

    elif response.status_code == 404:
        print(f"❌ ERROR: Jira ID {jira_id} not found (404 Not Found).")
    elif response.status_code == 401:
        print(f"❌ ERROR: Unauthorized. Check your API token.")
    else:
        print(f"❌ ERROR: Failed to fetch Jira requirement (Status {response.status_code}).")

    return None


def fetch_confluence_page(page_id):
    url = f"{CONFLUENCE_URL}/rest/api/content/{CONFLUENCE_PAGE_ID}?expand=body.storage"

    # Encode username dan API token ke dalam format Basic Auth
    auth_string = f"{CONFLUENCE_USERNAME}:{CONFLUENCE_API_TOKEN}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()

    # Headers untuk autentikasi
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {auth_encoded}"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        try:
            page_data = response.json()
            
            # Extract page content
            page_content = page_data["body"]["storage"]["value"]
            
            # Find the section with "Description" (basic string search or regex)
            if "Description:" in page_content:
                description_start = page_content.find("Description:") + len("Description:")
                description_details = page_content[description_start:].strip()
                
                # Optional: Clean up HTML tags if needed (using regex or BeautifulSoup)
                return description_details
            else:
                print("⚠️ WARNING: 'Description' section not found in Confluence page.")
                return page_content  # Return full content if no specific description
            
        except KeyError:
            print("❌ ERROR: Response structure is not as expected.")
            return None
    elif response.status_code == 401:
        print("❌ ERROR: Unauthorized. Check your API Token or credentials.")
        return None
    else:
        print(f"❌ ERROR: Failed to fetch Confluence page {page_id} (Status {response.status_code})")
        return None

# Contoh Penggunaan
requirement_text = fetch_confluence_page(CONFLUENCE_PAGE_ID)

if requirement_text:
    print("✅ Requirement berhasil diambil dari Confluence:")
    print(requirement_text)
else:
    print("❌ ERROR: Requirement tidak ditemukan atau gagal diambil.")

def parse_jira_description(adf):
    """Parses Jira's Atlassian Document Format (ADF) to extract text, tables, and images."""
    content = []

    def extract_text(block):
        """Recursively extract text from ADF blocks, including tables and images."""
        if isinstance(block, dict):
            if block.get("type") == "text":
                return block.get("text", "")
            elif block.get("type") == "table":
                return extract_table(block)
            elif block.get("type") == "mediaSingle":
                return extract_image(block)
            elif "content" in block:
                return " ".join(extract_text(item) for item in block["content"])
        elif isinstance(block, list):
            return " ".join(extract_text(item) for item in block)
        return ""

    def extract_table(table_block):
        """Extracts table content from Jira ADF format."""
        table_text = "\n📊 **Extracted Table:**\n"
        for row in table_block.get("content", []):  # Iterate through table rows
            row_text = " | ".join(extract_text(cell) for cell in row.get("content", []))
            table_text += f"{row_text}\n"
        return table_text

    def extract_image(image_block):
        """Extracts image URLs from Jira ADF format."""
        attrs = image_block.get("attrs", {})
        image_url = attrs.get("url") or attrs.get("id")  # Check for URL or ID
        if image_url:
            return f"\n🖼️ **Image:** {image_url}\n"
        return ""

    full_text = extract_text(adf["content"])
    return full_text.strip()


def generate_test_cases(requirement):
    print("@ Received requirement:", requirement)  # Debugging
    # Ensure requirement is properly extracted
    plain_test_requirement = extract_text(requirement) if isinstance(requirement, dict) else requirement
    # Debugging
    print("@ Extracted Requirement:", plain_test_requirement)
    # Ensure valid requirement exists
    if not plain_test_requirement or plain_test_requirement.startswith("x"):
        print("X No valid text extracted. Skipping test case generation.")
        return []
    
    print("@ Calling Gemini API to generate test cases...")  # Debugging
    
    # Set up the Gemini API key
    genai.configure(api_key='')  # Replace with your actual Gemini API key

    # Define the prompt
    prompt = f"""
    You are as a professional QA Engineer or Senio QA Engineer. Based on the following requirement, generate test cases in JSON format.

    Requirement:
    {plain_test_requirement}

    **Output format (JSON only, no explanations):**
    ```json
    [
        {{
            "Test Case ID": "TC_0001",
            "Description": "Describe the test case",
            "Preconditions": ["List preconditions"],
            "Steps": ["Step 1", "Step 2"],
            "Expected Results": ["Expected result"]
        }},
        ...
    ]
    ```
    Generate **up to 50 test cases** in this JSON format include explanation the description. Only return the JSON output.
    """
    print("🤖 Generating test cases from AI...")

    try:
        # Call the Gemini API
        model = genai.GenerativeModel('gemini-1.5-flash')  # Use the Gemini Pro model
        response = model.generate_content(prompt)

        # Extract the generated text from the response
        ai_output = response.text.strip()
        print("🤖 Gemini Response:", ai_output)  # Debugging

        # Pastikan AI tidak mengembalikan output kosong
        if not ai_output:
            print("❌ ERROR: AI response is empty.")
            return []

        # Perbaiki jika output masih mengandung kode markdown
        if ai_output.startswith("```json"):
            ai_output = ai_output[7:-3].strip()  # Hilangkan ```json ... ```

        # Coba parsing JSON
        try:
            test_cases = json.loads(ai_output)
        except json.JSONDecodeError as e:
            print(f"❌ ERROR: Gagal parsing test_cases JSON: {e}")
            print(f"🔍 AI Output:\n{ai_output}")  # Debugging untuk melihat kesalahan
            return []

        # Pastikan output berbentuk list of dict
        if not isinstance(test_cases, list) or not all(isinstance(tc, dict) for tc in test_cases):
            print("❌ ERROR: AI output bukan list of dict.")
            return []

        print(f"✅ {len(test_cases)} test cases generated successfully.")
        write_to_google_sheets("Nama Spreadsheet", test_cases)  # Write to Google Sheets

        return test_cases

    except Exception as e:
        print(f"❌ ERROR: Gagal menghasilkan test cases dari AI: {e}")
        return []

def parse_ai_response(generated_text):
    """Parse the AI response into a structured dictionary."""
    test_case = {}
    lines = generated_text.split("\n")
    current_section = None

    for line in lines:
        if line.startswith("**Test Case ID:**"):
            test_case["Test Case ID"] = line.split(":")[1].strip()
        elif line.startswith("**Description:**"):
            test_case["Description"] = line.split(":")[1].strip()
        elif line.startswith("**Preconditions:**"):
            current_section = "Preconditions"
            test_case["Preconditions"] = []
        elif line.startswith("**Steps:**"):
            current_section = "Steps"
            test_case["Steps"] = []
        elif line.startswith("**Expected Results:**"):
            current_section = "Expected Results"
            test_case["Expected Results"] = []
        elif line.strip().startswith("*") or line.strip().startswith("1."):
            if current_section:
                test_case[current_section].append(line.strip())

    return test_case

def process_test_cases(ai_output):
    try:
        test_cases = json.loads(ai_output)  # Parsing JSON string ke Python list
        if not isinstance(test_cases, list):  
            print(f"❌ ERROR: JSON tidak valid. Harusnya list of dictionaries, tapi dapat {type(test_cases)}")
            return []
        return test_cases[:50]  # Pastikan maksimal 50 test cases
    except json.JSONDecodeError as e:
        print(f"❌ ERROR: Gagal parsing test_cases JSON: {e}")
        return []        
    
def write_to_google_sheets(sheet_name, test_case):
    """Write test cases to Google Sheets."""
    try:
        # Authenticate with Google Sheets API
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)

        # Open or create the spreadsheet
        try:
            sheet = client.open(sheet_name).sheet1
        except gspread.SpreadsheetNotFound:
            print(f"⚠️ Spreadsheet '{sheet_name}' tidak ditemukan. Membuat yang baru...")
            sheet = client.create(sheet_name).sheet1

        # Add headers only if the sheet is empty
        if not sheet.get_all_values():
            print("📌 Menambahkan header ke Google Sheets...")
            sheet.append_row(["Test Case ID", "Description", "Preconditions", "Steps", "Expected Results", "Status Result"])

        # Prepare the rows for Google Sheets
        rows = []
        for idx, tc in enumerate(test_cases[:50], start=1):
            row = [
                tc.get("Test Case ID", f"TC_{idx:04d}"),
                tc.get("Description", ""),
                ", ".join(tc.get("Preconditions", [])),  # Jika None, gunakan default []
                ", ".join(tc.get("Steps", [])),
                ", ".join(tc.get("Expected Results", [])),
                "Pending"  # Default status result
            ]
            rows.append(row)

        # Write to Google Sheets
        sheet.append_rows(rows)
        print(f"✅ {len(rows)} test cases berhasil ditulis ke Google Sheets!")

    except Exception as e:
        print(f"❌ ERROR: Gagal menulis ke Google Sheets: {e}")
        

def test_google_sheets():
    try:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet_list = client.openall()
        print("✅ Available Spreadsheets:", [s.title for s in spreadsheet_list])
        sheet = client.open("ratika").sheet1
        print("✅ Successfully accessed Google Sheets!")
    except Exception as e:
        print(f"❌ Error accessing Google Sheets: {e}")
        
def main():
    print("🚀 Mengambil requirement dari Confluence...")
    requirement_text = fetch_confluence_page(CONFLUENCE_PAGE_ID)

    if requirement_text:
        print("✅ Requirement berhasil diambil! Generating test cases...")
        test_cases = generate_test_cases(requirement_text)  # Pastikan fungsi ini sudah ada
        write_to_google_sheets("ratika", test_cases)
    else:
        print("❌ ERROR: Requirement tidak ditemukan atau gagal diambil.")        
        
        
if __name__ == "__main__":
    print("🚀 Starting requirement retrieval...")

    # Define JIRA ID or Confluence Page ID
    jira_id = "MER-895"  # Replace with your JIRA ID
    # confluence_page_id = "3578953729"  # Replace with your Confluence Page ID

    # Fetch requirement from JIRA or Confluence
    requirement_text = None
    if jira_id:
        print(f"📌 Fetching requirement from JIRA ID: {jira_id}...")
        requirement_text = fetch_jira_requirement(jira_id)  # Fetch from JIRA
    
    # if not requirement_text and confluence_page_id:
    #     print(f"📌 Fetching requirement from Confluence Page ID: {confluence_page_id}...")
    #     requirement_text = fetch_confluence_page(confluence_page_id)  # Fetch from Confluence
    
    # Validate requirement retrieval
    if requirement_text:
        print("✅ Requirement successfully retrieved!")

        # Generate test cases
        test_cases = generate_test_cases(requirement_text)

        # Ensure test cases are generated
        if test_cases:
            print(f"✅ {len(test_cases)} test cases generated successfully.")

            # Write to Google Sheets
            write_to_google_sheets("", test_cases)
            print("✅ Test cases successfully written to Google Sheets!")
        else:
            print("❌ ERROR: Test cases generation failed.")
    else:
        print("⚠️ No requirement found from JIRA or Confluence. Exiting.")
