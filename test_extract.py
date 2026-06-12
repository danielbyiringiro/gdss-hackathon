#!/usr/bin/env python3
"""
Quick test script — call the extraction server from the command line.

Usage:
  python test_extract.py image1.jpg image2.jpg image3.jpg
"""

import sys
import json
import httpx

SERVER = "http://localhost:8000"

def main():
    paths = sys.argv[1:]
    if not paths:
        print("Usage: python test_extract.py <image1> [image2] ...")
        sys.exit(1)

    files = []
    for p in paths:
        with open(p, "rb") as f:
            files.append(("images", (p, f.read(), "image/jpeg")))

    print(f"Sending {len(files)} image(s) to {SERVER}/extract ...\n")
    resp = httpx.post(f"{SERVER}/extract", files=files, timeout=300)
    resp.raise_for_status()
    data = resp.json()

    print("=== AGGREGATED PRODUCT ===")
    for k, v in data["product"].items():
        print(f"  {k:<20} {v or '(empty)'}")

    print(f"\n=== PER-IMAGE ({data['images_processed']} images) ===")
    for img in data["per_image"]:
        print(f"\n  [{img['filename']}]")
        for k, v in img["extraction"].items():
            if v:
                print(f"    {k:<20} {v}")

if __name__ == "__main__":
    main()
