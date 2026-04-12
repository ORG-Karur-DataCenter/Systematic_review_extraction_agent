import os
import sys
import time
import json
import glob
import warnings
import pandas as pd
import argparse

# Suppress pandas FutureWarning about empty/NA concat
warnings.filterwarnings('ignore', category=FutureWarning, module='pandas')
from datetime import datetime
from google import genai
from google.genai import types
from template_parser import parse_template, get_field_names

# Force UTF-8 on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.text import Text
from rich.live import Live
from rich import box

console = Console(force_terminal=True)

# Configuration
ARTICLES_DIR = 'Articles'
OUTPUT_FILE = 'extracted_studies_api.xlsx'
MODEL_NAME = "gemini-2.5-flash"


def auto_detect_template():
    """Auto-detect a template file (.docx) in the current directory."""
    candidates = []
    for ext in ['*.docx', '*.xlsx']:
        for f in glob.glob(ext):
            if 'template' in f.lower():
                candidates.append(f)
    
    if not candidates:
        return None
    
    docx_files = [f for f in candidates if f.endswith('.docx')]
    if docx_files:
        return docx_files[0]
    return candidates[0]


DEFAULT_TEMPLATE = auto_detect_template()

# Global variables
TEMPLATE_FIELDS = None
ALL_COLUMNS = None
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
        raise ValueError("Template not loaded. Call load_template() first.")
    
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
    text = text.strip()
    return text


def extract_study_with_api(pdf_path, prompt):
    """Uploads file and extracts data using Gemini API."""
    fname = os.path.basename(pdf_path)
    
    try:
        uploaded_file = client.files.upload(file=pdf_path)
        
        wait_count = 0
        while uploaded_file.state == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
            wait_count += 1
            if wait_count > 60:
                return None, "Processing timed out"

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
        
        try:
            client.files.delete(name=uploaded_file.name)
        except:
            pass

        try:
            text = clean_json_string(response.text)
            data = json.loads(text)
            if isinstance(data, list):
                if len(data) > 0:
                    data = data[0]
                else:
                    return None, "Empty array"
            data['Source File'] = fname
            return data, None
        except json.JSONDecodeError:
            return None, f"Invalid JSON: {response.text[:100]}..."
            
    except Exception as e:
        error_msg = str(e)
        if '429' in error_msg or 'RESOURCE_EXHAUSTED' in error_msg:
            if rotate_key():
                return "RETRY", "Quota exceeded → rotated key"
            else:
                time.sleep(60)
                return "RETRY", "All keys exhausted, waited 60s"
        elif any(kw in error_msg for kw in ['ConnectionError', 'TimeoutError', 'timed out', 'UNAVAILABLE']):
            time.sleep(10)
            return "RETRY", f"Network error"
        else:
            return None, str(e)


def main(api_keys_input, limit=None, template_path=None):
    global API_KEYS, CURRENT_KEY_INDEX, client
    
    start_time = datetime.now()
    
    # Parse API keys
    API_KEYS = [k.strip() for k in api_keys_input.split(',') if k.strip()]
    CURRENT_KEY_INDEX = 0
    client = genai.Client(api_key=API_KEYS[0])
    
    # ── Banner ──
    console.print()
    console.print(Panel(
        Text("STRUCTURED DATA EXTRACTION PIPELINE", style="bold white", justify="center"),
        border_style="cyan", box=box.DOUBLE_EDGE, padding=(1, 4),
        subtitle="Gemini API · google.genai SDK", subtitle_align="center"
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

    pdf_files = sorted([os.path.join(ARTICLES_DIR, f) for f in os.listdir(ARTICLES_DIR) if f.lower().endswith('.pdf')])
    
    # Filter processed — only count rows that have actual data (not just Source File)
    processed_files = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_excel(OUTPUT_FILE)
            if 'Source File' in df_existing.columns:
                # A row counts as "processed" only if it has data beyond Source File
                data_cols = [c for c in df_existing.columns if c != 'Source File']
                has_data = df_existing[data_cols].notna().any(axis=1)
                
                # Remove empty rows from the file
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

    # ── Config Table ──
    config_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    config_table.add_column(style="dim")
    config_table.add_column(style="bold")
    config_table.add_row("Template", str(template_path))
    config_table.add_row("Model", MODEL_NAME)
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

    # ── Main Extraction Loop with Progress ──
    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=35, style="cyan", complete_style="green"),
        TaskProgressColumn(),
        TextColumn("·"),
        TimeElapsedColumn(),
        TextColumn("·"),
        TimeRemainingColumn(),
        console=console,
        expand=False,
    ) as progress:
        
        total = len(files_to_process)
        task = progress.add_task("Extracting studies...", total=total)
        
        for i, pdf_path in enumerate(files_to_process):
            fname = os.path.basename(pdf_path)
            short_name = fname[:30] + "..." if len(fname) > 33 else fname
            progress.update(task, description=f"[bold]Study {i+1}/{total}[/bold] [cyan]· {short_name}")
            
            max_retries = 5
            success = False
            
            for attempt in range(max_retries):
                data, error = extract_study_with_api(pdf_path, prompt)
                
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
                        existing = existing.loc[:, ~existing.columns.duplicated()]
                        df = df.loc[:, ~df.columns.duplicated()]
                        # Drop all-NA columns before concat to avoid FutureWarning
                        existing = existing.dropna(axis=1, how='all')
                        df = pd.concat([existing, df], ignore_index=True)
                    
                    df.to_excel(OUTPUT_FILE, index=False)
                    success = True
                    time.sleep(4)
                    break
                else:
                    if attempt < max_retries - 1:
                        time.sleep(2)
            
            if not success:
                failures.append(fname)
            
            progress.update(task, advance=1)

    # ── Summary ──
    elapsed = (datetime.now() - start_time).total_seconds()
    console.print()
    
    # Results table
    stats = Table(box=box.ROUNDED, border_style="cyan", title="Extraction Results", title_style="bold cyan")
    stats.add_column("Metric", style="white")
    stats.add_column("Value", style="bold", justify="right")
    stats.add_row("Total PDFs", str(len(files_to_process)))
    stats.add_row("Extracted", f"[bold green]{len(results)}")
    stats.add_row("Failed", f"[bold red]{len(failures)}" if failures else "[green]0")
    stats.add_row("Previously Done", f"[dim]{len(processed_files)}")
    stats.add_row("Time Elapsed", f"{elapsed:.0f}s ({elapsed/60:.1f} min)")
    if results:
        avg_time = elapsed / len(results)
        stats.add_row("Avg per Study", f"{avg_time:.1f}s")
    console.print(stats)
    console.print()

    # Output files
    files_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    files_table.add_column(style="cyan")
    files_table.add_column(style="dim")
    files_table.add_row(OUTPUT_FILE, f"{len(results) + len(processed_files)} total rows")
    console.print(Panel(files_table, title="[bold]Output Files", border_style="green"))

    # Show failures if any
    if failures:
        console.print()
        fail_table = Table(box=box.SIMPLE, border_style="red", title="Failed Extractions", title_style="bold red")
        fail_table.add_column("#", style="dim", width=3)
        fail_table.add_column("File", style="white")
        for i, f in enumerate(failures, 1):
            fail_table.add_row(str(i), f)
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
