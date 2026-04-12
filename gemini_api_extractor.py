"""
gemini_api_extractor.py — Structured Data Extraction via Gemini API

Based on the proven Obesity Spine extraction agent with added:
  - Rich terminal UI with progress bar and ETA
  - API key rotation for quota management
  - Auto-detection of template files
  - google.genai SDK (new) with google.generativeai (old) fallback
  - Empty row protection
"""

import os
import sys
import time
import json
import glob
import warnings
import pandas as pd
import argparse
from datetime import datetime

# Suppress pandas FutureWarning
warnings.filterwarnings('ignore', category=FutureWarning, module='pandas')

# Force UTF-8 on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Try new SDK first, fall back to old
try:
    from google import genai as new_genai
    from google.genai import types as genai_types
    SDK = "new"
except ImportError:
    new_genai = None
    SDK = "old"

try:
    import google.generativeai as old_genai
    from google.api_core import exceptions as gapi_exceptions
except ImportError:
    old_genai = None
    if SDK != "new":
        print("ERROR: Neither google-genai nor google-generativeai is installed.")
        print("  pip install google-genai")
        sys.exit(1)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.text import Text
from rich import box

console = Console(force_terminal=True)

from template_parser import parse_template, get_field_names

# Configuration
ARTICLES_DIR = 'Articles'
OUTPUT_FILE = 'extracted_studies_api.xlsx'


def auto_detect_template():
    """Auto-detect a template file (.docx/.xlsx) in the current directory."""
    candidates = []
    for ext in ['*.docx', '*.xlsx']:
        for f in glob.glob(ext):
            if 'template' in f.lower():
                candidates.append(f)
    if not candidates:
        return None
    docx_files = [f for f in candidates if f.endswith('.docx')]
    return docx_files[0] if docx_files else candidates[0]


DEFAULT_TEMPLATE = auto_detect_template()

# Globals
TEMPLATE_FIELDS = None
ALL_COLUMNS = None
API_KEYS = []
CURRENT_KEY_INDEX = 0

# SDK-specific globals
_new_client = None
_old_configured = False


def _init_sdk(key_index=0):
    """Initialize the SDK with the given key index."""
    global _new_client, _old_configured, CURRENT_KEY_INDEX
    CURRENT_KEY_INDEX = key_index
    key = API_KEYS[key_index]
    
    if SDK == "new":
        _new_client = new_genai.Client(api_key=key)
    else:
        old_genai.configure(api_key=key)
        _old_configured = True


def rotate_key():
    """Switch to the next API key."""
    global CURRENT_KEY_INDEX
    if len(API_KEYS) <= 1:
        return False
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEYS)
    _init_sdk(CURRENT_KEY_INDEX)
    console.print(f"    [dim]↻ Rotated to key {CURRENT_KEY_INDEX + 1}/{len(API_KEYS)}[/dim]")
    return True


def load_template(template_path):
    """Load template and set global field variables."""
    global TEMPLATE_FIELDS, ALL_COLUMNS
    TEMPLATE_FIELDS = parse_template(template_path)
    ALL_COLUMNS = get_field_names(TEMPLATE_FIELDS)


def create_prompt():
    """Create extraction prompt from loaded template fields."""
    if TEMPLATE_FIELDS is None:
        raise ValueError("Template not loaded.")
    
    prompt = "You are an expert scientific researcher. Extract the following information from the attached PDF study.\n"
    prompt += "Return the result as a valid JSON object where keys are the 'Field Name' and values are the extracted text/numbers. If information is strictly missing, use null.\n"
    prompt += "Do not hallucinate data. If you are unsure, extraction is better left as null.\n\n"
    
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
    return text.strip()


def _extract_new_sdk(pdf_path, prompt):
    """Extract using new google.genai SDK."""
    fname = os.path.basename(pdf_path)
    
    uploaded = _new_client.files.upload(file=pdf_path)
    
    # Wait for processing
    wait = 0
    while uploaded.state == "PROCESSING":
        time.sleep(2)
        uploaded = _new_client.files.get(name=uploaded.name)
        wait += 1
        if wait > 60:
            return None, "Processing timed out"
    
    response = _new_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            genai_types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type),
            prompt
        ],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
        )
    )
    
    try:
        _new_client.files.delete(name=uploaded.name)
    except:
        pass
    
    return response.text, None


def _extract_old_sdk(pdf_path, prompt):
    """Extract using old google.generativeai SDK (proven working)."""
    fname = os.path.basename(pdf_path)
    
    sample_file = old_genai.upload_file(path=pdf_path, display_name=fname)
    
    # Wait for processing
    while sample_file.state.name == "PROCESSING":
        time.sleep(1)
        sample_file = old_genai.get_file(sample_file.name)
    
    if sample_file.state.name == "FAILED":
        return None, "File processing failed"
    
    model = old_genai.GenerativeModel("models/gemini-2.0-flash")
    generation_config = old_genai.GenerationConfig(
        response_mime_type="application/json"
    )
    
    response = model.generate_content(
        [sample_file, prompt],
        generation_config=generation_config
    )
    
    try:
        old_genai.delete_file(sample_file.name)
    except:
        pass
    
    return response.text, None


def extract_study(pdf_path, prompt):
    """Upload PDF and extract data. Returns (data_dict, error_string)."""
    fname = os.path.basename(pdf_path)
    
    try:
        # Use whichever SDK is available
        if SDK == "new":
            raw_text, err = _extract_new_sdk(pdf_path, prompt)
        else:
            raw_text, err = _extract_old_sdk(pdf_path, prompt)
        
        if err:
            return None, err
        
        # Parse JSON
        text = clean_json_string(raw_text)
        data = json.loads(text)
        
        # Handle array response
        if isinstance(data, list):
            if len(data) > 0:
                data = data[0]
            else:
                return None, "Empty array returned"
        
        # Validate: must have real extracted content
        real_fields = {k: v for k, v in data.items() if v is not None and str(v).strip()}
        if len(real_fields) < 3:
            return None, f"Only {len(real_fields)} non-null fields extracted"
        
        data['Source File'] = fname
        return data, None
        
    except json.JSONDecodeError:
        return None, f"Invalid JSON: {raw_text[:150] if raw_text else 'empty'}..."
    except Exception as e:
        error_msg = str(e)
        if '429' in error_msg or 'RESOURCE_EXHAUSTED' in error_msg:
            if rotate_key():
                return "RETRY", "Quota → rotated key"
            else:
                time.sleep(60)
                return "RETRY", "All keys exhausted, waited 60s"
        elif any(kw in error_msg.lower() for kw in ['connection', 'timeout', 'timed out', 'unavailable']):
            time.sleep(10)
            return "RETRY", "Network error"
        else:
            return None, error_msg


def main(api_keys_input, limit=None, template_path=None):
    global API_KEYS
    
    start_time = datetime.now()
    
    # Parse API keys
    API_KEYS = [k.strip() for k in api_keys_input.split(',') if k.strip()]
    _init_sdk(0)
    
    # Banner
    console.print()
    console.print(Panel(
        Text("STRUCTURED DATA EXTRACTION PIPELINE", style="bold white", justify="center"),
        border_style="cyan", box=box.DOUBLE_EDGE, padding=(1, 4),
        subtitle=f"Gemini API · {SDK} SDK", subtitle_align="center"
    ))
    console.print()
    
    # Load Template
    if template_path is None:
        template_path = DEFAULT_TEMPLATE
    if template_path is None:
        console.print("[bold red]✘[/bold red] No template file found.")
        console.print("  Place a .docx file with 'template' in the name here, or use --template")
        return
    
    try:
        load_template(template_path)
    except Exception as e:
        console.print(f"[bold red]✘[/bold red] Error loading template: {e}")
        return

    # Get Files
    if not os.path.exists(ARTICLES_DIR):
        console.print(f"[bold red]✘[/bold red] Directory '{ARTICLES_DIR}' does not exist.")
        return

    pdf_files = sorted([
        os.path.join(ARTICLES_DIR, f) 
        for f in os.listdir(ARTICLES_DIR) 
        if f.lower().endswith('.pdf')
    ])
    
    # Filter processed — only count rows with actual data
    processed_files = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_excel(OUTPUT_FILE)
            if 'Source File' in df_existing.columns:
                data_cols = [c for c in df_existing.columns if c != 'Source File']
                has_data = df_existing[data_cols].notna().any(axis=1)
                empty_count = (~has_data).sum()
                if empty_count > 0:
                    df_existing = df_existing[has_data]
                    df_existing.to_excel(OUTPUT_FILE, index=False)
                    console.print(f"  [yellow]⚠[/yellow] Cleaned {empty_count} empty rows from {OUTPUT_FILE}")
                processed_files = set(df_existing['Source File'].astype(str).tolist())
        except:
            pass

    files_to_process = [f for f in pdf_files if os.path.basename(f) not in processed_files]
    if limit:
        files_to_process = files_to_process[:int(limit)]

    # Config Table
    config_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    config_table.add_column(style="dim")
    config_table.add_column(style="bold")
    config_table.add_row("Template", str(template_path))
    config_table.add_row("SDK", f"google.{'genai' if SDK == 'new' else 'generativeai'}")
    config_table.add_row("API Keys", f"{len(API_KEYS)} key(s)")
    config_table.add_row("PDFs", f"{len(files_to_process)} to process ({len(processed_files)} already done)")
    config_table.add_row("Fields", f"{len(TEMPLATE_FIELDS)} fields per study")
    console.print(Panel(config_table, title="[bold]Configuration", border_style="dim"))
    console.print()

    if not files_to_process:
        console.print("[green]✔[/green] All studies already extracted. Nothing to do.")
        return

    prompt = create_prompt()
    results = []
    failures = []

    # Main extraction loop
    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=35, style="cyan", complete_style="green"),
        TaskProgressColumn(),
        TextColumn("·"),
        TimeElapsedColumn(),
        TextColumn("·"),
        TimeRemainingColumn(),
        console=console, expand=False,
    ) as progress:
        
        total = len(files_to_process)
        task = progress.add_task("Starting...", total=total)
        
        for i, pdf_path in enumerate(files_to_process):
            fname = os.path.basename(pdf_path)
            short = fname[:30] + "..." if len(fname) > 33 else fname
            progress.update(task, description=f"[bold]Study {i+1}/{total}[/bold] [cyan]· {short}")
            
            max_retries = 5
            success = False
            
            for attempt in range(max_retries):
                data, error = extract_study(pdf_path, prompt)
                
                if data == "RETRY":
                    continue
                
                if data and isinstance(data, dict):
                    results.append(data)
                    
                    # Save incrementally
                    df = pd.DataFrame([data])
                    for c in ALL_COLUMNS:
                        if c not in df.columns:
                            df[c] = None
                    
                    cols = ['Source File'] + [c for c in ALL_COLUMNS if c in df.columns]
                    df = df[cols]
                    
                    if os.path.exists(OUTPUT_FILE):
                        existing = pd.read_excel(OUTPUT_FILE)
                        existing = existing.loc[:, ~existing.columns.duplicated()]
                        df = df.loc[:, ~df.columns.duplicated()]
                        existing = existing.dropna(axis=1, how='all')
                        df = pd.concat([existing, df], ignore_index=True)
                    
                    df.to_excel(OUTPUT_FILE, index=False)
                    success = True
                    time.sleep(4)  # Rate limit safety
                    break
                else:
                    if attempt < max_retries - 1:
                        time.sleep(2)
            
            if not success:
                failures.append((fname, error or "Unknown error"))
            
            progress.update(task, advance=1)

    # Summary
    elapsed = (datetime.now() - start_time).total_seconds()
    console.print()
    
    stats = Table(box=box.ROUNDED, border_style="cyan", title="Extraction Results", title_style="bold cyan")
    stats.add_column("Metric", style="white")
    stats.add_column("Value", style="bold", justify="right")
    stats.add_row("Total PDFs", str(len(files_to_process)))
    stats.add_row("Extracted", f"[bold green]{len(results)}")
    stats.add_row("Failed", f"[bold red]{len(failures)}" if failures else "[green]0")
    stats.add_row("Previously Done", f"[dim]{len(processed_files)}")
    stats.add_row("Time Elapsed", f"{elapsed:.0f}s ({elapsed/60:.1f} min)")
    if results:
        stats.add_row("Avg per Study", f"{elapsed/len(results):.1f}s")
    console.print(stats)
    console.print()

    files_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    files_table.add_column(style="cyan")
    files_table.add_column(style="dim")
    files_table.add_row(OUTPUT_FILE, f"{len(results) + len(processed_files)} total rows")
    console.print(Panel(files_table, title="[bold]Output Files", border_style="green"))

    if failures:
        console.print()
        fail_table = Table(box=box.SIMPLE, border_style="red", title="Failed Extractions", title_style="bold red")
        fail_table.add_column("#", style="dim", width=3)
        fail_table.add_column("File", style="white")
        fail_table.add_column("Error", style="dim")
        for i, (f, err) in enumerate(failures, 1):
            fail_table.add_row(str(i), f, str(err)[:60])
        console.print(fail_table)

    console.print()
    console.print(Panel(
        Text("Extraction Complete", style="bold green", justify="center"),
        border_style="green", box=box.DOUBLE_EDGE,
        subtitle=f"{len(results)} extracted · {len(failures)} failed · {elapsed:.0f}s",
        subtitle_align="center"
    ))
    console.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract data using Gemini API")
    parser.add_argument("--key", help="Gemini API Key(s), comma-separated for rotation", required=True)
    parser.add_argument("--template", help="Path to template file", default=DEFAULT_TEMPLATE)
    parser.add_argument("--limit", help="Limit number of files", default=None)
    args = parser.parse_args()
    
    main(args.key, args.limit, args.template)
