<p align="center">
  <h1 align="center">Systematic Review Extraction Agent</h1>
  <p align="center">
    AI-powered structured data extraction from scientific PDFs.<br>
    Upload your template, point to your articles, get a complete dataset.
  </p>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#output">Output</a> •
  <a href="#advanced">Advanced</a> •
  <a href="#contributing">Contributing</a>
</p>

---

> Part of the **Agentic AI-Powered Systematic Review Pipeline**
>
> [Deduplication Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_DeDuplication_agent) →
> [Screening Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_screening_agent) →
> **Extraction Agent** →
> [Validation Agent](https://github.com/ORG-Karur-DataCenter/Sys_review_extraction_validation_agent)

---

## Quickstart

### 1. Install

```bash
git clone https://github.com/ORG-Karur-DataCenter/Systematic_review_extraction_agent.git
cd Systematic_review_extraction_agent
pip install -r requirements.txt
playwright install chromium
```

### 2. Prepare Your Inputs

| Input | Description |
|-------|-------------|
| `Articles/` folder | Place all your included PDFs here |
| Template file | `.xlsx` or `.docx` defining the fields to extract |

### 3. Run

**Browser mode (default — free, no API key):**
```bash
python gemini_extractor.py --browser chrome
```

A browser window opens, you log in to Gemini once, and PDFs are processed automatically.

**API mode (faster):**
```bash
python gemini_extractor.py --api-key YOUR_KEY
```

No browser needed. Each PDF is uploaded via API and extracted in ~10 seconds.

---

## How It Works

```
                    ┌──────────────────────────┐
                    │   gemini_extractor.py     │
                    └────────┬─────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌───────────┐  ┌──────────┐
        │ Step 1   │  │  Step 2   │  │ Step 3   │
        │ Parse    │  │ Upload    │  │ Post-    │
        │ template │  │ PDF to    │  │ process  │
        │ schema   │  │ Gemini    │  │ & save   │
        │ (.xlsx)  │  │ + extract │  │ (.xlsx)  │
        └──────────┘  └───────────┘  └──────────┘
```

### Step 1 — Parse Template
Reads your `.xlsx` or `.docx` template and extracts all field names, sections, and descriptions. This defines the extraction schema.

### Step 2 — AI Extraction
For each PDF in `Articles/`:
1. Uploads the PDF to Gemini (via browser or API)
2. Sends the extraction prompt with all field definitions
3. Receives structured JSON with extracted values
4. Validates field completeness — logs any missing fields

### Step 3 — Post-Processing
Two automatic transformations:

**Percentage-to-Count Conversion:**
```
"30%" + Sample Size 100 → "30/100 (30%)"
```
Uses deterministic formula: `count = round(pct × n / 100)`

**Missing Data Justification:**
```json
{"Study_ID": {"BMI": "Not reported in study", "Follow-up": "Only in-hospital outcomes"}}
```
Every null field gets an AI-generated reason saved to `missing_data_justifications.json`.

---

## Extraction Schema

The template covers 50+ fields across these categories:

| Category | Example Fields |
|----------|---------------|
| **Study Characteristics** | Study ID, Journal, Country, Study Design, Database |
| **Demographics** | Age (mean ± SD), Sex distribution, BMI |
| **Intervention** | Drug/Procedure, Dose, Duration, Comparator |
| **Outcomes** | Primary/Secondary Outcomes, Follow-up Duration |
| **Comorbidities** | HTN, DM, CKD, CVD, Obesity |
| **Quality** | Risk of Bias, Newcastle-Ottawa Scale, Jadad Score |

You can use any template — the system adapts to whatever fields you define.

---

## Output

| File | Description |
|------|-------------|
| `extracted_studies.xlsx` | Complete extraction dataset (one row per study) |
| `missing_data_justifications.json` | Per-field null reasons for every study |
| `extraction_summary.json` | Run summary (files processed, success/fail counts) |

### Resume Support

If the pipeline is interrupted, re-running it will **skip already-processed PDFs** and continue from where it left off. No duplicate work.

---

## Advanced

### Command-Line Options

```
python gemini_extractor.py --help

Options:
  --browser CHANNEL   Browser for extraction (chrome, msedge)   [default]
  --api-key KEY       Gemini API key (faster, no browser)
  --template FILE     Path to template (.xlsx or .docx)
  --limit N           Process only first N PDFs
```

### API Mode with Key Rotation

For large datasets, provide multiple keys to avoid rate limits:

```bash
python gemini_extractor.py --api-kit API_KIT.txt
```

`API_KIT.txt` — one key per line:
```
AIzaSyABC...
AIzaSyDEF...
AIzaSyGHI...
```

On rate limit (429), the pipeline automatically switches to the next key.

### Verify API Connection

```bash
python test_connection.py
python check_models.py
```

### Inspect Template Fields

```bash
python inspect_template.py
```

---

## Project Structure

```
extraction_agent/
├── gemini_extractor.py            # Main extractor (browser + API modes)
├── gemini_api_extractor.py        # API-only extractor wrapper
├── template_parser.py             # Template schema reader (.xlsx/.docx)
├── inspect_template.py            # Debug/inspect template fields
├── check_models.py                # List available Gemini models
├── test_connection.py             # Verify API key and model access
├── requirements.txt               # Dependencies
├── LICENSE                        # MIT License
├── Articles/                      # Place PDFs here (gitignored)
└── *.xlsx / *.docx                # Extraction templates
```

---

## API Configuration

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `temperature` | `0.2` | Deterministic, reproducible extraction |
| `max_output_tokens` | `8192` | Full schema JSON without truncation |
| Model | `gemini-2.5-flash` | Fast, capable |

---

## Modes

| Mode | Command | Speed | Cost |
|------|---------|-------|------|
| **Browser** (default) | `python gemini_extractor.py --browser chrome` | ~30s/PDF | Free |
| **API** | `python gemini_extractor.py --api-key KEY` | ~10s/PDF | Free tier |
| **API + rotation** | `python gemini_extractor.py --api-kit API_KIT.txt` | ~10s/PDF | Free tier × N keys |

---

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built for systematic reviewers. PDFs in, structured data out.</sub>
</p>
