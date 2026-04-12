import os
import time
import json
import glob
import pandas as pd
import argparse
from google import genai
from google.genai import types
from template_parser import parse_template, get_field_names

# Configuration
ARTICLES_DIR = 'Articles'
OUTPUT_FILE = 'extracted_studies_api.xlsx'
MODEL_NAME = "gemini-2.0-flash"


def auto_detect_template():
    """Auto-detect a template file (.docx) in the current directory."""
    candidates = []
    for ext in ['*.docx', '*.xlsx']:
        for f in glob.glob(ext):
            if 'template' in f.lower():
                candidates.append(f)
    
    if not candidates:
        return None
    
    # Prefer .docx over .xlsx
    docx_files = [f for f in candidates if f.endswith('.docx')]
    if docx_files:
        return docx_files[0]
    return candidates[0]


DEFAULT_TEMPLATE = auto_detect_template()

# Global variable to store template fields
TEMPLATE_FIELDS = None
ALL_COLUMNS = None

# API Key Pool for rotation
API_KEYS = []
CURRENT_KEY_INDEX = 0
client = None


def rotate_key():
    """Switch to the next API key in the pool."""
    global CURRENT_KEY_INDEX, client
    if len(API_KEYS) <= 1:
        return False
    
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEYS)
    client = genai.Client(api_key=API_KEYS[CURRENT_KEY_INDEX])
    print(f"  Rotated to API key {CURRENT_KEY_INDEX + 1}/{len(API_KEYS)}")
    return True


def load_template(template_path):
    """Load template and set global field variables."""
    global TEMPLATE_FIELDS, ALL_COLUMNS
    print(f"Loading template: {template_path}")
    TEMPLATE_FIELDS = parse_template(template_path)
    ALL_COLUMNS = get_field_names(TEMPLATE_FIELDS)
    print(f"Loaded {len(TEMPLATE_FIELDS)} fields from template")

def create_prompt():
    """Create extraction prompt from loaded template fields."""
    if TEMPLATE_FIELDS is None:
        raise ValueError("Template not loaded. Call load_template() first.")
    
    prompt = "You are an expert scientific researcher. Extract the following information from the attached PDF study.\n"
    prompt += "Return the result as a valid JSON object where keys are the 'Field Name' and values are the extracted text/numbers. If information is strictly missing, use null.\n"
    prompt += "Do not hallucinate data. If you are unsure, extraction is better left as null.\n\n"
    
    # Group fields by section for better context
    sections = {}
    for field in TEMPLATE_FIELDS:
        section = field.section if field.section else "General"
        if section not in sections:
            sections[section] = []
        sections[section].append(field)
    
    for section_name, fields in sections.items():
        prompt += f"--- {section_name} ---\n"
        for field in fields:
            desc = f": {field.description}" if field.description else ""
            prompt += f"- {field.name}{desc}\n"
    
    prompt += "\nReturn ONLY the JSON object. No markdown formatting (like ```json), no preamble."
    return prompt

def clean_json_string(response_text):
    """Clean the response text to get valid JSON."""
    text = response_text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline+1:]
        if text.endswith("```"):
            text = text[:-3]
    text = text.strip()
    return text

def extract_study_with_api(pdf_path, prompt):
    """Uploads file and extracts data using Gemini API (google.genai SDK)."""
    fname = os.path.basename(pdf_path)
    print(f"[{fname}] Uploading to Gemini...")
    
    try:
        # Upload the file
        uploaded_file = client.files.upload(file=pdf_path)
        
        # Wait for processing
        wait_count = 0
        while uploaded_file.state == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
            wait_count += 1
            if wait_count > 60:
                print(f"[{fname}] Processing timed out. Skipping.")
                return None

        # Generate content
        print(f"[{fname}] Generating extraction...")
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type),
                prompt
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        # Clean up uploaded file
        try:
            client.files.delete(name=uploaded_file.name)
        except:
            pass

        # Parse Response
        try:
            text = clean_json_string(response.text)
            data = json.loads(text)
            # Handle array response — model sometimes returns [{}] instead of {}
            if isinstance(data, list):
                if len(data) > 0:
                    data = data[0]
                else:
                    print(f"[{fname}] Empty array returned.")
                    return None
            data['Source File'] = fname
            return data
        except json.JSONDecodeError:
            print(f"[{fname}] Error: Invalid JSON returned.")
            print(f"Debug Raw: {response.text[:200]}...")
            return None
            
    except Exception as e:
        error_msg = str(e)
        if '429' in error_msg or 'RESOURCE_EXHAUSTED' in error_msg:
            print(f"[{fname}] Quota exceeded (429).")
            if rotate_key():
                return "RETRY"
            else:
                print(f"  All keys exhausted. Waiting 60 seconds...")
                time.sleep(60)
                return "RETRY"
        elif any(kw in error_msg for kw in ['ConnectionError', 'TimeoutError', 'timed out', 'UNAVAILABLE']):
            print(f"[{fname}] Network error: {e}. Waiting 10s...")
            time.sleep(10)
            return "RETRY"
        else:
            print(f"[{fname}] API Error: {e}")
            return None

def main(api_keys_input, limit=None, template_path=None):
    global API_KEYS, CURRENT_KEY_INDEX, client
    
    # Parse API keys (comma-separated or single)
    API_KEYS = [k.strip() for k in api_keys_input.split(',') if k.strip()]
    CURRENT_KEY_INDEX = 0
    
    # Initialize client with first key
    client = genai.Client(api_key=API_KEYS[0])
    print(f"Initialized with {len(API_KEYS)} API key(s)")
    
    # Load Template
    if template_path is None:
        template_path = DEFAULT_TEMPLATE
    
    if template_path is None:
        print("ERROR: No template file found.")
        print("  Place a .docx file with 'template' in the name in the current directory,")
        print("  or specify one with --template path/to/template.docx")
        return
    
    print(f"Auto-detected template: {template_path}")
    
    try:
        load_template(template_path)
    except Exception as e:
        print(f"Error loading template: {e}")
        return

    # Get Files
    if not os.path.exists(ARTICLES_DIR):
        print(f"Error: Directory {ARTICLES_DIR} does not exist.")
        return

    pdf_files = sorted([os.path.join(ARTICLES_DIR, f) for f in os.listdir(ARTICLES_DIR) if f.lower().endswith('.pdf')])
    
    # Filter processed
    processed_files = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_excel(OUTPUT_FILE)
            if 'Source File' in df_existing.columns:
                processed_files = set(df_existing['Source File'].astype(str).tolist())
        except:
            pass

    files_to_process = [f for f in pdf_files if os.path.basename(f) not in processed_files]
    
    if limit:
        files_to_process = files_to_process[:int(limit)]

    print(f"Found {len(pdf_files)} total. {len(files_to_process)} to process.")
    
    prompt = create_prompt()
    results = []

    for pdf_path in files_to_process:
        max_retries = 5  # More retries since we have key rotation
        for attempt in range(max_retries):
            data = extract_study_with_api(pdf_path, prompt)
            
            if data == "RETRY":
                continue
            
            if data:
                results.append(data)
                
                # Save Incrementally
                df = pd.DataFrame([data])
                for c in ALL_COLUMNS:
                    if c not in df.columns: df[c] = None
                
                cols = ['Source File'] + [c for c in ALL_COLUMNS if c in df.columns]
                df = df[cols]
                
                if os.path.exists(OUTPUT_FILE):
                    existing = pd.read_excel(OUTPUT_FILE)
                    df = pd.concat([existing, df], ignore_index=True)
                
                df.to_excel(OUTPUT_FILE, index=False)
                print(f"✔ Saved {os.path.basename(pdf_path)} [{len(results)}/{len(files_to_process)}]")
                
                time.sleep(4)
                break
            else:
                print(f"Failed to extract {os.path.basename(pdf_path)} after attempt {attempt+1}")
                time.sleep(2)
        
    print(f"\nExtraction Complete. {len(results)}/{len(files_to_process)} studies extracted.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract data using Gemini API")
    parser.add_argument("--key", help="Gemini API Key(s), comma-separated for rotation", required=True)
    parser.add_argument("--template", help="Path to template file", default=DEFAULT_TEMPLATE)
    parser.add_argument("--limit", help="Limit number of files", default=None)
    args = parser.parse_args()
    
    main(args.key, args.limit, args.template)
