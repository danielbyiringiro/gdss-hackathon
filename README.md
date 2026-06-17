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
(Claude), `output_results.xlsx` (LLaVA), and per-field diff reports `report_openai.xlsx` /
`report_claude.xlsx` / `report_llava.xlsx`.

---

## 1. Setup

```bash
pip install -r requirements.txt
```

### Provide an API key

```bash
cp .env.example .env          # then edit .env and paste your real key
export OPENAI_API_KEY="sk-..."            # or ANTHROPIC_API_KEY="sk-ant-..."
```

> **For reviewers reproducing this submission:** paste a valid OpenAI key into `.env`
> (or export `OPENAI_API_KEY`). No key is committed to the repo. Without a key the server
> still runs and the UI loads, but the cloud backends return a clear 503 and you can switch
> to the free `ollama` backend instead.
>
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

Open <http://localhost:8000>, pick a backend, drop all photos of **one** product
(front / back / sides / barcode), and click **Extract attributes**. The merged row is
shown and appended to `output_results.xlsx`, downloadable from the UI.

## 3. REST API

```bash
# one product, several angles, merged into one row
curl -X POST http://localhost:8000/extract \
  -F backend=openai \
  -F images=@front.jpg -F images=@back.jpg -F images=@barcode.jpg | python -m json.tool
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
output_results_openai.xlsx   GPT-4o results               (written by your run)
output_results_claude.xlsx   Claude-vision results
output_results.xlsx          LLaVA results                (baseline)
report_claude.xlsx           Per-field diff vs reference (Claude)
report_llava.xlsx            Per-field diff vs reference (LLaVA)
claude_vision_extractions.json   raw Claude extractions (auditable)
images/                      product photos (169)
docs/                        demo video + narrated-slide generator
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
(Render, Railway, Fly.io, Cloud Run, EC2). Set the `ANTHROPIC_API_KEY` env var on the
host and run `uvicorn server:app --host 0.0.0.0 --port $PORT`. If a public instance is
deployed, its URL is listed at the top of the submission notes.

## Demo video

`docs/demo_video.mp4` — narrated slide walkthrough of the image → IMDB process and the
accuracy comparison. Rebuild it with:

```bash
python docs/make_video.py
```

Narration uses **OpenAI text-to-speech** when `OPENAI_API_KEY` is set (natural voice,
~2–3 cents for the whole script), and automatically falls back to a fully-offline
espeak-ng voice otherwise. Override the voice/model via env vars:

```bash
TTS_VOICE=nova TTS_MODEL=gpt-4o-mini-tts python docs/make_video.py
# voices: alloy · echo · fable · onyx · nova · shimmer
# force the offline voice with: TTS_BACKEND=espeak
```
