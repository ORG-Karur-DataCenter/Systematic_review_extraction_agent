# Systematic Review Extraction Agent

An AI-powered data extraction tool that reads scientific PDF articles and extracts structured research data using Google Gemini. Designed for systematic reviews and meta-analyses — runs free via browser automation or fast via API.

Part of the **Agentic AI-Powered Systematic Review Pipeline** described in:
> *"Agentic AI for Systematic Reviews: A Four-Agent Pipeline for Deduplication, Screening, Extraction, and Validation"*

**Related Repositories:**
- [Deduplication Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_DeDuplication_agent)
- [Screening Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_screening_agent)
- [Validation & Healing Agent](https://github.com/ORG-Karur-DataCenter/Sys_review_extraction_validation_agent)

---

## Features

- **Automated PDF extraction** — uploads PDFs to Gemini and extracts 50+ structured data fields
- **Comprehensive schema** — captures study design, sample size, demographics, interventions, outcomes, comorbidities
- **Missing data justification** — for every null field, records an AI-generated reason why (`missing_data_justifications.json`)
- **Deterministic percentage conversion** — converts outcome percentages to exact counts using `round(pct × n / 100)`
- **Cross-validation support** — extract with two LLM calls and compare for consensus
- **Free by default** — Playwright browser automation; no API costs
- **API mode** — add `--api-key` for speed; supports API key rotation for rate-limit bypass

---

## Installation

```bash
git clone https://github.com/ORG-Karur-DataCenter/Systematic_review_extraction_agent.git
cd Systematic_review_extraction_agent
pip install -r requirements.txt
playwright install chromium
```

---

## Usage

### Free Mode (Browser Automation)

```bash
python gemini_extractor.py --browser chrome
```

- Browser opens to gemini.google.com
- Log in with your Google account (one-time)
- PDFs in `Articles/` are processed automatically

### API Mode (Faster)

```bash
python gemini_extractor.py --api-key YOUR_KEY
```

### With Multiple Keys (Rate-Limit Rotation)

```bash
python gemini_extractor.py --api-kit path/to/API_KIT.txt
```

`API_KIT.txt` format — one key per line:
```
AIzaSyABC...
AIzaSyDEF...
AIzaSyGHI...
```

---

## Project Structure

```
extraction_agent/
├── gemini_extractor.py               # Main extractor (browser + API modes)
├── gemini_api_extractor.py           # API-only extractor wrapper
├── template_parser.py                # Reads extraction schema from Excel template
├── inspect_template.py               # Debug/inspect extraction template fields
├── check_models.py                   # List available Gemini models
├── test_connection.py                # Verify API key and model access
├── GLP1_Meta_Analysis_Data_Extraction_Template (1).xlsx  # Template schema
├── requirements.txt                  # Python dependencies
├── Articles/                         # Place your PDFs here (gitignored)
└── README.md                         # This file
```

---

## Extraction Schema

The extraction template covers:

| Category | Fields |
|---|---|
| **Study Characteristics** | Study ID, Journal, Country, Study Design, Database, Sample Size |
| **Demographics** | Age (mean ± SD), Sex (% male/female), BMI, Diabetes Status |
| **Intervention** | Drug/Procedure, Dose, Duration, Comparator |
| **Outcomes** | Primary Outcome, Secondary Outcomes, Follow-up Duration |
| **Comorbidities** | HTN, DM, CKD, CVD, Obesity |
| **Quality** | Risk of Bias, Newcastle-Ottawa Scale, Jadad Score |
| **Null Reasons** | Per-field justification for every missing value |

---

## Post-Processing

After extraction, two automatic transformations are applied:

**1. Null Reason Logging**
```python
# All null fields → structured reason saved to JSON
{"Study_ID": {"BMI": "Not reported in study", "Follow-up": "Only in-hospital outcomes reported"}}
```

**2. Percentage-to-Count Conversion**
```python
# Converts "30%" to "30/100 (30%)" using sample size
count = round(percentage * sample_size / 100)
# → "30/100 (30%)"
```

---

## API Configuration

| Parameter | Value |
|---|---|
| `temperature` | `0.2` (deterministic, reproducible) |
| `max_output_tokens` | `8192` (full schema) |
| Model | `gemini-2.5-flash` (default) |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
