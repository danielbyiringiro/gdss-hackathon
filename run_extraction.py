#!/usr/bin/env python3
"""
run_extraction.py — reproduce the full image → IMDB pipeline headlessly.

Grouping is FULLY PROGRAMMATIC: products are detected from the filename
prefix (S<session>_<image>.jpg) by grouping.group_images() — no hand-built
mapping. For each product it runs the chosen vision backend and writes one
Item Master Database row, then optionally scores against a reference.

Two modes (default = montage, the cheapest / most faithful):
  --mode montage    one tiled image per product  → 1 API call / product (~40 calls)
  --mode per-image  every photo separately + merge → 1 call / image (~169 calls)

Usage:
  export OPENAI_API_KEY=sk-...        # or ANTHROPIC_API_KEY, or use --backend ollama
  python run_extraction.py --backend openai --out output_results_openai.xlsx \
                           --evaluate reference.xlsx

Cost (montage mode, ~40 calls over 169 images):
  gpt-4o        ≈ $0.25 – 0.35      gpt-4o-mini ≈ $0.03
  claude-sonnet ≈ $0.20 – 0.30      ollama/llava = free (local)
"""
import argparse
import asyncio
import json
import os
import sys

import grouping
import server   # backends, merge, xlsx writer


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", default="images")
    ap.add_argument("--backend", default=os.environ.get("BACKEND", server.DEFAULT_BACKEND))
    ap.add_argument("--mode", choices=["montage", "per-image"], default="montage")
    ap.add_argument("--out", default="output_results.xlsx")
    ap.add_argument("--evaluate", help="reference.xlsx to score against")
    ap.add_argument("--limit", type=int, help="only process the first N products (test)")
    ap.add_argument("--resume", action="store_true",
                    help="keep existing rows and skip products already done "
                         "(don't re-spend tokens after a rate-limit/crash)")
    args = ap.parse_args()

    server.OUTPUT_XLSX = args.out
    progress_path = args.out + ".done.json"
    done = set()
    if args.resume and os.path.exists(args.out) and os.path.exists(progress_path):
        done = set(json.load(open(progress_path)))
        print(f"resuming: {len(done)} product(s) already done, skipping them")
    else:
        for p in (args.out, progress_path):
            if os.path.exists(p):
                os.remove(p)

    groups = grouping.group_images(args.images_dir)
    if args.limit:
        groups = dict(list(groups.items())[:args.limit])
    n_imgs = sum(len(v) for v in groups.values())
    calls = len(groups) if args.mode == "montage" else n_imgs
    print(f"{len(groups)} products · {n_imgs} images · mode={args.mode} · "
          f"backend={args.backend} · ~{calls} API call(s)")

    for i, (key, files) in enumerate(groups.items(), 1):
        if key in done:
            print(f"[{i:>2}/{len(groups)}] {key}  →  (skipped, already done)")
            continue
        paths = [os.path.join(args.images_dir, f) for f in files]
        if args.mode == "montage":
            blob = grouping.build_montage(paths)
            merged = await server.extract_one(blob, "image/jpeg", args.backend)
        else:
            ex = [await server.extract_one(grouping.resize_jpeg(p), "image/jpeg", args.backend)
                  for p in paths]
            merged = server.merge_extractions(ex)
        row = server.append_to_xlsx(merged, args.out)
        done.add(key)
        json.dump(sorted(done), open(progress_path, "w"))
        print(f"[{i:>2}/{len(groups)}] {key}  →  {merged.get('ITEM_NAME','')[:50]:50} (row {row})")

    print(f"\nWrote {args.out}")
    if args.evaluate:
        import subprocess
        subprocess.run([sys.executable, "evaluate_results.py", args.evaluate, args.out])


if __name__ == "__main__":
    asyncio.run(main())
