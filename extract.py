"""
Legit Kits Cut Guide Extractor
================================
Uses Claude vision API to extract structured data from scanned quilt pattern
images, then writes data files and runs generate.py / tracking.py.

Usage:
    python extract.py <pattern_folder>

Input folder layout:
    <pattern_folder>/
    ├── cut/       cut_01.png, cut_02.png, ...   (cut guide pages)
    ├── assy/      assy_01.png, assy_02.png, ...  (assembly guide pages)
    └── overview/  overview_01.png, ...           (overview pages)

Output (written into data/ in the current directory):
    data/cut_guide_data.py
    data/assembly_data.py

Then automatically runs generate.py and tracking.py.

Requirements:
    pip install anthropic pillow
    ANTHROPIC_API_KEY must be set in the environment.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import anthropic
from PIL import Image
import io

def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from .env in the same directory as this script."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAX_BYTES = 4 * 1024 * 1024  # 4 MB — leave headroom under the 5 MB API limit

def encode_image(path: Path) -> str:
    """Return base64-encoded JPEG bytes, resizing if the file exceeds MAX_BYTES."""
    img = Image.open(path).convert("RGB")
    quality = 85
    while True:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= MAX_BYTES:
            return base64.standard_b64encode(data).decode("utf-8")
        # Reduce: lower quality first, then shrink dimensions
        if quality > 50:
            quality -= 10
        else:
            w, h = img.size
            img = img.resize((w * 3 // 4, h * 3 // 4), Image.LANCZOS)


def sorted_images(folder: Path, prefix: str) -> list[Path]:
    """Return sorted image files whose name starts with <prefix> followed by one or more underscores and digits."""
    pattern = re.compile(rf"^{re.escape(prefix)}_+\d+\.(png|jpg|jpeg)$", re.IGNORECASE)
    files = [p for p in folder.iterdir() if pattern.match(p.name)]
    return sorted(files, key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)))


def call_claude(client: anthropic.Anthropic, image_path: Path, prompt: str) -> str:
    """Send a single image to Claude and return the text response."""
    b64 = encode_image(image_path)
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return msg.content[0].text


# ---------------------------------------------------------------------------
# Cut guide extraction
# ---------------------------------------------------------------------------

CUT_GUIDE_PROMPT = """\
This is a page from a Legit Kits quilt cut guide. Each page covers one or more fabrics.
For each fabric on this page, extract ALL piece entries.

A fabric section has:
- Fabric code (short code like AF, BT, etc.)
- Fabric name (e.g. Saffron, Chocolate)
- SKU (product code)
- Fabric size (e.g. 2.5" x 44", Fat Quarter)
- A list of pieces, each with:
  - Piece number (integer, from circled numbers like ① ② etc.)
  - Template code (e.g. F3m, A4a, B7c — row letter + column number + optional segment letter)
  - Quantity (the number in parentheses after the template code, e.g. F3m(3) means quantity 3; if absent use 1)

IMPORTANT: The piece list is split across two areas of the page — read ALL of them.
The page number is printed at the bottom of the cut guide page.

Return ONLY a JSON array. Each element represents one piece row:
[
  {
    "fabric_code": "AF",
    "fabric_name": "Saffron",
    "sku": "SKU-12345",
    "fabric_size": "2.5\\\" x 44\\\"",
    "piece_num": 1,
    "template_code": "F3m",
    "quantity": 3,
    "page": 1
  },
  ...
]

If a field is not visible or not applicable, use null. Do not include any text outside the JSON array.
"""


def extract_cut_guide(client: anthropic.Anthropic, cut_folder: Path) -> list[dict]:
    images = sorted_images(cut_folder, "cut")
    if not images:
        print("  [cut] No images found — skipping.")
        return []

    all_rows: list[dict] = []
    for img in images:
        print(f"  [cut] Processing {img.name} …")
        raw = call_claude(client, img, CUT_GUIDE_PROMPT)
        # Strip markdown code fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw)
        try:
            rows = json.loads(raw)
            all_rows.extend(rows)
            print(f"         -> {len(rows)} piece rows")
        except json.JSONDecodeError as e:
            print(f"  [cut] WARNING: JSON parse error for {img.name}: {e}")
            print(f"         Raw response (first 500 chars): {raw[:500]}")

    return all_rows


# ---------------------------------------------------------------------------
# Assembly guide extraction
# ---------------------------------------------------------------------------

ASSY_PROMPT = """\
This is a page from a Legit Kits quilt assembly guide. It shows how blocks are assembled
from fragments (sub-pieces).

For each block shown on this page, extract:
- block_id: The block identifier (e.g. A1, B7, F3 — row letter + column number)
- fragments: Ordered list of fragment IDs that make up the block
  - Single-fragment blocks: fragment ID equals block ID (e.g. ["A1"])
  - Multi-fragment blocks: fragment IDs are block ID + letter suffix (e.g. ["B7a","B7b",...])

Return ONLY a JSON array:
[
  {"block_id": "A1", "fragments": ["A1"]},
  {"block_id": "B7", "fragments": ["B7a","B7b","B7c","B7d","B7e","B7f","B7g","B7h"]},
  ...
]

Do not include any text outside the JSON array.
"""


def extract_assembly(client: anthropic.Anthropic, assy_folder: Path) -> dict[str, list[str]]:
    images = sorted_images(assy_folder, "assy")
    if not images:
        print("  [assy] No images found — skipping.")
        return {}

    blocks: dict[str, list[str]] = {}
    for img in images:
        print(f"  [assy] Processing {img.name} …")
        raw = call_claude(client, img, ASSY_PROMPT)
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw)
        try:
            entries = json.loads(raw)
            for entry in entries:
                blocks[entry["block_id"]] = entry["fragments"]
            print(f"         -> {len(entries)} blocks")
        except json.JSONDecodeError as e:
            print(f"  [assy] WARNING: JSON parse error for {img.name}: {e}")

    return blocks


# ---------------------------------------------------------------------------
# Overview extraction (currently informational — not written to a data file)
# ---------------------------------------------------------------------------

OVERVIEW_PROMPT = """\
This is a page from a Legit Kits quilt overview / kit contents guide.
Extract any structured information visible: fabric list, fabric codes, SKUs, yardage,
quilt dimensions, block counts, or other metadata.

Return a JSON object with whatever fields are present, for example:
{
  "quilt_name": "Land of the Free",
  "finished_size": "72\\\" x 90\\\"",
  "fabrics": [
    {"code": "AF", "name": "Saffron", "sku": "LK-1234", "yardage": "1.5 yards"}
  ]
}

If the page has no useful structured data, return {}.
Do not include any text outside the JSON object.
"""


def extract_overview(client: anthropic.Anthropic, overview_folder: Path) -> list[dict]:
    images = sorted_images(overview_folder, "overview")
    if not images:
        print("  [overview] No images found — skipping.")
        return []

    results = []
    for img in images:
        print(f"  [overview] Processing {img.name} …")
        raw = call_claude(client, img, OVERVIEW_PROMPT)
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw)
        try:
            data = json.loads(raw)
            if data:
                results.append(data)
        except json.JSONDecodeError as e:
            print(f"  [overview] WARNING: JSON parse error for {img.name}: {e}")

    return results


# ---------------------------------------------------------------------------
# Write data files
# ---------------------------------------------------------------------------

def write_cut_guide_data(rows: list[dict], out_path: Path) -> None:
    """Write rows as a Python DATA tuple list matching the existing format."""
    lines = [
        '"""',
        "Auto-generated cut guide data.",
        '"""',
        "",
        "DATA = [",
    ]
    for r in rows:
        fabric_code  = repr(r.get("fabric_code") or "")
        fabric_name  = repr(r.get("fabric_name") or "")
        sku          = repr(r.get("sku") or "")
        fabric_size  = repr(r.get("fabric_size") or "")
        piece_num    = int(r.get("piece_num") or 0)
        template     = repr(r.get("template_code") or "")
        quantity     = int(r.get("quantity") or 1)
        page         = int(r.get("page") or 0)
        lines.append(
            f"    ({fabric_code}, {fabric_name}, {sku}, {fabric_size}, "
            f"{piece_num}, {template}, {quantity}, {page}),"
        )
    lines.append("]")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")


def write_assembly_data(blocks: dict[str, list[str]], out_path: Path) -> None:
    """Write blocks as a Python BLOCKS dict matching the existing format."""
    lines = [
        '"""',
        "Auto-generated block assembly data.",
        '"""',
        "",
        "",
        "def _frags(block, letters):",
        '    return [f"{block}{c}" for c in letters]',
        "",
        "",
        "BLOCKS = {",
    ]
    for block_id, frags in blocks.items():
        lines.append(f"    {repr(block_id)}: {repr(frags)},")
    lines.append("}")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(blocks)} blocks to {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Extract Legit Kits pattern data via Claude vision API")
    parser.add_argument("pattern_folder", help="Folder containing cut/, assy/, overview/ subfolders")
    parser.add_argument("--api-key", help="Anthropic API key (overrides ANTHROPIC_API_KEY env var)")
    args = parser.parse_args()

    pattern_folder = Path(args.pattern_folder).resolve()
    if not pattern_folder.is_dir():
        sys.exit(f"Error: {pattern_folder} is not a directory")

    cut_folder      = pattern_folder / "cut"
    assy_folder     = pattern_folder / "assy"
    overview_folder = pattern_folder / "overview"

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Error: provide --api-key or set ANTHROPIC_API_KEY environment variable")

    client = anthropic.Anthropic(api_key=api_key)

    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)

    # --- Overview (informational) ---
    if overview_folder.is_dir():
        print("\n=== Overview pages ===")
        overview_data = extract_overview(client, overview_folder)
        if overview_data:
            out = data_dir / "overview_data.json"
            out.write_text(json.dumps(overview_data, indent=2), encoding="utf-8")
            print(f"Wrote overview data to {out}")

    # --- Assembly guide ---
    if assy_folder.is_dir():
        print("\n=== Assembly guide pages ===")
        blocks = extract_assembly(client, assy_folder)
        if blocks:
            write_assembly_data(blocks, data_dir / "assembly_data.py")

    # --- Cut guide ---
    if cut_folder.is_dir():
        print("\n=== Cut guide pages ===")
        rows = extract_cut_guide(client, cut_folder)
        if rows:
            write_cut_guide_data(rows, data_dir / "cut_guide_data.py")

    # --- Run downstream scripts ---
    print("\n=== Running generate.py ===")
    subprocess.run([sys.executable, "generate.py"], check=False)

    print("\n=== Running tracking.py ===")
    subprocess.run([sys.executable, "tracking.py"], check=False)

    print("\nDone.")


if __name__ == "__main__":
    main()
