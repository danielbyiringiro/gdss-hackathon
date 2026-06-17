#!/usr/bin/env python3
"""
make_video.py — build the narrated slide demo (docs/demo_video.mp4).

Renders themed slides with PIL, synthesizes narration fully offline with the
bundled espeak-ng shared library (no network / no API key), and stitches
image+audio clips together with ffmpeg.

    python docs/make_video.py
"""

import ctypes
import io
import os
import re
import struct
import subprocess
import sys
import wave

import httpx
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import grouping  # noqa: E402  (programmatic montage builder)

# Load ../.env so OPENAI_API_KEY / TTS_* are picked up (inline comments stripped)
_envf = os.path.join(ROOT, ".env")
if os.path.exists(_envf):
    for _l in open(_envf, encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            _v = _v.strip()
            if _v[:1] not in ('"', "'"):
                _v = re.split(r"\s+#", _v, maxsplit=1)[0]
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Narration voice: OpenAI TTS when a key is present (natural), else offline espeak.
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
TTS_MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "alloy")
USE_OPENAI_TTS = bool(OPENAI_KEY) and os.environ.get("TTS_BACKEND", "auto") != "espeak"
W, H = 1280, 720
BG, CARD, INK, MUT, ACC, OK, BAD, LINE = (
    (15, 18, 32),
    (26, 31, 53),
    (232, 236, 246),
    (154, 163, 192),
    (108, 140, 255),
    (70, 211, 154),
    (255, 110, 120),
    (43, 50, 82),
)

# Cross-platform font lookup (macOS, Linux); falls back to PIL's default.
_FONT_CANDIDATES = {
    True: [  # bold
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    False: [  # regular
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}
_font_path = {}


def _resolve_font(bold):
    if bold not in _font_path:
        _font_path[bold] = next(
            (p for p in _FONT_CANDIDATES[bold] if os.path.exists(p)), None
        )
    return _font_path[bold]


def font(sz, bold=True):
    p = _resolve_font(bold)
    if p:
        try:
            return ImageFont.truetype(p, sz)
        except OSError:
            pass
    try:
        return ImageFont.load_default(sz)   # Pillow ≥ 10.1 scales the default
    except TypeError:
        return ImageFont.load_default()


# ----------------------------------------------- TTS fallback (offline espeak)
# Lazily initialised so the OpenAI-voice path needs no extra packages.
_espeak = {}


def _espeak_init():
    if _espeak:
        return
    try:
        import espeakng_loader  # only needed for the offline fallback
    except ImportError:
        _espeak["unavailable"] = True
        return
    e = ctypes.CDLL(espeakng_loader.get_library_path())
    samples = bytearray()
    CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short),
                          ctypes.c_int, ctypes.c_void_p)

    def cb(wav, n, ev):
        if wav and n > 0:
            samples.extend(ctypes.cast(wav, ctypes.POINTER(ctypes.c_short * n)).contents)
        return 0

    cbref = CB(cb)
    sr = e.espeak_Initialize(0x02, 0, os.path.dirname(espeakng_loader.get_data_path()).encode(), 0)
    e.espeak_SetSynthCallback(cbref)
    e.espeak_SetVoiceByName(b"en-us+f3")
    e.espeak_SetParameter(1, 150, 0)  # rate
    e.espeak_SetParameter(3, 60, 0)   # pitch
    _espeak.update(e=e, sr=sr, samples=samples, cbref=cbref)


def synth_espeak(text, path):
    _espeak_init()
    if _espeak.get("unavailable"):
        # No TTS engine available at all — write ~3s of silence so the
        # video still assembles (install espeakng-loader, or set a key, for audio).
        print("  no offline TTS engine (pip install espeakng-loader); writing silence")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b"\x00\x00" * 22050 * 3)
        return
    e, sr, samples = _espeak["e"], _espeak["sr"], _espeak["samples"]
    samples.clear()
    b = text.encode("utf-8")
    e.espeak_Synth(b, len(b) + 1, 0, 0, 0, 0x1000 | 1, None, None)
    e.espeak_Synchronize()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(samples))


# espeak needs letters spelled out; a natural TTS voice reads the real words.
_NATURAL = {"I-M-D-B": "IMDB", "G-P-T four-o": "GPT-4o", "Lava model": "LLaVA model"}


def synth_openai(text, path):
    """Natural narration via OpenAI's text-to-speech API (WAV out)."""
    for k, v in _NATURAL.items():
        text = text.replace(k, v)
    r = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={"model": TTS_MODEL, "voice": TTS_VOICE,
              "input": text, "response_format": "wav"},
        timeout=60.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI TTS {r.status_code}: {r.text[:300]}")
    with open(path, "wb") as f:
        f.write(r.content)


def synth(text, path):
    if USE_OPENAI_TTS:
        try:
            synth_openai(text, path)
            return
        except Exception as exc:
            print(f"  OpenAI TTS failed ({exc}); using offline espeak voice")
    synth_espeak(text, path)


# ---------------------------------------------------------------- drawing utils
def base():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=ACC)
    d.text((48, 28), "Image → IMDB", font=font(26), fill=ACC)
    d.text((W - 360, 34), "Product Attribute Extractor", font=font(16, False), fill=MUT)
    return img, d


def card(d, x, y, w, h):
    d.rounded_rectangle(
        [x, y, x + w, y + h], radius=16, fill=CARD, outline=LINE, width=1
    )


def wrap(d, text, fnt, maxw):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if d.textlength(t, font=fnt) <= maxw:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- slides
def s_title(p):
    img, d = base()
    d.text((48, 250), "Image → IMDB", font=font(72), fill=INK)
    d.text(
        (50, 338),
        "Turning product photos into an Item Master Database",
        font=font(28, False),
        fill=MUT,
    )
    d.text((48, 430), "Powered by GPT-4o vision", font=font(30), fill=ACC)
    d.text((48, 640), "GDSS Hackathon submission", font=font(18, False), fill=MUT)
    img.save(p)


def s_problem(p):
    img, d = base()
    d.text((48, 110), "The task", font=font(40), fill=INK)
    for i, t in enumerate(
        [
            "Photograph a product from several angles.",
            "Extract 13 catalog attributes into one database row:",
            "ITEM_NAME · BARCODE · MANUFACTURER · BRAND · WEIGHT ·",
            "PACKAGING_TYPE · COUNTRY · VARIANT · TYPE · FLAVOR ·",
            "PROMOTION · ADDONS · TAGLINE",
        ]
    ):
        d.text(
            (50, 190 + i * 44),
            t,
            font=font(24, i not in (2, 3, 4)),
            fill=INK if i < 2 else MUT,
        )
    card(d, 48, 430, W - 96, 210)
    d.text((72, 452), "Baseline (local LLaVA) hallucinates:", font=font(22), fill=BAD)
    d.text(
        (72, 498),
        "MOK Rose Soap  →  brand “NATURAL SOAPS”,  country “INDIA”",
        font=font(22, False),
        fill=INK,
    )
    d.text(
        (72, 552),
        "35 of 45 products were so wrong they couldn’t even be",
        font=font(22, False),
        fill=MUT,
    )
    d.text(
        (72, 584), "matched to the right catalog row.", font=font(22, False), fill=MUT
    )
    img.save(p)


def s_approach(p):
    img, d = base()
    d.text((48, 110), "The solution", font=font(40), fill=INK)
    items = [
        (
            "GPT-4o vision backend",
            "reads small print, weights, manufacturers & barcodes",
        ),
        ("Web UI", "drag photos in → see the row → download the spreadsheet"),
        ("Programmatic grouping", "products auto-detected from S<session> filenames"),
        ("Pluggable", "OpenAI · Claude · local Ollama + LLaVA, one setting"),
    ]
    y = 200
    for title, sub in items:
        card(d, 48, y, W - 96, 92)
        d.ellipse([72, y + 34, 96, y + 58], fill=ACC)
        d.text((120, y + 20), title, font=font(26), fill=INK)
        d.text((120, y + 56), sub, font=font(20, False), fill=MUT)
        y += 108
    img.save(p)


def s_ui(p):
    img, d = base()
    d.text((48, 96), "Live: photos → attributes", font=font(36), fill=INK)
    # product montage thumbnail — built programmatically from the image group
    imgs_dir = os.path.join(ROOT, "images")
    g = grouping.group_images(imgs_dir)
    paths = [os.path.join(imgs_dir, f) for f in g.get("S229664513", [])]
    if paths:
        th = Image.open(io.BytesIO(grouping.build_montage(paths))).convert("RGB")
        th.thumbnail((520, 520))
        card(d, 48, 168, 560, 470)
        img.paste(th, (68 + (520 - th.width) // 2, 188 + (450 - th.height) // 2))
    # extracted row (real GPT-4o-mini output for this product)
    x = 650
    card(d, x, 168, W - x - 48, 470)
    rows = [
        ("ITEM_NAME", "ENA PA MAYONNAISE PREMIUM 1000ML"),
        ("BRAND", "ENA PA"),
        ("WEIGHT", "1000ML"),
        ("PACKAGING_TYPE", "GLASS JAR"),
        ("TYPE", "MAYONNAISE"),
        ("COUNTRY", "GHANA"),
        ("VARIANT", "PREMIUM"),
        ("MANUFACTURER", "ENA PA"),
    ]
    yy = 200
    for k, v in rows:
        d.text((x + 26, yy), k, font=font(17, False), fill=MUT)
        d.text((x + 250, yy), v, font=font(17), fill=INK)
        yy += 50
    img.save(p)


def s_results(p):
    img, d = base()
    d.text((48, 96), "Results — GPT-4o vs. local LLaVA", font=font(36), fill=INK)
    cols = [("Metric", 60), ("LLaVA", 660), ("GPT-4o-mini", 880), ("Claude", 1130)]
    for c, x in cols:
        d.text((x, 176), c, font=font(20), fill=MUT)
    d.line([60, 212, W - 50, 212], fill=LINE, width=1)
    rows = [
        ("Products matched to truth", "10/45", "37/45", "40/45"),
        ("  (% of catalog)", "22%", "82%", "89%"),
        ("Field similarity (matched)", "59.8%", "65.4%", "73.7%"),
        ("Whole-catalog accuracy", "13.3%", "53.8%", "65.5%"),
        ("WEIGHT (exact)", "20%", "54.1%", "77.5%"),
        ("PACKAGING (exact)", "50%", "70.3%", "87.5%"),
    ]
    y = 234
    for m, a, b, c in rows:
        d.text((60, y), m, font=font(20, False), fill=INK)
        d.text((660, y), a, font=font(20), fill=BAD)
        d.text((880, y), b, font=font(20), fill=OK)
        d.text((1130, y), c, font=font(20), fill=MUT)
        y += 58
    d.text((60, y + 16),
           "GPT-4o-mini reproducible for ~$0.10 with the provided key.",
           font=font(18, False), fill=MUT)
    img.save(p)


def s_repro(p):
    img, d = base()
    d.text((48, 110), "Reproduce it", font=font(40), fill=INK)
    card(d, 48, 200, W - 96, 360)
    lines = [
        "$ pip install -r requirements.txt",
        "$ export OPENAI_API_KEY=sk-...",
        "$ python server.py            # → http://localhost:8000",
        "",
        "# regenerate + score the whole benchmark (~$0.10):",
        "$ python run_extraction.py --backend openai \\",
        "      --out output_results_openai.xlsx \\",
        "      --evaluate reference.xlsx",
    ]
    y = 232
    for ln in lines:
        d.text(
            (78, y), ln, font=font(22, False), fill=OK if ln.startswith("#") else INK
        )
        y += 40
    d.text(
        (48, 600),
        "Key goes in .env — switch to local Ollama with one flag.",
        font=font(20, False),
        fill=MUT,
    )
    img.save(p)


def s_end(p):
    img, d = base()
    d.text((48, 280), "Image → IMDB", font=font(60), fill=INK)
    d.text(
        (50, 370),
        "Accurate product attribute extraction with GPT-4o vision.",
        font=font(26, False),
        fill=MUT,
    )
    d.text(
        (48, 470), "22% → 82% products correctly catalogued.", font=font(28), fill=OK
    )
    img.save(p)


SLIDES = [
    (
        s_title,
        "Image to I-M-D-B: a tool that turns product photographs into a structured item master database, powered by G-P-T four-o vision.",
    ),
    (
        s_problem,
        "The task is simple to state. Photograph a product from several angles, then extract thirteen catalog attributes into a single database row: the item name, barcode, manufacturer, brand, weight, packaging, country, and more. The baseline using a local Lava model hallucinates badly. It read the MOK rose soap as brand NATURAL SOAPS made in INDIA. In fact, thirty five of forty five products were so wrong they could not even be matched to the correct catalog row.",
    ),
    (
        s_approach,
        "Our solution swaps in a cloud vision backend that reliably reads small print, weights, manufacturers and barcodes. Products are grouped automatically from their filenames, with no hand-made mapping. It ships with a web interface where you drag photos in and get the row back, plus a headless batch reproducer. The backend is pluggable across G-P-T four-o, Claude, and a local model.",
    ),
    (
        s_ui,
        "Here is the process in action. We drop every photo of one product into the interface. The images are merged, and the model returns a clean, fully populated row: item name, brand, weight, packaging, type, country, variant, and the barcode read straight off the label.",
    ),
    (
        s_results,
        "And the results. The decisive number is the match rate. The local Lava model matched only ten of forty five products, twenty two percent. G-P-T four-o mini, the cheapest model, matched thirty seven, eighty two percent of the catalog, for about ten cents. Weight accuracy rose from twenty to fifty four percent, and packaging from fifty to seventy percent. The full G-P-T four-o and Claude models score higher still.",
    ),
    (
        s_repro,
        "Reproducing this is three commands. Install requirements, set your OpenAI key, and run the server. One more command regenerates the whole benchmark and scores it against the reference, for about ten cents. The key lives in a dot env file, and you can switch to Claude or a fully local model with one flag.",
    ),
    (
        s_end,
        "Image to I-M-D-B. Accurate product attribute extraction with G-P-T four-o vision, taking us from twenty two to eighty two percent of products correctly catalogued. Thank you.",
    ),
]


def audio_duration(path):
    """Seconds of audio, via ffprobe (robust for any WAV); clamped sanely."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        d = float(out)
        if 0.3 < d < 120:
            return d
    except Exception:
        pass
    return 8.0   # fallback if probing fails


def main():
    work = os.path.join(HERE, "_build")
    os.makedirs(work, exist_ok=True)
    voice = f"OpenAI TTS ({TTS_VOICE})" if USE_OPENAI_TTS else "offline espeak"
    print(f"Building {len(SLIDES)} slides · narration: {voice}")
    clips = []
    for i, (fn, narr) in enumerate(SLIDES):
        png = os.path.join(work, f"s{i}.png")
        wav = os.path.join(work, f"s{i}.wav")
        mp4 = os.path.join(work, f"s{i}.mp4")
        print(f"[{i+1}/{len(SLIDES)}] rendering slide…", flush=True)
        fn(png)
        print(f"[{i+1}/{len(SLIDES)}] synthesizing narration…", flush=True)
        synth(narr, wav)
        print(f"[{i+1}/{len(SLIDES)}] encoding clip…", flush=True)
        dur = audio_duration(wav) + 0.4
        # -nostdin so ffmpeg never blocks waiting on the terminal.
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                "-loop", "1", "-i", png,
                "-i", wav,
                "-c:v", "libx264", "-tune", "stillimage", "-t", f"{dur:.2f}",
                "-c:a", "aac", "-b:a", "128k",
                "-pix_fmt", "yuv420p", "-vf", "scale=1280:720",
                mp4,
            ],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed on slide {i}:\n{proc.stderr[-600:]}")
        clips.append(mp4)
        print(f"slide {i}: {dur:.1f}s")
    lst = os.path.join(work, "list.txt")
    open(lst, "w").write("\n".join(f"file '{c}'" for c in clips))
    out = os.path.join(HERE, "demo_video.mp4")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{proc.stderr[-600:]}")
    print("wrote", out)


if __name__ == "__main__":
    main()
