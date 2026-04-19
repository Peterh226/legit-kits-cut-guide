"""
Legit Kits Cut Guide Extractor
================================
Uses Claude vision API to extract structured data from scanned quilt pattern
images, then writes data files and runs generate.py / tracking.py.

Usage:
    python extract.py <pattern_folder>

Input folder layout:
    <pattern_folder>/
    ├── cut/       cut_001.jpg, cut_002.jpg, ...   (cut guide pages)
    ├── assy/      assy_001.jpg, assy_002.jpg, ...  (assembly guide pages)
    └── overview/  overview_001.jpg, ...            (overview/color guide pages)

Processing order:
    1. overview/ — builds the master fabric list (code, name, SKU) used to
                   correct any missing or misread fabric codes in cut guide data
    2. assy/     — extracts block assembly fragment lists
    3. cut/      — extracts piece rows; fabric codes are resolved against the
                   master fabric list from step 1

Output (written into data/ in the current directory):
    data/overview_data.json   — master fabric list + other overview metadata
    data/assembly_data.py     — block -> fragment mapping
    data/cut_guide_data.py    — all piece rows

Then automatically runs generate.py and tracking.py.

Requirements:
    pip install anthropic pillow
    ANTHROPIC_API_KEY must be set in the environment (or in a .env file).
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

MAX_BYTES = 4 * 1024 * 1024  # stay under the 5 MB API limit

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
        if quality > 50:
            quality -= 10
        else:
            w, h = img.size
            img = img.resize((w * 3 // 4, h * 3 // 4), Image.LANCZOS)


def sorted_images(folder: Path, prefix: str) -> list[Path]:
    """Return sorted image files whose name starts with <prefix> followed by underscores and digits."""
    pattern = re.compile(rf"^{re.escape(prefix)}_+\d+\.(png|jpg|jpeg)$", re.IGNORECASE)
    files = [p for p in folder.iterdir() if pattern.match(p.name)]
    return sorted(files, key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)))


def call_claude(client: anthropic.Anthropic, image_path: Path, prompt: str) -> str:
    """Send a single image to Claude and return the text response."""
    b64 = encode_image(image_path)
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=16000,
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


def _parse_json(raw: str, source: str) -> list | dict | None:
    """Strip markdown fences and parse JSON; return None on error."""
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  WARNING: JSON parse error for {source}: {e}")
        print(f"           Raw response (first 500 chars): {raw[:500]}")
        return None


# ---------------------------------------------------------------------------
# Master fabric list (from overview / color guide)
# ---------------------------------------------------------------------------

def build_fabric_lookup(overview_data: list[dict]) -> dict[str, str]:
    """
    Build a case-insensitive fabric name -> code mapping from the overview data.
    Uses only pages identified as the Color Guide fabric list (the authoritative pages).
    Falls back to any page that has a fabrics list if no color guide pages found.
    """
    lookup: dict[str, str] = {}  # lowercase name -> code

    color_guide_pages = [
        p for p in overview_data
        if "Color Guide" in p.get("document_type", "") or
           "Color Guide" in p.get("guide_type", "")
    ]
    source_pages = color_guide_pages if color_guide_pages else overview_data

    for page in source_pages:
        for fabric in page.get("fabrics", []):
            code = fabric.get("code", "").strip()
            name = fabric.get("name", "").strip()
            if code and name:
                lookup[name.lower()] = code

    return lookup


def resolve_fabric_codes(rows: list[dict], lookup: dict[str, str]) -> tuple[list[dict], int]:
    """
    For any row with a missing or suspect fabric_code, look up the correct code
    from the master fabric list using the fabric_name.
    Returns (corrected_rows, number_of_fixes).
    """
    fixed = 0
    result = []
    for row in rows:
        code = (row.get("fabric_code") or "").strip()
        name = (row.get("fabric_name") or "").strip()
        # Resolve if code is empty or doesn't look like a valid 1-2 letter code
        if (not code or not re.match(r"^[A-Z]{1,2}$", code)) and name:
            resolved = lookup.get(name.lower())
            if resolved:
                row = dict(row, fabric_code=resolved)
                fixed += 1
        result.append(row)
    return result, fixed


# ---------------------------------------------------------------------------
# Overview extraction
# ---------------------------------------------------------------------------

OVERVIEW_PROMPT = """\
This is a page from a Legit Kits quilt overview / color guide.
Extract any structured information visible: fabric list, fabric codes, SKUs, yardage,
quilt dimensions, block counts, or other metadata.

For fabric lists, extract each fabric as:
  {"code": "AF", "name": "Saffron", "sku": "1320", "yardage": "Fat 1/8YD"}

Return a JSON object with whatever fields are present, for example:
{
  "quilt_name": "Land of the Free",
  "document_type": "Color Guide - Fabric List by Code",
  "fabrics": [
    {"code": "AF", "name": "Saffron", "sku": "1320", "yardage": "Fat 1/8YD"}
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
        print(f"  [overview] Processing {img.name} ...")
        raw = call_claude(client, img, OVERVIEW_PROMPT)
        data = _parse_json(raw, img.name)
        if data:
            results.append(data)

    return results


# ---------------------------------------------------------------------------
# Cut guide extraction
# ---------------------------------------------------------------------------

def _build_cut_guide_prompt(fabric_lookup: dict[str, str]) -> str:
    """Build the cut guide prompt, injecting the known fabric list if available."""
    fabric_hint = ""
    if fabric_lookup:
        pairs = ", ".join(f"{code}={name.title()}" for name, code in sorted(fabric_lookup.items()))
        fabric_hint = f"\nKnown fabric codes for this pattern: {pairs}\nUse these codes exactly when you can identify the fabric by name.\n"

    return f"""\
This is a page from a Legit Kits quilt cut guide. Each page covers one or more fabrics.
For each fabric on this page, extract ALL piece entries.
{fabric_hint}
A fabric section has:
- Fabric code (short code like AF, BT, etc.)
- Fabric name (e.g. Saffron, Chocolate)
- SKU (product code)
- Fabric size (e.g. 2.5" x 44", Fat Quarter)
- A list of pieces, each with:
  - Piece number (integer, from circled numbers)
  - Template code (e.g. F3m, A4a, B7c — row letter + column number + optional segment letter)
  - Quantity (the number in parentheses after the template code; if absent use 1)

IMPORTANT: The piece list is split across two areas of the page — read ALL of them.
The page number is printed at the bottom of the cut guide page.

Return ONLY a JSON array. Each element represents one piece row:
[
  {{
    "fabric_code": "AF",
    "fabric_name": "Saffron",
    "sku": "1320",
    "fabric_size": "Fat 1/8YD",
    "piece_num": 1,
    "template_code": "F3m",
    "quantity": 3,
    "page": 1
  }}
]

If a field is not visible, use null. Do not include any text outside the JSON array.
"""


def extract_cut_guide(
    client: anthropic.Anthropic,
    cut_folder: Path,
    fabric_lookup: dict[str, str],
) -> list[dict]:
    images = sorted_images(cut_folder, "cut")
    if not images:
        print("  [cut] No images found — skipping.")
        return []

    prompt = _build_cut_guide_prompt(fabric_lookup)
    all_rows: list[dict] = []
    for img in images:
        print(f"  [cut] Processing {img.name} ...")
        raw = call_claude(client, img, prompt)
        rows = _parse_json(raw, img.name)
        if rows is not None:
            all_rows.extend(rows)
            print(f"         -> {len(rows)} piece rows")

    # Post-process: fill in any remaining missing/bad codes from master list
    all_rows, fixed = resolve_fabric_codes(all_rows, fabric_lookup)
    if fixed:
        print(f"  [cut] Resolved {fixed} fabric codes from color guide master list")

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
        print(f"  [assy] Processing {img.name} ...")
        raw = call_claude(client, img, ASSY_PROMPT)
        entries = _parse_json(raw, img.name)
        if entries:
            for entry in entries:
                blocks[entry["block_id"]] = entry["fragments"]
            print(f"         -> {len(entries)} blocks")

    # Fill in single-fragment blocks for any grid position not in the assembly guide
    all_blocks = [f"{r}{c}" for r in "ABCDEFGH" for c in "12345678"]
    complete: dict[str, list[str]] = {}
    added = 0
    for block_id in all_blocks:
        if block_id in blocks:
            complete[block_id] = blocks[block_id]
        else:
            complete[block_id] = [block_id]
            added += 1
    print(f"  [assy] Added {added} single-fragment blocks -> {len(complete)} total")

    return complete


# ---------------------------------------------------------------------------
# Assembly guide visual data extraction
# ---------------------------------------------------------------------------

ASSY_VISUAL_PROMPT = """\
This is a page from a Legit Kits quilt assembly guide.

For each block that appears on this page, extract two things:

1. DIAGRAM — if a visual block diagram appears (circles with fragment labels, dashed sewing lines):
   - block_id: e.g. "G7"
   - bbox: bounding box of the entire diagram as [left%, top%, right%, bottom%]
     where values are percentages of the full image width/height (0-100)
   - circles: for each circle containing a fragment label (e.g. G7a, G7b):
     {"fragment_id": "G7a", "cx": %, "cy": %}
     where cx/cy are the circle center as % of image dimensions
   - sewing_sequence: list of sewing steps in order, e.g.
     ["Sew G7(b) to G7(c)", "Sew G7(bc) to G7(d)", ...]
     Extract these from boxed numbers and dashed lines if visible on the diagram itself.

2. INSTRUCTIONS — if a text instruction panel appears for a block (even if no diagram):
   - block_id: e.g. "G7"
   - sewing_sequence: ordered list of steps from the text, e.g.
     ["Sew G7(b) to G7(c)", "Sew G7(bc) to G7(d)", ...]

Return a JSON object with two keys:
{
  "diagrams": [
    {
      "block_id": "G7",
      "bbox": [5, 45, 95, 95],
      "circles": [
        {"fragment_id": "G7a", "cx": 8, "cy": 10},
        {"fragment_id": "G7b", "cx": 35, "cy": 8}
      ],
      "sewing_sequence": ["Sew G7(b) to G7(c)", "..."]
    }
  ],
  "instructions": [
    {
      "block_id": "G7",
      "sewing_sequence": ["Sew G7(b) to G7(c)", "Sew G7(bc) to G7(d)", "..."]
    }
  ]
}

Notes:
- A block may appear in diagrams only, instructions only, or both (if on the same page).
- For circle positions, use the CENTER of the circle, as % of the full image dimensions.
- bbox values are % of image width (left/right) and height (top/bottom).
- If a section is empty, return an empty list for that key.
- Do not include any text outside the JSON object.
"""


def extract_assembly_visual(
    client: anthropic.Anthropic,
    assy_folder: Path,
    data_dir: Path,
) -> dict:
    """
    Extract diagram bounding boxes, circle positions, and sewing steps from
    each assembly guide image. Writes data/assembly_guide.json and copies
    images to quilt-tracker-app/static/assy/.
    """
    images = sorted_images(assy_folder, "assy")
    if not images:
        print("  [assy-visual] No images found — skipping.")
        return {}

    # Output: block_id -> {image, bbox, circles, sewing_sequence}
    guide: dict = {}          # block_id -> merged data
    image_map: dict = {}      # image filename -> list of block_ids with diagrams

    for img in images:
        print(f"  [assy-visual] Processing {img.name} ...")
        raw = call_claude(client, img, ASSY_VISUAL_PROMPT)
        data = _parse_json(raw, img.name)
        if not data:
            continue

        page_diagrams    = data.get("diagrams", [])
        page_instructions = data.get("instructions", [])

        for d in page_diagrams:
            bid = d.get("block_id")
            if not bid:
                continue
            guide.setdefault(bid, {})
            guide[bid]["image"]           = img.name
            guide[bid]["bbox"]            = d.get("bbox", [])
            guide[bid]["circles"]         = d.get("circles", [])
            if d.get("sewing_sequence"):
                guide[bid]["sewing_sequence"] = d["sewing_sequence"]
            image_map.setdefault(img.name, []).append(bid)

        for inst in page_instructions:
            bid = inst.get("block_id")
            if not bid:
                continue
            guide.setdefault(bid, {})
            # Only set sewing_sequence from instructions if not already set by diagram
            if "sewing_sequence" not in guide[bid] and inst.get("sewing_sequence"):
                guide[bid]["sewing_sequence"] = inst["sewing_sequence"]
                guide[bid].setdefault("instruction_image", img.name)

        print(f"         -> {len(page_diagrams)} diagrams, {len(page_instructions)} instruction blocks")

    # Write JSON
    out = data_dir / "assembly_guide.json"
    out.write_text(json.dumps(guide, indent=2), encoding="utf-8")
    print(f"  [assy-visual] Wrote {len(guide)} blocks to {out}")

    # Copy assy images to quilt-tracker-app/static/assy/
    static_assy = Path(__file__).parent / "quilt-tracker-app" / "static" / "assy"
    static_assy.mkdir(parents=True, exist_ok=True)
    for img in images:
        dest = static_assy / img.name
        dest.write_bytes(img.read_bytes())
    print(f"  [assy-visual] Copied {len(images)} images to {static_assy}")

    return guide


# ---------------------------------------------------------------------------
# Write data files
# ---------------------------------------------------------------------------

def write_cut_guide_data(rows: list[dict], out_path: Path) -> None:
    """Write rows as a Python DATA tuple list."""
    lines = [
        '"""',
        "Auto-generated cut guide data.",
        '"""',
        "",
        "DATA = [",
    ]
    for r in rows:
        fabric_code = repr((r.get("fabric_code") or "").strip())
        fabric_name = repr(r.get("fabric_name") or "")
        sku         = repr(r.get("sku") or "")
        fabric_size = repr(r.get("fabric_size") or "")
        piece_num   = int(r.get("piece_num") or 0)
        template    = repr(r.get("template_code") or "")
        quantity    = int(r.get("quantity") or 1)
        page        = int(r.get("page") or 0)
        lines.append(
            f"    ({fabric_code}, {fabric_name}, {sku}, {fabric_size}, "
            f"{piece_num}, {template}, {quantity}, {page}),"
        )
    lines.append("]")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")


def write_assembly_data(blocks: dict[str, list[str]], out_path: Path) -> None:
    """Write blocks as a Python BLOCKS dict."""
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

    # --- Overview first — builds master fabric lookup ---
    fabric_lookup: dict[str, str] = {}
    if overview_folder.is_dir():
        print("\n=== Overview pages ===")
        overview_data = extract_overview(client, overview_folder)
        if overview_data:
            out = data_dir / "overview_data.json"
            out.write_text(json.dumps(overview_data, indent=2), encoding="utf-8")
            print(f"Wrote overview data to {out}")
            fabric_lookup = build_fabric_lookup(overview_data)
            print(f"Built master fabric list: {len(fabric_lookup)} fabrics")

    # --- Assembly guide ---
    if assy_folder.is_dir():
        print("\n=== Assembly guide pages (structure) ===")
        blocks = extract_assembly(client, assy_folder)
        if blocks:
            write_assembly_data(blocks, data_dir / "assembly_data.py")

        print("\n=== Assembly guide pages (visual) ===")
        extract_assembly_visual(client, assy_folder, data_dir)

    # --- Cut guide (uses master fabric list for code resolution) ---
    if cut_folder.is_dir():
        print("\n=== Cut guide pages ===")
        rows = extract_cut_guide(client, cut_folder, fabric_lookup)
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
