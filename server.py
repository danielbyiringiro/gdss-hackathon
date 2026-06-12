"""
Product Attribute Extraction Server
Uses Ollama (LLaVA) to extract structured product data from images.
Aggregates evidence across multiple images to fill missing fields.
"""

import base64
import json
import re
import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional
import uvicorn

app = FastAPI(title="Product Attribute Extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llava"  # or "llava:13b" for more accuracy

FIELDS = [
    "ITEM_NAME",
    "BARCODE",
    "MANUFACTURER",
    "BRAND",
    "WEIGHT",
    "PACKAGING_TYPE",
    "COUNTRY",
    "VARIANT",
    "TYPE",
    "FRAGRANCE_FLAVOR",
    "PROMOTION",
    "ADDONS",
    "TAGLINE",
]

FIELD_DESCRIPTIONS = """
- ITEM_NAME: Full descriptive product name as intended for the catalog (string)
- BARCODE: Numeric barcode as printed on the package; numeric string without spaces/dashes
- MANUFACTURER: Company that manufactures the product (string)
- BRAND: Brand name as shown on the package (string)
- WEIGHT: Net weight or net volume including unit, same format as on pack (examples: "250G", "430G", "1.5 KG", "500 ML")
- PACKAGING_TYPE: Packaging form (examples: "TUB", "GLASS JAR", "SACHET", "BOTTLE", "CAN")
- COUNTRY: Country of manufacture or packing (string)
- VARIANT: Product variant if applicable (e.g. "ORIGINAL", "LOW FAT"); use empty string if not applicable
- TYPE: Product type or short category (e.g. "MARGARINE", "MAYONNAISE", "BUTTER")
- FRAGRANCE_FLAVOR: Flavor or fragrance where applicable (e.g. "RICH", "ORIGINAL"); use empty string if not applicable
- PROMOTION: Any on-pack promotion text (e.g. "50% OFF"); use empty string if not applicable
- ADDONS: Additional product features or pack contents (e.g. "SPOON INCLUDED"); use empty string if not applicable
- TAGLINE: Short promotional or descriptive tagline; use empty string if not applicable
"""

EXTRACTION_PROMPT = f"""You are a product data extraction specialist. Analyze this product image carefully and extract all visible information.

Extract the following fields:
{FIELD_DESCRIPTIONS}

Rules:
- Read ALL text visible on the packaging carefully
- For BARCODE: look for the numeric digits below the barcode lines
- For WEIGHT: use exactly the format shown on pack (uppercase, no space between number and unit: "250G" not "250 g")
- For PACKAGING_TYPE: describe the physical container type in uppercase
- If a field is not visible or not applicable, return an empty string ""
- Do not guess or infer — only extract what is clearly visible

Return ONLY a valid JSON object with exactly these keys: {json.dumps(FIELDS)}
No explanation, no markdown, just the raw JSON object.
"""

MERGE_PROMPT = """You are merging product data extracted from multiple images of the same product.

Here are the extractions from each image:
{extractions}

Rules:
- For each field, prefer non-empty values over empty ones
- If multiple images agree on a value, use that value
- If images disagree, prefer the more complete/specific value
- For BARCODE: prefer the longest numeric string
- For WEIGHT: prefer values with explicit units
- Never hallucinate — only use values present in the extractions
- Return ONLY a valid JSON object with exactly these keys: {fields}
No explanation, no markdown, just the raw JSON object.
"""


def clean_json(text: str) -> dict:
    """Extract and parse JSON from model output."""
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"```json|```", "", text).strip()
    # Find the first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group()
    parsed = json.loads(text)
    # Normalize keys and ensure all fields present
    result = {}
    for field in FIELDS:
        # Try exact match, then case-insensitive
        val = parsed.get(field) or parsed.get(field.lower()) or parsed.get(field.replace("_", " ")) or ""
        result[field] = str(val).strip() if val else ""
    return result


async def extract_from_image(image_bytes: bytes, mime_type: str) -> dict:
    """Send one image to Ollama LLaVA and get structured extraction."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": MODEL,
        "prompt": EXTRACTION_PROMPT,
        "images": [b64],
        "stream": False,
        "options": {
            "temperature": 0.1,  # Low temp for factual extraction
            "num_predict": 512,
        },
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

    raw_text = data.get("response", "")
    try:
        return clean_json(raw_text)
    except Exception as e:
        # Return empty fields if parse fails
        print(f"Parse error: {e}\nRaw: {raw_text[:300]}")
        return {field: "" for field in FIELDS}


async def merge_extractions(extractions: list[dict]) -> dict:
    """Use LLaVA (text-only) to intelligently merge multiple extractions."""
    if len(extractions) == 1:
        return extractions[0]

    # Simple rule-based merge first (fast, no extra LLM call)
    merged = {field: "" for field in FIELDS}
    for field in FIELDS:
        candidates = [e[field] for e in extractions if e.get(field, "").strip()]
        if not candidates:
            continue
        if len(set(candidates)) == 1:
            merged[field] = candidates[0]
        else:
            # Pick longest non-empty value as heuristic
            merged[field] = max(candidates, key=len)

    return merged


@app.post("/extract")
async def extract_product(images: list[UploadFile] = File(...)):
    """
    Extract product attributes from one or more images.
    Accepts: multipart/form-data with field name 'images' (one or more files).
    Returns: JSON with all product fields aggregated across all images.
    """
    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required.")
    if len(images) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 images per request.")

    # Check Ollama is running
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            check = await client.get("http://localhost:11434/api/tags")
            check.raise_for_status()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Ollama is not running. Start it with: ollama serve"
        )

    extractions = []
    per_image_results = []

    for img in images:
        content = await img.read()
        mime = img.content_type or "image/jpeg"
        result = await extract_from_image(content, mime)
        extractions.append(result)
        per_image_results.append({
            "filename": img.filename,
            "extraction": result,
        })

    merged = await merge_extractions(extractions)

    return JSONResponse({
        "product": merged,
        "images_processed": len(images),
        "per_image": per_image_results,
    })


@app.get("/health")
async def health():
    """Check server and Ollama status."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://localhost:11434/api/tags")
            tags = r.json()
            models = [m["name"] for m in tags.get("models", [])]
        return {"status": "ok", "ollama": "running", "models": models}
    except Exception as e:
        return {"status": "ok", "ollama": "not reachable", "error": str(e)}


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
