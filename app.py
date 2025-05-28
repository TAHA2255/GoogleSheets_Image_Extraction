from flask import Flask, request, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pytesseract
from PIL import Image
import openai
import json
from dotenv import load_dotenv
import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
import fitz  # PyMuPDF for PDF handling
import re

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")
creds_json = os.getenv("GOOGLE_CREDENTIALS")

app = Flask(__name__)

# Google Sheets + Drive setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

if creds_json:
    creds_dict = json.loads(creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
else:
    raise Exception("Missing GOOGLE_CREDENTIALS environment variable")

client = gspread.authorize(creds)
drive_service = build('drive', 'v3', credentials=creds)

# Sheets
image_sheet = client.open("Online Clients Weight Analysis NEW (Responses)").worksheet("Image Data")
pdf_sheet = client.open("Online Clients Weight Analysis NEW (Responses)").worksheet("Lab Reports")


# ========== IMAGE EXTRACTOR ==========

def download_image_from_drive(file_id):
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        fh.seek(0)
        img = Image.open(fh)
        return img
    except Exception as e:
        raise Exception(f"Drive image download failed: {str(e)}")


def extract_text_from_drive_link(image_url):
    try:
        if "/d/" in image_url:
            file_id = image_url.split("/d/")[1].split("/")[0]
        elif "id=" in image_url:
            file_id = image_url.split("id=")[1].split("&")[0]
        else:
            return {"error": "Invalid Google Drive link format", "url": image_url}

        img = download_image_from_drive(file_id)
        text = pytesseract.image_to_string(img)

        prompt = f"""
You are a medical assistant. Extract key patient data, vitals, diagnoses, test results, medications, and relevant structured info from this medical text. Return the result as JSON like: {{"data": ...}}.

Medical Text:
{text}
        """

        completion = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a medical information extractor."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        ai_response = completion['choices'][0]['message']['content']

        if ai_response.startswith("```"):
            ai_response = ai_response.strip("`").split("\n", 1)[1].rsplit("\n", 1)[0]

        try:
            return json.loads(ai_response)
        except json.JSONDecodeError:
            return {"error": "Failed to parse cleaned AI response", "raw": ai_response}

    except Exception as e:
        return {"error": str(e)}


# ========== PDF EXTRACTOR ==========


def extract_file_id(url):
    # Match /d/FILE_ID or id=FILE_ID
    patterns = [
        r"/d/([a-zA-Z0-9_-]+)",        # /d/{id}/
        r"id=([a-zA-Z0-9_-]+)",        # id={id}
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def extract_text_from_drive_pdf(pdf_url):
    try:
        
        file_id = extract_file_id(pdf_url)
        if not file_id:
            return {"error": "Invalid Google Drive link format", "url": pdf_url}
        # Download PDF from Google Drive
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        fh.seek(0)
        doc = fitz.open(stream=fh, filetype="pdf")

        extracted_text = ""
        for page in doc:
            extracted_text += page.get_text()

        # Send to OpenAI for structured lab result extraction
        prompt = f"""
You are a bilingual medical assistant. Read the following lab report and return a short summary of the most important results in JSON format:

{{
  "summary": {{
    "english": "...",
    "arabic": "..."
  }}
}}

Only return valid JSON. Do not wrap it in backticks or markdown code blocks.

Lab Report:
{extracted_text}
"""

        completion = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a medical lab report extractor."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        ai_response = completion['choices'][0]['message']['content']

        if ai_response.startswith("```"):
            ai_response = ai_response.strip("`").split("\n", 1)[1].rsplit("\n", 1)[0]

        try:
            return json.loads(ai_response)
        except json.JSONDecodeError:
            return {"error": "Failed to parse cleaned AI response", "raw": ai_response}

    except Exception as e:
        return {"error": str(e)}


# ========== ROUTES ==========

@app.route("/")
def home():
    return "Flask app is working!"


@app.route("/webhook", methods=["POST"])
def webhook_image():
    data = request.json
    image_url = data.get("image_url")
    name = data.get("name")

    if image_url:
        result = extract_text_from_drive_link(image_url)
        row = [name, json.dumps(result)]
        image_sheet.append_row(row)
        return jsonify({"status": "success", "text": result})
    else:
        return jsonify({"status": "error", "message": "Missing image_url"}), 400


@app.route("/webhook/pdf", methods=["POST"])
def webhook_pdf():
    data = request.json
    pdf_url = data.get("pdf_url")
    name = data.get("name")

    if pdf_url:
        result = extract_text_from_drive_pdf(pdf_url)
        row = [name, json.dumps(result)]
        pdf_sheet.append_row(row)
        return jsonify({"status": "success", "text": result})
    else:
        return jsonify({"status": "error", "message": "Missing pdf_url"}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run('0.0.0.0', port=5000)
