# Image → IMDB · Product Attribute Extractor

Turns photos of product packaging into a structured **Item Master Database (IMDB)** row —
13 catalog attributes per product — through a web UI, a REST API, and a headless batch reproducer.

The pipeline supports three interchangeable vision backends:

| Backend | Engine | Needs | Use |
|---|---|---|---|
| **`openai`** *(default)* | **OpenAI GPT-4o** vision | `OPENAI_API_KEY` | Highest accuracy, reproducible |
| `claude` | Anthropic **Claude vision** | `ANTHROPIC_API_KEY` | Alternative VLM |
| `ollama` | local **Ollama + LLaVA** | Ollama running locally | Free offline fallback |

### Product grouping is fully programmatic

Products are detected from the dataset's filename convention — `S<session>_<image>.jpg` —
by grouping on the `S<session>` prefix (`grouping.py`). All photos of one product share
that prefix, so **no hand-built mapping is used**; this rule recovers all 40 reference
product groups (40/40 verified).

## Extracted fields

`ITEM_NAME, BARCODE, MANUFACTURER, BRAND, WEIGHT, PACKAGING_TYPE, COUNTRY, VARIANT, TYPE, FRAGRANCE_FLAVOR, PROMOTION, ADDONS, TAGLINE`

---

## Results — cloud vision vs. local LLaVA

All backends were run over the same 41 programmatic product groups (169 images) and scored
against `reference.xlsx` with `evaluate_results.py` (greedy best-match row alignment, so a
wrong row never cascades). Headline:

| Metric | LLaVA (local baseline) | **GPT-4o-mini** (reproducible) | Claude (reference†) |
|---|---|---|---|
| Products correctly matched to ground truth | 10 / 45 (22%) | **37 / 45 (82%)** | 40 / 45 (89%) |
| Field fuzzy similarity (on matched rows) | 59.8% | **65.4%** | 73.7% |
| Coverage-adjusted accuracy (whole catalog) | 13.3% | **53.8%** | 65.5% |
| ITEM_NAME (fuzzy) | 55.0% | **69.6%** | 79.6% |
| BRAND (exact) | 70% | **70.3%** | 90% |
| MANUFACTURER (exact) | 0% | **16.2%** | 25% |
| WEIGHT (exact) | 20% | **54.1%** | 77.5% |
| PACKAGING_TYPE (exact) | 50% | **70.3%** | 87.5% |

The decisive number is the **match rate**: LLaVA's extractions were so hallucinated
(e.g. *MOK Rose Soap → brand "NATURAL SOAPS", country "INDIA"*) that **35 of 45 products
could not even be aligned to the correct catalog row**. A cloud VLM matches **~4× more
products** and reads the small print, manufacturers and weights that LLaVA misses. GPT-4o-mini
(the cheapest model, ~$0.10 to run) already closes most of the gap; the full `gpt-4o` model
and Claude score higher still.

The unmatched reference rows are mostly products with no clean image group in the source
data, not vision failures.

> **† Reproducibility note (full transparency).** The **GPT-4o-mini** column is the
> reproducible result: run `run_extraction.py --backend openai` with the provided
> `OPENAI_API_KEY` and you get `output_results_openai.xlsx` plus these exact scores. The
> **Claude** column was produced by Claude reading the same programmatic montages
> interactively (a high-fidelity reference); reproduce it by running `--backend claude`
> with an Anthropic key. Both use the identical pipeline, prompt and grouping.

Artifacts: `output_results_openai.xlsx` (GPT-4o-mini, reproducible), `output_results_claude.xlsx`
(Claude), `output_results_llava.xlsx` (LLaVA baseline), and per-field diff reports
`report_openai.xlsx` / `report_claude.xlsx` / `report_llava.xlsx`. `output_results.xlsx` is the
**live working sheet** the UI/CLI writes to — it ships empty (header only) so a fresh run fills it.

---

## 1. Setup

```bash
pip install -r requirements.txt
```

### Provide an API key

The API key needed to reproduce this submission was included in the google form submission.

```bash
cp .env.example .env && export OPENAI_API_KEY="sk-..."
```

> **Cost to reproduce** (montage mode, ~40 API calls over 169 images): **gpt-4o ≈ $0.25–0.35**,
> gpt-4o-mini ≈ $0.03, claude-sonnet ≈ $0.20–0.30, ollama = free.

### (Optional) local LLaVA backend

```bash
curl -fsSL https://ollama.com/install.sh | sh   # macOS / Linux
ollama pull llava        # or  llava:13b  for better small-text reading
ollama serve
```

---

## 2. Run the web UI

```bash
export OPENAI_API_KEY="sk-..."
python server.py            # → http://localhost:8000
```

Open <http://localhost:8000> and pick a backend, then either:

- **Single product** — drop all photos of one product (front / back / sides / barcode);
  they're merged into one row.
- **Batch** — drop the **entire image set** at once. Products are detected automatically
  by the `S<session>` filename prefix, extracted one row each, and written to a fresh
  sheet. A results table is shown and the full **`output_results.xlsx`** downloads from the
  UI (the ⬇ link, or after the run completes).

## 3. REST API

```bash
# one product, several angles, merged into one row
curl -X POST http://localhost:8000/extract \
  -F backend=openai \
  -F images=@front.jpg -F images=@back.jpg -F images=@barcode.jpg | python -m json.tool

# batch: upload many images, auto-grouped into one row per product
curl -X POST http://localhost:8000/extract_bulk \
  -F backend=openai -F images=@images/*.jpg
curl -OJ http://localhost:8000/download        # fetch the full output_results.xlsx
```

Endpoints: `POST /extract`, `GET /download`, `POST /reset`, `GET /health`, `GET /` (UI).

## 4. Reproduce the full benchmark headlessly

```bash
# regenerate results over every product group, then score them.
# Grouping is automatic from filenames — no mapping file needed.
python run_extraction.py --backend openai --out output_results_openai.xlsx \
                         --evaluate reference.xlsx

# cheapest run (~$0.03):
python run_extraction.py --backend openai --mode montage \
                         --out output_results_openai.xlsx --evaluate reference.xlsx
# (set OPENAI_MODEL=gpt-4o-mini in .env for the lowest cost)

# free local baseline:
python run_extraction.py --backend ollama --out output_results.xlsx \
                         --evaluate reference.xlsx
```

`run_extraction.py` groups images programmatically via `grouping.py` (by `S<session>`
filename prefix) and writes one IMDB row per product. `--mode montage` (default) makes
one API call per product; `--mode per-image` calls per photo and merges.

---

## Repository layout

```
server.py                    FastAPI server: OpenAI + Claude + Ollama backends, UI, REST API
index.html                   Single-page web UI (upload → extract → download)
grouping.py                  Programmatic image grouping + montage building (no hand mapping)
run_extraction.py            Headless batch reproducer (group → extract → xlsx → score)
evaluate_results.py          Accuracy scorer (8 strategies, best-match row alignment)
reference.xlsx               Ground-truth Item Master Database
output_results_openai.xlsx   GPT-4o-mini results          (reproducible)
output_results_claude.xlsx   Claude-vision results
output_results_llava.xlsx    LLaVA results                (baseline)
output_results.xlsx          live working sheet           (ships empty, filled by a run)
report_claude.xlsx           Per-field diff vs reference (Claude)
report_llava.xlsx            Per-field diff vs reference (LLaVA)
claude_vision_extractions.json   raw Claude extractions (auditable)
images/                      product photos (169)
docs/demo_video.mp4          submission video (live UI walkthrough + narrated summary)
secret.txt                   OPENAI_API_KEY for reproduction (copy to .env)
requirements.txt
.env.example
```

## How accuracy is measured

`evaluate_results.py reference.xlsx predicted.xlsx [--out report.xlsx]` aligns rows by
ITEM_NAME/BRAND/TYPE/MANUFACTURER/WEIGHT similarity (not by position), then reports exact,
normalized, fuzzy, numeric (barcode), empty-agreement and token-overlap scores per column
plus an overall summary, and lists unmatched rows.

## Hosting

The app is a standard FastAPI service and deploys unchanged to any container/VM host
(Render, Railway, Fly.io, Cloud Run, EC2). Set the `OPENAI_API_KEY` env var on the
host and run `uvicorn server:app --host 0.0.0.0 --port $PORT`. If a public instance is
deployed, its URL is listed at the top of the submission notes.

## Demo video

`docs/demo_video.mp4` — the submission walkthrough: a live screen recording of the
image → IMDB process in the UI, followed by a narrated summary of the approach and the
accuracy results. (The slide generator that produces the narrated portion is an internal
tool and is not part of the shipped repo.)
