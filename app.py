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

load_dotenv()  # Load from .env file

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
#creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

client = gspread.authorize(creds)
drive_service = build('drive', 'v3', credentials=creds)

# Target sheet
dest_sheet = client.open("Online Clients Weight Analysis NEW (Responses)").worksheet("Image Data")


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
        raise Exception(f"Drive download failed: {str(e)}")


def extract_text_from_drive_link(image_url):
    try:
        # Extract file ID
        if "/d/" in image_url:
            file_id = image_url.split("/d/")[1].split("/")[0]
        elif "id=" in image_url:
            file_id = image_url.split("id=")[1].split("&")[0]
        else:
            return {"error": "Invalid Google Drive link format", "url": image_url}

        # Download and OCR the image
        img = download_image_from_drive(file_id)
        text = pytesseract.image_to_string(img)

        # Structure using OpenAI
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

        # Clean possible code block formatting
        if ai_response.startswith("```"):
            ai_response = ai_response.strip("`").split("\n", 1)[1].rsplit("\n", 1)[0]

        try:
            structured_data = json.loads(ai_response)
            return structured_data
        except json.JSONDecodeError:
            return {"error": "Failed to parse cleaned AI response", "raw": ai_response}

    except Exception as e:
        return {"error": str(e)}


@app.route("/")
def home():
    return "Flask app is working!"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    image_url = data.get("image_url")
    name = data.get("name")

    if image_url:
        extracted_text = extract_text_from_drive_link(image_url)
        row = [name, json.dumps(extracted_text)]
        #dest_sheet.append_row(row)
        return jsonify({"status": "success", "text": extracted_text})
    else:
        return jsonify({"status": "error", "message": "Missing image_url"}), 400


if __name__ == "__main__":
    app.run('0.0.0.0')
