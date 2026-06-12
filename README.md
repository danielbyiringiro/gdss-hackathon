# Product Attribute Extractor

Extracts structured product attributes from packaging images using **Ollama + LLaVA** — fully local, no API key needed.

## Extracted Fields

| Field | Description | Example |
|---|---|---|
| ITEM_NAME | Full catalog name | "FLORA ORIGINAL SPREAD" |
| BARCODE | Numeric barcode digits | "8712566310012" |
| MANUFACTURER | Manufacturing company | "UPFIELD" |
| BRAND | Brand name on pack | "FLORA" |
| WEIGHT | Net weight/volume with unit | "500G" / "1.5 KG" / "500 ML" |
| PACKAGING_TYPE | Physical container type | "TUB" / "GLASS JAR" / "BOTTLE" |
| COUNTRY | Country of manufacture | "SOUTH AFRICA" |
| VARIANT | Product variant | "ORIGINAL" / "LOW FAT" |
| TYPE | Product category | "MARGARINE" / "SPREAD" |
| FRAGRANCE_FLAVOR | Flavor/fragrance | "RICH" / "LEMON" |
| PROMOTION | On-pack promotion | "50% OFF" / "2 FOR 1" |
| ADDONS | Extra features/contents | "SPOON INCLUDED" |
| TAGLINE | Promotional tagline | "MAKES EVERY MEAL SPECIAL" |

## Setup

### 1. Install Ollama
```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows: download from https://ollama.com/download
```

### 2. Pull the vision model
```bash
ollama pull llava
# For better accuracy (slower, needs 16GB RAM):
ollama pull llava:13b
```

### 3. Start Ollama
```bash
ollama serve
```

### 4. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 5. Start the server
```bash
python server.py
```
Server runs at `http://localhost:8000`

---

## Usage

### REST API

**POST** `/extract`  
Content-Type: `multipart/form-data`  
Field name: `images` (one or more image files)

```bash
# Single image
curl -X POST http://localhost:8000/extract \
  -F "images=@front.jpg" \
  | python -m json.tool

# Multiple images (aggregated)
curl -X POST http://localhost:8000/extract \
  -F "images=@front.jpg" \
  -F "images=@back.jpg" \
  -F "images=@side.jpg" \
  | python -m json.tool
```

**Response:**
```json
{
  "product": {
    "ITEM_NAME": "FLORA ORIGINAL SPREAD",
    "BARCODE": "8712566310012",
    "MANUFACTURER": "UPFIELD",
    "BRAND": "FLORA",
    "WEIGHT": "500G",
    "PACKAGING_TYPE": "TUB",
    "COUNTRY": "SOUTH AFRICA",
    "VARIANT": "ORIGINAL",
    "TYPE": "SPREAD",
    "FRAGRANCE_FLAVOR": "",
    "PROMOTION": "",
    "ADDONS": "",
    "TAGLINE": "MAKES EVERY MEAL SPECIAL"
  },
  "images_processed": 2,
  "per_image": [...]
}
```

### CLI test script
```bash
python test_extract.py front.jpg back.jpg side.jpg
```

### Health check
```bash
curl http://localhost:8000/health
```

---

## How it works

1. Each image is sent to LLaVA with a structured extraction prompt
2. LLaVA returns a JSON object with all fields for that image
3. After all images are processed, fields are **merged** across images:
   - Non-empty values preferred over empty
   - Longer/more specific values preferred when images disagree
   - Consensus values (same across images) are used directly
4. Final aggregated product object is returned

## Tips for accuracy

- **Use 3–5 images**: front, back, side, barcode close-up, label
- **Good lighting**: avoid glare and shadows
- **Sharp focus**: especially important for barcode and small text
- **Higher model**: `llava:13b` reads small text significantly better than `llava:7b`
- **Barcode close-up**: always include a dedicated barcode image for reliable digit extraction
