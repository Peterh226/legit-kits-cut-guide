"""
Legit Kits Cut Guide Extractor
================================
Uses Claude vision API to extract structured data from scanned quilt pattern
images, with per-page checkpointing, resume, and validation.

Usage:
    python extract.py <pattern_folder> [options]

    pattern_folder      Folder containing cut/, assy/, overview/ subfolders

Options:
    --quilt-id ID       Quilt identifier (default: lowercased folder name)
    --stage STAGE       Which stage to run: cut, assy, overview, colors, all (default: all)
    --resume            Skip already-processed pages in the staging file
    --page N            Process only page N (1-based)
    --pages START-END   Process a range of pages, e.g. --pages 5-10
    --finalize          Write final output files from staging without API calls
    --status            Print per-stage staging status and exit
    --dry-run           Call API and validate, but don't write output files
    --api-key KEY       Anthropic API key (overrides ANTHROPIC_API_KEY env var)
    --no-rotate-cuts    Don't rotate cut images 90° CCW on copy. Default behavior
                        assumes the standard portrait scan convention; use this if
                        scans are already upright.
    --fix-rotation      After copying cut images, run a per-page Haiku rotation
                        check (fallback for non-standard scans; ~$0.01/image)

Output (written to quilts/<quilt-id>/):
    overview_data.json      Master fabric list and metadata
    assembly_data.py        Block -> fragment mapping
    assembly_guide.json     Visual assembly data
    cut_guide_data.py       All piece rows
    fabric_colors.json      Approximate hex color per fabric code (from --stage colors)

Staging files (quilts/<quilt-id>/, one per stage):
    overview_raw.json / assy_raw.json / assy_visual_raw.json / cut_raw.json / colors_raw.json
    Each is a dict keyed by image filename; each value has status, data, warnings.
    Re-running with --resume skips entries already marked ok or warning.
    Re-running with --page N or --pages START-END overwrites just those entries.
"""

import argparse
import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from PIL import Image
import io


# ---------------------------------------------------------------------------
# Environment / setup
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

MAX_BYTES = 4 * 1024 * 1024

def encode_image(path: Path) -> str:
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
    pattern = re.compile(rf"^{re.escape(prefix)}_+\d+\.(png|jpg|jpeg)$", re.IGNORECASE)
    files = [p for p in folder.iterdir() if pattern.match(p.name)]
    return sorted(files, key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)))


def call_claude(client: anthropic.Anthropic, image_path: Path, prompt: str) -> str:
    b64 = encode_image(image_path)
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return msg.content[0].text


def _img_to_b64(img: Image.Image) -> str:
    """Encode a PIL image to base64 JPEG, shrinking if needed to fit MAX_BYTES."""
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


def call_claude_multi(client: anthropic.Anthropic, b64_images: list[str], prompt: str) -> str:
    """Send multiple base64-encoded images plus a prompt to Claude."""
    image_blocks = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}
        for b64 in b64_images
    ]
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": image_blocks + [{"type": "text", "text": prompt}],
        }],
    )
    return msg.content[0].text


def _cut_page_images(img_path: Path) -> list[str]:
    """Encode a cut page as three base64 JPEGs:
    [0] full page (for layout / fabric metadata)
    [1] left half (higher effective resolution for left-side text)
    [2] right half (higher effective resolution for right-side text)

    Splitting horizontally avoids Claude's downscale of the full 3229×2479 page,
    which otherwise renders thin segment-suffix characters (1 vs l vs n) ambiguous.
    """
    full = Image.open(img_path).convert("RGB")
    w, h = full.size
    left  = full.crop((0,     0, w // 2, h))
    right = full.crop((w // 2, 0, w,      h))
    return [_img_to_b64(full), _img_to_b64(left), _img_to_b64(right)]


def _parse_json(raw: str, source: str) -> list | dict | None:
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  WARNING: JSON parse error for {source}: {e}")
        print(f"           Raw (first 500 chars): {raw[:500]}")
        return None


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Staging helpers
# ---------------------------------------------------------------------------

def load_staging(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  WARNING: Could not load staging file {path}: {e}")
    return {}


def save_staging(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def print_staging_status(label: str, staging: dict, images: list[Path]) -> None:
    ok    = sum(1 for v in staging.values() if v.get("status") == "ok")
    warn  = sum(1 for v in staging.values() if v.get("status") == "warning")
    err   = sum(1 for v in staging.values() if v.get("status") == "error")
    done  = len(staging)
    total = len(images)

    print(f"\n=== {label} ===")
    print(f"  Pages : {done}/{total}  (ok={ok}, warning={warn}, error={err}, pending={total - done})")

    for name, v in staging.items():
        if v.get("status") == "warning":
            print(f"  WARN  {name}: {'; '.join(v.get('warnings', []))}")
    for name, v in staging.items():
        if v.get("status") == "error":
            print(f"  ERROR {name}: {v.get('error', '')}")


def filter_pages(images: list[Path], page: int | None, pages: str | None) -> list[Path]:
    if page is not None:
        idx = page - 1
        if 0 <= idx < len(images):
            return [images[idx]]
        print(f"  WARNING: --page {page} out of range (1-{len(images)})")
        return []
    if pages is not None:
        m = re.match(r"^(\d+)-(\d+)$", pages)
        if not m:
            sys.exit(f"Error: --pages must be START-END, got: {pages!r}")
        start, end = int(m.group(1)) - 1, int(m.group(2)) - 1
        return images[max(0, start) : end + 1]
    return images


def _should_skip(staging: dict, img_name: str, resume: bool) -> bool:
    return resume and staging.get(img_name, {}).get("status") in ("ok", "warning")


def _target_page_numbers(page: int | None, pages: str | None) -> set[int]:
    if page is not None:
        return {page}
    if pages is not None:
        m = re.match(r"^(\d+)-(\d+)$", pages)
        if m:
            return set(range(int(m.group(1)), int(m.group(2)) + 1))
    return set()


def _load_existing_cut_rows(out_path: Path) -> list[dict]:
    """Import existing cut_guide_data.py and return its DATA as a list of dicts."""
    if not out_path.exists():
        return []
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_cut_guide_data_existing", out_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        keys = ["fabric_code", "fabric_name", "sku", "fabric_size",
                "piece_num", "template_code", "quantity", "page"]
        return [dict(zip(keys, row)) for row in mod.DATA]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Fabric lookup
# ---------------------------------------------------------------------------

def build_fabric_lookup(overview_data: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
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
    fixed = 0
    result = []
    for row in rows:
        code = (row.get("fabric_code") or "").strip()
        name = (row.get("fabric_name") or "").strip()
        if (not code or not re.match(r"^[A-Z]{1,2}$", code)) and name:
            resolved = lookup.get(name.lower())
            if resolved:
                row = dict(row, fabric_code=resolved)
                fixed += 1
        result.append(row)
    return result, fixed


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_cut_rows(rows: list, img_name: str, fabric_lookup: dict | None = None) -> list[str]:
    if not isinstance(rows, list):
        return ["Response is not a list"]
    warnings = []
    if len(rows) == 0:
        warnings.append("No rows extracted")
        return warnings

    # Per-row field checks
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            warnings.append(f"Row {i}: not a dict")
            continue
        if not row.get("piece_num"):
            warnings.append(f"Row {i}: missing piece_num")
        if not row.get("template_code"):
            warnings.append(f"Row {i}: missing template_code")
        code = (row.get("fabric_code") or "").strip()
        if code and not re.match(r"^[A-Z]{1,2}$", code):
            warnings.append(f"Row {i}: suspect fabric_code {code!r}")
        # Check 4: fabric code exists in overview data
        if fabric_lookup and code and code not in fabric_lookup.values():
            warnings.append(f"Row {i}: fabric_code {code!r} not in overview data")

    valid_rows = [r for r in rows if isinstance(r, dict)]

    # Check 1: declared piece count matches extracted count per fabric
    from collections import defaultdict as _dd
    by_fabric: dict = _dd(list)
    for row in valid_rows:
        code = (row.get("fabric_code") or "").strip()
        if code:
            by_fabric[code].append(row)
    for code, fabric_rows in by_fabric.items():
        declared = next((r.get("fabric_piece_count") for r in fabric_rows
                         if r.get("fabric_piece_count") is not None), None)
        if declared is not None and int(declared) != len(fabric_rows):
            warnings.append(
                f"Fabric {code}: declared {int(declared)} pieces but extracted {len(fabric_rows)}"
            )

    # Check 2: duplicate piece_num within same fabric
    for code, fabric_rows in by_fabric.items():
        seen: set = set()
        for row in fabric_rows:
            pn = row.get("piece_num")
            if pn is not None:
                if pn in seen:
                    warnings.append(f"Fabric {code}: duplicate piece_num {pn}")
                seen.add(pn)

    return warnings


def validate_assy_entries(entries: list, img_name: str, grid_rows: str = "ABCDEFGH", grid_cols: int = 8) -> list[str]:
    if not isinstance(entries, list):
        return ["Response is not a list"]
    warnings = []
    if len(entries) == 0:
        warnings.append("No blocks extracted")
        return warnings
    row_pat = "[" + grid_rows[0] + "-" + grid_rows[-1] + "]"
    col_pat = f"[1-{grid_cols}]" if grid_cols <= 9 else f"([1-9]|[1-{grid_cols // 10}][0-9])"
    block_re = re.compile(rf"^{row_pat}{col_pat}$")
    for e in entries:
        if not isinstance(e, dict):
            warnings.append(f"Entry is not a dict: {e!r}")
            continue
        bid = e.get("block_id", "")
        if not block_re.match(bid):
            warnings.append(f"Suspect block_id: {bid!r}")
        if not e.get("fragments"):
            warnings.append(f"Block {bid}: no fragments")
    return warnings


def validate_overview(data: dict, img_name: str) -> list[str]:
    if not isinstance(data, dict):
        return ["Response is not a dict"]
    if not data.get("fabrics") and not data.get("quilt_name"):
        return ["No fabrics or quilt_name — may be a non-data page"]
    return []


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

OVERVIEW_PROMPT = """\
This is a page from a Legit Kits quilt overview / color guide.
Extract any structured information visible: fabric list, fabric codes, SKUs, yardage,
quilt dimensions, block counts, or other metadata.

For fabric lists, extract each fabric as:
  {"code": "AF", "name": "Saffron", "sku": "1320", "yardage": "Fat 1/8YD"}

If the page shows a "Pattern Side" or "Finished Quilt" block grid, include a "grid" field
with the row and column labels EXACTLY as printed — some quilts have letters across the top
(columns) and numbers down the side (rows); others have numbers across and letters down.
Report whichever labels appear for rows vs columns:
  "grid": {"rows": ["A","B","C","D"], "columns": [1,2,3,4]}   // letters down side
  "grid": {"rows": [1,2,3,4], "columns": ["A","B","C","D"]}   // letters across top

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
  {"block_id": "B7", "fragments": ["B7a","B7b","B7c","B7d","B7e","B7f","B7g","B7h"]}
]

Do not include any text outside the JSON array.
"""

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

2. INSTRUCTIONS — if a text instruction panel appears for a block (even if no diagram):
   - block_id: e.g. "G7"
   - sewing_sequence: ordered list of steps from the text

Return a JSON object:
{
  "diagrams": [
    {
      "block_id": "G7",
      "bbox": [5, 45, 95, 95],
      "circles": [{"fragment_id": "G7a", "cx": 8, "cy": 10}],
      "sewing_sequence": ["Sew G7(b) to G7(c)", "..."]
    }
  ],
  "instructions": [
    {"block_id": "G7", "sewing_sequence": ["Sew G7(b) to G7(c)", "..."]}
  ]
}

If a section is empty return an empty list. Do not include text outside the JSON object.
"""


def _build_cut_prompt(fabric_lookup: dict[str, str]) -> str:
    fabric_hint = ""
    if fabric_lookup:
        pairs = ", ".join(f"{code}={name.title()}" for name, code in sorted(fabric_lookup.items()))
        fabric_hint = f"\nKnown fabric codes for this pattern: {pairs}\nUse these codes exactly when you can identify the fabric by name.\n"
    return f"""\
You are given THREE images of the same Legit Kits cut guide page:
  Image 1 — the full page (use for layout, fabric metadata, counting circled
            piece numbers in the diagrams).
  Image 2 — the LEFT half of the page (zoomed in, higher effective resolution
            for any text/template codes printed in the left half).
  Image 3 — the RIGHT half of the page (zoomed in, higher effective resolution
            for any text/template codes printed in the right half).

When reading template codes — especially the smaller segment-suffix character
that follows the block ID — ALWAYS consult the matching half-image (image 2
or 3) before deciding what character it is. The half-images preserve far more
pixel detail than the full page, which is the difference between confidently
reading a digit "1" vs. mistaking it for "l" or "n".

Each page covers one or more fabrics. For each fabric on this page, extract
ALL piece entries.
{fabric_hint}
A fabric section has:
- Fabric code (short code like AF, BT, etc.)
- Fabric name (e.g. Saffron, Chocolate)
- SKU (product code)
- Fabric size (e.g. 2.5" x 44", Fat Quarter)
- A list of pieces, each with:
  - Piece number (integer, from circled numbers)
  - Template code (e.g. F3m, A4a, B7c — row letter + column number + optional segment letter)
  - Sew sequence (the number in parentheses after the template code — this is the piece's
    position in the sewing order within its segment, NOT a quantity. If no parentheses
    appear, the segment contains only this single piece, so the sequence value is 1.)
- fabric_piece_count: the TOTAL number of pieces listed for this fabric on this page
  (count every circled number visible in the piece list, including any printed below the image)

IMPORTANT: The piece list is split across two areas of the page — read ALL of them,
including any entries printed BELOW the diagram image.
The page number is printed at the bottom of the cut guide page.

IMPORTANT: Template codes use a mixed font size where the block ID portion (e.g. "B1",
"C2") appears slightly larger than the segment suffix that follows. Segment suffixes are
either a lowercase letter (a-z) OR a digit/multi-digit number that continues after the
letters run out (1, 2, ..., 11, 12, ...). Examples of templates: B1a, B1z, B11, B12,
C210. Do NOT insert any separator character between block ID and suffix. Do NOT confuse
the segment suffix with the parenthesised sew sequence. For example: "B12(3)" means
template_code="B12", sew_sequence=3 — not template_code="B1", sew_sequence=2. Similarly
"C210(1)" means template_code="C210", sew_sequence=1.

CRITICAL — character disambiguation for the segment suffix:
- The segment suffix is printed in a SMALLER, THINNER font than the block ID. At low
  resolution a small thin digit "1" can be mistaken for a lowercase letter such as
  "l", "i", "n", "h", or "r". If the character is a SINGLE thin vertical stroke with
  no curves, humps, dots, or descenders, prefer reading it as the digit "1".
- Lowercase letters have distinguishing features: "n" has a rounded hump, "h" has a
  hump and ascender, "l" has a clear ascender to the top, "i" has a dot, "r" has a
  flag at the top. If you do not see any of these features, the character is a digit.
- A lowercase letter "l" (the letter ell) should always be RECORDED IN UPPERCASE as
  "L" (e.g. C1L, not C1l) so it is never confused with the segment-after-z digit "1".
  Only use "L" if the original character clearly has an ascender / is the letter ell.

If no parentheses follow the template code on a row, the segment contains exactly one
piece, so the sew sequence value is 1.

Return ONLY a JSON array. Each element represents one piece row. NOTE: the JSON key is
named "quantity" for backward compatibility, but its value is the sew sequence number
described above (NOT a quantity).
[
  {{
    "fabric_code": "AF",
    "fabric_name": "Saffron",
    "sku": "1320",
    "fabric_size": "Fat 1/8YD",
    "piece_num": 1,
    "template_code": "F3m",
    "quantity": 3,
    "page": 1,
    "fabric_piece_count": 6
  }}
]

fabric_piece_count must be the same value for every row belonging to the same fabric.
If a field is not visible, use null. Do not include any text outside the JSON array.
"""


# ---------------------------------------------------------------------------
# Stage: overview
# ---------------------------------------------------------------------------

def run_overview(
    client: anthropic.Anthropic,
    folder: Path,
    staging_path: Path,
    resume: bool,
    page: int | None,
    pages: str | None,
    dry_run: bool,
) -> list[dict]:
    # Skip overview_000.jpg — that's the cover page, copied separately to cover.jpg.
    images = [p for p in sorted_images(folder, "overview") if _page_num(p) > 0]
    staging = load_staging(staging_path)
    targets = filter_pages(images, page, pages)

    for img in targets:
        if _should_skip(staging, img.name, resume):
            print(f"  [overview] Skip {img.name}")
            continue
        print(f"  [overview] {img.name} ...", end=" ", flush=True)
        try:
            raw = call_claude(client, img, OVERVIEW_PROMPT)
            data = _parse_json(raw, img.name)
            if data is None:
                staging[img.name] = {"status": "error", "error": "JSON parse failed", "raw": raw[:1000], "ts": _ts()}
                print("ERROR: JSON parse failed")
            else:
                warnings = validate_overview(data, img.name)
                staging[img.name] = {"status": "warning" if warnings else "ok", "data": data, "warnings": warnings, "ts": _ts()}
                print(f"ok" if not warnings else f"WARNING: {'; '.join(warnings)}")
        except Exception as e:
            staging[img.name] = {"status": "error", "error": str(e), "ts": _ts()}
            print(f"ERROR: {e}")
        if not dry_run:
            save_staging(staging_path, staging)

    return [v["data"] for v in staging.values() if v.get("status") in ("ok", "warning") and "data" in v]


# ---------------------------------------------------------------------------
# Stage: assembly
# ---------------------------------------------------------------------------

def run_assy(
    client: anthropic.Anthropic,
    folder: Path,
    staging_path: Path,
    visual_staging_path: Path,
    resume: bool,
    page: int | None,
    pages: str | None,
    dry_run: bool,
    grid_rows: str = "ABCDEFGH",
    grid_cols: int = 8,
) -> tuple[dict[str, list[str]], dict]:
    images = sorted_images(folder, "assy")
    staging        = load_staging(staging_path)
    visual_staging = load_staging(visual_staging_path)
    targets = filter_pages(images, page, pages)

    for img in targets:
        # Structure pass
        if not _should_skip(staging, img.name, resume):
            print(f"  [assy] {img.name} (structure) ...", end=" ", flush=True)
            try:
                raw = call_claude(client, img, ASSY_PROMPT)
                entries = _parse_json(raw, img.name)
                if entries is None:
                    staging[img.name] = {"status": "error", "error": "JSON parse failed", "raw": raw[:1000], "ts": _ts()}
                    print("ERROR: JSON parse failed")
                else:
                    warnings = validate_assy_entries(entries, img.name, grid_rows, grid_cols)
                    staging[img.name] = {"status": "warning" if warnings else "ok", "data": entries, "warnings": warnings, "ts": _ts()}
                    print(f"{len(entries)} blocks" if not warnings else f"WARNING: {'; '.join(warnings)}")
            except Exception as e:
                staging[img.name] = {"status": "error", "error": str(e), "ts": _ts()}
                print(f"ERROR: {e}")
            if not dry_run:
                save_staging(staging_path, staging)

        # Visual pass
        if not _should_skip(visual_staging, img.name, resume):
            print(f"  [assy] {img.name} (visual) ...", end=" ", flush=True)
            try:
                raw = call_claude(client, img, ASSY_VISUAL_PROMPT)
                data = _parse_json(raw, img.name)
                if data is None:
                    visual_staging[img.name] = {"status": "error", "error": "JSON parse failed", "raw": raw[:1000], "ts": _ts()}
                    print("ERROR: JSON parse failed")
                else:
                    n = len(data.get("diagrams", []))
                    visual_staging[img.name] = {"status": "ok", "data": data, "warnings": [], "ts": _ts()}
                    print(f"{n} diagrams, {len(data.get('instructions', []))} instructions")
            except Exception as e:
                visual_staging[img.name] = {"status": "error", "error": str(e), "ts": _ts()}
                print(f"ERROR: {e}")
            if not dry_run:
                save_staging(visual_staging_path, visual_staging)

    return _assemble_blocks(staging, grid_rows, grid_cols), _assemble_guide(visual_staging)


def _assemble_blocks(staging: dict, grid_rows: str = "ABCDEFGH", grid_cols: int = 8) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    for v in staging.values():
        if v.get("status") in ("ok", "warning") and "data" in v:
            for entry in v["data"]:
                blocks[entry["block_id"]] = entry["fragments"]
    all_block_ids = [f"{r}{c}" for r in grid_rows for c in [str(n) for n in range(1, grid_cols + 1)]]
    complete = {}
    added = 0
    for bid in all_block_ids:
        if bid in blocks:
            complete[bid] = blocks[bid]
        else:
            complete[bid] = [bid]
            added += 1
    if added:
        print(f"  [assy] Added {added} single-fragment blocks -> {len(complete)} total")
    return complete


def _assemble_guide(visual_staging: dict) -> dict:
    guide: dict = {}
    for img_name, v in visual_staging.items():
        if v.get("status") not in ("ok", "warning") or "data" not in v:
            continue
        for d in v["data"].get("diagrams", []):
            bid = d.get("block_id")
            if not bid:
                continue
            guide.setdefault(bid, {}).update({"image": img_name, "bbox": d.get("bbox", []), "circles": d.get("circles", [])})
            if d.get("sewing_sequence"):
                guide[bid]["sewing_sequence"] = d["sewing_sequence"]
        for inst in v["data"].get("instructions", []):
            bid = inst.get("block_id")
            if not bid:
                continue
            guide.setdefault(bid, {})
            if "sewing_sequence" not in guide[bid] and inst.get("sewing_sequence"):
                guide[bid]["sewing_sequence"] = inst["sewing_sequence"]
                guide[bid].setdefault("instruction_image", img_name)
    return guide


# ---------------------------------------------------------------------------
# Stage: cut
# ---------------------------------------------------------------------------

def run_cut(
    client: anthropic.Anthropic,
    folder: Path,
    staging_path: Path,
    fabric_lookup: dict[str, str],
    resume: bool,
    page: int | None,
    pages: str | None,
    dry_run: bool,
) -> list[dict]:
    images = sorted_images(folder, "cut")
    staging = load_staging(staging_path)
    targets = filter_pages(images, page, pages)
    prompt = _build_cut_prompt(fabric_lookup)

    for img in targets:
        if _should_skip(staging, img.name, resume):
            print(f"  [cut] Skip {img.name}")
            continue
        print(f"  [cut] {img.name} ...", end=" ", flush=True)
        try:
            raw = call_claude_multi(client, _cut_page_images(img), prompt)
            rows = _parse_json(raw, img.name)
            if rows is None:
                staging[img.name] = {"status": "error", "error": "JSON parse failed", "raw": raw[:1000], "ts": _ts()}
                print("ERROR: JSON parse failed")
            else:
                rows, fixed = resolve_fabric_codes(rows, fabric_lookup)
                warnings = validate_cut_rows(rows, img.name, fabric_lookup)
                staging[img.name] = {"status": "warning" if warnings else "ok", "data": rows, "rows": len(rows), "warnings": warnings, "ts": _ts()}
                suffix = f", {fixed} codes resolved" if fixed else ""
                if warnings:
                    print(f"{len(rows)} rows{suffix} — WARNING: {'; '.join(warnings)}")
                else:
                    print(f"{len(rows)} rows{suffix}")
        except Exception as e:
            staging[img.name] = {"status": "error", "error": str(e), "ts": _ts()}
            print(f"ERROR: {e}")
        if not dry_run:
            save_staging(staging_path, staging)

    # Collect rows from all ok/warning entries in full staging, preserving image order
    all_rows: list[dict] = []
    for img in images:
        v = staging.get(img.name, {})
        if v.get("status") in ("ok", "warning") and "data" in v:
            all_rows.extend(v["data"])
    return all_rows


# ---------------------------------------------------------------------------
# Stage: colors
# ---------------------------------------------------------------------------

COLORS_PROMPT = """\
This is a Color Guide page from a Legit Kits quilt pattern.
Each row shows a fabric with its short code (like "AA", "DF", "LQ"), a colored swatch \
rectangle, and the fabric name.

For every fabric on this page, look at its colored swatch and return the approximate \
display color as a CSS hex code.

Return ONLY a JSON object mapping fabric code to hex color. Example:
{"AA": "#8B2E1F", "DF": "#FFC107", "CZ": "#F5EDD6"}

Guidelines:
- Match the dominant mid-tone of the swatch, not highlights or shadows
- For very dark / near-black fabrics use "#1a1a1a"
- For white / near-white fabrics use "#f0f0f0"
- If a code's swatch is unclear or ambiguous, omit it rather than guess
- Do not include any text outside the JSON object
"""


def run_colors(
    client: anthropic.Anthropic,
    overview_folder: Path,
    overview_staging_path: Path,
    out_dir: Path,
    resume: bool,
    dry_run: bool,
) -> dict[str, str]:
    overview_staging = load_staging(overview_staging_path)
    color_guide_imgs: list[tuple[str, Path]] = []
    for img_name, v in overview_staging.items():
        data = v.get("data", {})
        doc_type = data.get("document_type", "") if isinstance(data, dict) else ""
        if "Color Guide" in doc_type:
            img_path = overview_folder / img_name
            if img_path.exists():
                color_guide_imgs.append((img_name, img_path))

    if not color_guide_imgs:
        print("  [colors] No Color Guide pages found in overview staging — run overview stage first")
        return {}

    print(f"  [colors] Found {len(color_guide_imgs)} Color Guide pages")
    colors_staging_path = out_dir / "colors_raw.json"
    staging = load_staging(colors_staging_path)

    for img_name, img_path in color_guide_imgs:
        if _should_skip(staging, img_name, resume):
            print(f"  [colors] Skip {img_name}")
            continue
        print(f"  [colors] {img_name} ...", end=" ", flush=True)
        try:
            raw  = call_claude(client, img_path, COLORS_PROMPT)
            data = _parse_json(raw, img_name)
            if not isinstance(data, dict):
                staging[img_name] = {"status": "error", "error": "Response is not a dict", "raw": raw[:500], "ts": _ts()}
                print("ERROR: not a dict")
            else:
                staging[img_name] = {"status": "ok", "data": data, "ts": _ts()}
                print(f"{len(data)} colors")
        except Exception as e:
            staging[img_name] = {"status": "error", "error": str(e), "ts": _ts()}
            print(f"ERROR: {e}")
        if not dry_run:
            save_staging(colors_staging_path, staging)

    merged: dict[str, str] = {}
    for v in staging.values():
        if v.get("status") in ("ok", "warning") and isinstance(v.get("data"), dict):
            merged.update(v["data"])
    return merged


# ---------------------------------------------------------------------------
# Write output files
# ---------------------------------------------------------------------------

def write_cut_guide_data(rows: list[dict], out_path: Path) -> None:
    lines = ['"""', "Auto-generated cut guide data.", '"""', "", "DATA = ["]
    for r in rows:
        lines.append(
            f"    ({repr((r.get('fabric_code') or '').strip())}, "
            f"{repr(r.get('fabric_name') or '')}, "
            f"{repr(r.get('sku') or '')}, "
            f"{repr(r.get('fabric_size') or '')}, "
            f"{int(r.get('piece_num') or 0)}, "
            f"{repr(r.get('template_code') or '')}, "
            f"{int(r.get('quantity') or 1)}, "
            f"{int(r.get('page') or 0)}),"
        )
    lines += ["]", ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {out_path}")


def _suffix_key(suffix: str) -> int:
    """Ordering key for segment suffixes: a=0..z=25 (L/l→11), then 1=26, 2=27, ..."""
    if suffix == 'L':
        return ord('l') - ord('a')
    if len(suffix) == 1 and suffix.isalpha():
        return ord(suffix.lower()) - ord('a')
    if suffix and all(c.isdigit() for c in suffix):
        return 26 + int(suffix) - 1
    return 999


def _load_blocks_if_available(out_dir: Path) -> dict:
    asm_path = out_dir / "assembly_data.py"
    if not asm_path.exists():
        return {}
    spec = importlib.util.spec_from_file_location("assembly_data", asm_path)
    mod  = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return getattr(mod, "BLOCKS", {})
    except Exception:
        return {}


def _repair_suffix_order(rows: list[dict], blocks: dict) -> tuple[list[dict], list[str]]:
    """Auto-correct letter-suffix segment codes that appear out of order on their cut page.

    A letter suffix appearing after a later letter (or any numeric suffix) in the same
    (page, fabric, block) group is a misread of a post-z numeric suffix.  When exactly
    one numeric segment for that block has the matching sew_seq not yet present, the code
    is corrected automatically.  Ambiguous cases are logged but left unchanged.

    Returns (repaired_rows, log_lines).
    """
    import re as _re

    def _block_of(tmpl):
        m = _re.match(r'^([A-Z]\d|\d[A-Z])', str(tmpl))
        return m.group(1) if m else None

    # Numeric segs per block
    numeric_segs_for_block: dict[str, list[str]] = {}
    for block, frags in blocks.items():
        nums = [f for f in frags if _re.search(r'\d+$', f[len(block):])]
        if nums:
            numeric_segs_for_block[block] = nums

    # Sew-seq sets per segment (for candidate filtering)
    by_seg: dict[str, set] = {}
    for r in rows:
        seg = r.get('template_code') or ''
        seq = r.get('quantity')
        if seg and isinstance(seq, int):
            by_seg.setdefault(seg, set()).add(seq)

    # Group rows by (page, fabric_code, block_id) preserving list order
    from collections import defaultdict as _dd
    groups: dict[tuple, list[int]] = _dd(list)
    for i, r in enumerate(rows):
        block = _block_of(r.get('template_code') or '')
        if block is None:
            continue
        key = (int(r.get('page') or 0), r.get('fabric_code') or '', block)
        groups[key].append(i)

    log: list[str] = []
    repaired = list(rows)

    for (page, fabric, block), idxs in sorted(groups.items()):
        sorted_idxs = sorted(idxs, key=lambda i: int(repaired[i].get('piece_num') or 0))
        max_key_seen = -1
        for i in sorted_idxs:
            r = repaired[i]
            tmpl = r.get('template_code') or ''
            suffix = tmpl[len(block):]
            key = _suffix_key(suffix)
            if key < max_key_seen and key < 26:
                # Letter suffix out of order — find candidates among numeric segs
                sew_seq = r.get('quantity')
                candidates = []
                if isinstance(sew_seq, int) and block in numeric_segs_for_block:
                    for nseg in numeric_segs_for_block[block]:
                        nkey = _suffix_key(nseg[len(block):])
                        if nkey > max_key_seen and sew_seq not in by_seg.get(nseg, set()):
                            candidates.append(nseg)
                if len(candidates) == 1:
                    new_tmpl = candidates[0]
                    log.append(
                        f"  [auto-repair] p{page} {fabric}: '{tmpl}'({sew_seq}) -> '{new_tmpl}' "
                        f"(suffix key {key} < max {max_key_seen})"
                    )
                    repaired[i] = dict(r, template_code=new_tmpl)
                    by_seg.setdefault(new_tmpl, set()).add(sew_seq)
                    by_seg[tmpl].discard(sew_seq)
                else:
                    cand_str = candidates if candidates else "(no candidate)"
                    log.append(
                        f"  [manual] p{page} {fabric}: '{tmpl}'({sew_seq}) out of order "
                        f"(suffix key {key} < max {max_key_seen}); candidates: {cand_str}"
                    )
            else:
                max_key_seen = max(max_key_seen, key)

    return repaired, log


def write_assembly_data(blocks: dict[str, list[str]], out_path: Path) -> None:
    lines = ['"""', "Auto-generated block assembly data.", '"""', "", "",
             "def _frags(block, letters):",
             '    return [f"{block}{c}" for c in letters]', "", "", "BLOCKS = {"]
    for block_id, frags in blocks.items():
        lines.append(f"    {repr(block_id)}: {repr(frags)},")
    lines += ["}", ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(blocks)} blocks to {out_path}")


def _copy_assy_images(assy_folder: Path, out_dir: Path) -> None:
    images = sorted_images(assy_folder, "assy")
    dest = out_dir / "assy"
    dest.mkdir(exist_ok=True)
    for img in images:
        (dest / img.name).write_bytes(img.read_bytes())
    print(f"Copied {len(images)} assy images to {dest}")

def _copy_cut_images(cut_folder: Path, out_dir: Path, rotate_ccw: int = 90) -> None:
    """Copy cut images into out_dir/cut, optionally rotating CCW by `rotate_ccw` degrees.

    Standard scan convention is portrait-fed pages with the title on the right edge,
    which read upright after 90° CCW. Pass rotate_ccw=0 if scans are already upright.
    """
    images = sorted_images(cut_folder, "cut")
    dest = out_dir / "cut"
    dest.mkdir(exist_ok=True)
    copied = 0
    for img in images:
        target = dest / img.name
        if target.exists():
            continue
        if rotate_ccw:
            Image.open(img).rotate(rotate_ccw, expand=True).save(target, quality=92)
        else:
            target.write_bytes(img.read_bytes())
        copied += 1
    suffix = f" (rotated {rotate_ccw}° CCW)" if rotate_ccw else ""
    print(f"Copied {copied} cut images to {dest} ({len(images)} total){suffix}")


def _fix_cut_rotation(client: anthropic.Anthropic, cut_dir: Path) -> None:
    """Check each cut image and rotate in-place if footer is not in lower-right."""
    images = sorted(cut_dir.glob("*.jpg"))
    if not images:
        return
    print(f"  [rotation] Checking {len(images)} cut images ...")
    rotated = 0
    for img_path in images:
        thumb = Image.open(img_path).convert("RGB")
        thumb.thumbnail((600, 800), Image.LANCZOS)
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=75)
        b64 = base64.standard_b64encode(buf.getvalue()).decode()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": (
                        "This is a scanned cut guide page from a quilt kit. "
                        "When correctly oriented: the page title appears at the TOP and "
                        "a footer reading 'Page X of Y' appears in the LOWER-RIGHT corner. "
                        "How many degrees CLOCKWISE must this image be rotated to reach "
                        "correct orientation? Reply with exactly one of: 0, 90, 180, 270"
                    )},
                ],
            }],
        )
        text = resp.content[0].text.strip().split()[0]
        try:
            deg = int(text)
            deg = deg if deg in (0, 90, 180, 270) else 0
        except ValueError:
            deg = 0
        if deg:
            rotated += 1
            print(f"  [rotation] {img_path.name}: rotating {deg}° CW")
            Image.open(img_path).rotate(-deg, expand=True).save(img_path, quality=92)
        else:
            print(f"  [rotation] {img_path.name}: ok")
    print(f"  [rotation] {rotated} of {len(images)} images needed rotation")


def _page_num(img: Path) -> int:
    m = re.search(r"(\d+)", img.stem)
    return int(m.group(1)) if m else -1


def _copy_overview_image(overview_folder: Path, out_dir: Path, rotate_ccw: int = 0) -> None:
    # Skip the optional cover page (overview_000.jpg); first grid photo is overview_001+.
    images = [p for p in sorted_images(overview_folder, "overview") if _page_num(p) > 0]
    if not images:
        return
    dest = out_dir / "quilt_overview.jpg"
    if dest.exists():
        print(f"  quilt_overview.jpg already exists — skipping (edit manually if needed)")
        return
    if rotate_ccw:
        rotated = Image.open(images[0]).rotate(rotate_ccw, expand=True)
        rotated.save(dest, quality=92)
        print(f"  Copied {images[0].name} -> quilt_overview.jpg (rotated {rotate_ccw}° CCW)")
    else:
        dest.write_bytes(images[0].read_bytes())
        print(f"  Copied {images[0].name} -> quilt_overview.jpg (replace manually if needed)")


def _copy_cover_image(overview_folder: Path, out_dir: Path) -> None:
    """Copy overview_000.jpg (if present) to quilts/<id>/cover.jpg. Always overwrites."""
    candidates = [p for p in sorted_images(overview_folder, "overview") if _page_num(p) == 0]
    if not candidates:
        return
    src = candidates[0]
    dest = out_dir / "cover.jpg"
    dest.write_bytes(src.read_bytes())
    print(f"  Copied {src.name} -> cover.jpg")


def _cross_check_metadata(out_dir: Path) -> None:
    """Compare colors_expected / pieces_expected in config.json metadata against
    actual counts from cut_guide_data.py. Prints a warning if they disagree."""
    config_path = out_dir / "config.json"
    if not config_path.exists():
        return
    config = json.loads(config_path.read_text(encoding="utf-8"))
    meta = config.get("metadata", {})
    if not meta.get("colors_expected") and not meta.get("pieces_expected"):
        return

    cut_path = out_dir / "cut_guide_data.py"
    if not cut_path.exists():
        return
    ns: dict = {}
    exec(cut_path.read_text(encoding="utf-8"), ns)
    rows = ns.get("DATA", [])
    actual_colors = len({r[0] for r in rows if r})
    actual_pieces = len(rows)

    issues = []
    if meta.get("colors_expected") is not None and meta["colors_expected"] != actual_colors:
        issues.append(f"colors: expected {meta['colors_expected']}, got {actual_colors}")
    if meta.get("pieces_expected") is not None and meta["pieces_expected"] != actual_pieces:
        issues.append(f"pieces: expected {meta['pieces_expected']}, got {actual_pieces}")
    if issues:
        print("\n=== Cross-check ===")
        for line in issues:
            print(f"  WARNING - {line}")
    else:
        print(f"\n  [cross-check] colors {actual_colors} OK  pieces {actual_pieces} OK")


def _configure_quilt(out_dir: Path) -> None:
    """Interactive prompt to capture cover-page metadata into config.json.

    Press Enter to keep the current value shown in brackets.
    """
    config_path = out_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    meta = dict(config.get("metadata", {}))

    print(f"\nConfigure metadata for: {out_dir.name}")
    print("(press Enter to keep the current value shown in brackets)\n")

    def ask_str(label: str, key: str) -> None:
        current = meta.get(key, "")
        default = f" [{current}]" if current else ""
        val = input(f"  {label}{default}: ").strip()
        if val:
            meta[key] = val

    def ask_int(label: str, key: str, valid: set[int] | None = None) -> None:
        current = meta.get(key)
        default = f" [{current}]" if current is not None else ""
        while True:
            val = input(f"  {label}{default}: ").strip()
            if not val:
                return
            try:
                n = int(val)
            except ValueError:
                print("    Not a number; try again.")
                continue
            if valid is not None and n not in valid:
                print(f"    Must be one of {sorted(valid)}.")
                continue
            meta[key] = n
            return

    ask_str("Finished size (e.g. 60 x 72 in)",        "finished_size")
    ask_int("Complexity (1=Faster, 2=Moderate, 3=Detailed)", "complexity", valid={1, 2, 3})
    ask_str("Design #",                                "design_number")
    ask_int("Colors expected (cross-check)",          "colors_expected")
    ask_int("Pieces expected (cross-check)",          "pieces_expected")

    config["metadata"] = meta
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"\nSaved metadata to {config_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Extract Legit Kits pattern data via Claude vision API")
    parser.add_argument("pattern_folder", nargs="?",
                        help="Folder with cut/, assy/, overview/ subfolders (optional with --configure + --quilt-id)")
    parser.add_argument("--quilt-id",  help="Quilt identifier (default: lowercased folder name)")
    parser.add_argument("--stage",     choices=["cut", "assy", "overview", "colors", "all"], default="all")
    parser.add_argument("--resume",    action="store_true", help="Skip already-processed pages")
    parser.add_argument("--page",      type=int,  help="Process only this page (1-based)")
    parser.add_argument("--pages",     help="Process page range e.g. 5-10")
    parser.add_argument("--finalize",  action="store_true", help="Write output files from staging; no API calls")
    parser.add_argument("--status",    action="store_true", help="Show staging status and exit")
    parser.add_argument("--dry-run",      action="store_true", help="Call API but don't write output files")
    parser.add_argument("--no-rotate-cuts", action="store_true",
                        help="Don't rotate cut images 90° CCW on copy (use if scans are already upright)")
    parser.add_argument("--fix-rotation", action="store_true",
                        help="Run Haiku-based per-page rotation check after copying (fallback for non-standard scans)")
    parser.add_argument("--configure", action="store_true",
                        help="Interactively prompt for cover-page metadata (finished size, complexity, design #, "
                             "colors/pieces expected) and write to config.json. Also copies overview_000.jpg to "
                             "cover.jpg if present. Does not run any extraction.")
    parser.add_argument("--api-key",   help="Anthropic API key")
    args = parser.parse_args()

    if not args.pattern_folder and not args.quilt_id:
        sys.exit("Error: provide pattern_folder, or --quilt-id with --configure")

    pattern_folder = Path(args.pattern_folder).resolve() if args.pattern_folder else None
    if pattern_folder and not pattern_folder.is_dir():
        sys.exit(f"Error: {pattern_folder} is not a directory")

    quilt_id = args.quilt_id or (pattern_folder.name.lower() if pattern_folder else None)
    if not quilt_id:
        sys.exit("Error: could not determine quilt-id")
    out_dir  = Path(__file__).parent / "quilts" / quilt_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Configure (interactive metadata) ---
    if args.configure:
        if pattern_folder:
            _copy_cover_image(pattern_folder / "overview", out_dir)
        _configure_quilt(out_dir)
        return

    config_path = out_dir / "config.json"
    quilt_config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    grid_rows = quilt_config.get("grid_rows", "ABCDEFGH")
    grid_cols = int(quilt_config.get("grid_cols", 8))

    cut_folder      = pattern_folder / "cut"
    assy_folder     = pattern_folder / "assy"
    overview_folder = pattern_folder / "overview"

    cut_staging_path     = out_dir / "cut_raw.json"
    assy_staging_path    = out_dir / "assy_raw.json"
    assy_vis_path        = out_dir / "assy_visual_raw.json"
    overview_staging_path = out_dir / "overview_raw.json"

    # --- Status ---
    if args.status:
        for label, path, folder, prefix in [
            ("overview", overview_staging_path, overview_folder, "overview"),
            ("assy",     assy_staging_path,     assy_folder,     "assy"),
            ("cut",      cut_staging_path,      cut_folder,      "cut"),
        ]:
            if folder.is_dir():
                print_staging_status(label, load_staging(path), sorted_images(folder, prefix))
        return

    # --- Finalize ---
    if args.finalize:
        _finalize(out_dir, cut_staging_path, cut_folder, assy_staging_path, assy_vis_path, overview_staging_path,
                  grid_rows=grid_rows, grid_cols=grid_cols)
        return

    # --- Processing ---
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Error: provide --api-key or set ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    fabric_lookup: dict[str, str] = {}

    if args.stage in ("overview", "all") and overview_folder.is_dir():
        print("\n=== Overview ===")
        overview_data = run_overview(client, overview_folder, overview_staging_path,
                                     args.resume, args.page, args.pages, args.dry_run)
        if overview_data and not args.dry_run:
            (out_dir / "overview_data.json").write_text(json.dumps(overview_data, indent=2), encoding="utf-8")
            print(f"Wrote overview_data.json")
            detected = _detect_grid_from_overview(overview_data)
            if detected:
                grid_rows, grid_cols, grid_layout = detected
                _update_config_grid(out_dir, grid_rows, grid_cols, grid_layout)
            rotate_ccw = 90 if grid_layout == "col_letters" else 0
            _copy_overview_image(overview_folder, out_dir, rotate_ccw=rotate_ccw)
            _copy_cover_image(overview_folder, out_dir)
        fabric_lookup = build_fabric_lookup(overview_data)
        print(f"Fabric lookup: {len(fabric_lookup)} fabrics")

    elif (out_dir / "overview_data.json").exists():
        overview_data = json.loads((out_dir / "overview_data.json").read_text(encoding="utf-8"))
        fabric_lookup = build_fabric_lookup(overview_data)
        print(f"Loaded existing overview_data.json — {len(fabric_lookup)} fabrics")

    if args.stage in ("assy", "all") and assy_folder.is_dir():
        print("\n=== Assembly ===")
        blocks, guide = run_assy(client, assy_folder, assy_staging_path, assy_vis_path,
                                 args.resume, args.page, args.pages, args.dry_run,
                                 grid_rows=grid_rows, grid_cols=grid_cols)
        if not args.dry_run:
            write_assembly_data(blocks, out_dir / "assembly_data.py")
            (out_dir / "assembly_guide.json").write_text(json.dumps(guide, indent=2), encoding="utf-8")
            print(f"Wrote assembly_guide.json ({len(guide)} blocks)")
            _copy_assy_images(assy_folder, out_dir)

    if args.stage in ("cut", "all") and cut_folder.is_dir():
        print("\n=== Cut guide ===")
        rows = run_cut(client, cut_folder, cut_staging_path, fabric_lookup,
                       args.resume, args.page, args.pages, args.dry_run)
        if rows and not args.dry_run:
            out_path = out_dir / "cut_guide_data.py"
            if args.page or args.pages:
                target_pages = _target_page_numbers(args.page, args.pages)
                existing = _load_existing_cut_rows(out_path)
                kept = [r for r in existing if int(r.get("page") or 0) not in target_pages]
                new_rows = [r for r in rows if int(r.get("page") or 0) in target_pages]
                rows = sorted(kept + new_rows, key=lambda r: (int(r.get("page") or 0), int(r.get("piece_num") or 0)))
            blocks_for_repair = blocks if 'blocks' in dir() and blocks else _load_blocks_if_available(out_dir)
            if blocks_for_repair:
                rows, repair_log = _repair_suffix_order(rows, blocks_for_repair)
                if repair_log:
                    print(f"\n  Suffix order repairs ({sum(1 for l in repair_log if '[auto-repair]' in l)} auto, "
                          f"{sum(1 for l in repair_log if '[manual]' in l)} manual review):")
                    for line in repair_log:
                        print(line)
            write_cut_guide_data(rows, out_path)
            rotate_ccw = 0 if args.no_rotate_cuts else 90
            _copy_cut_images(cut_folder, out_dir, rotate_ccw=rotate_ccw)
            if args.fix_rotation:
                print("\n=== Rotation check ===")
                _fix_cut_rotation(client, out_dir / "cut")

    if args.stage in ("colors", "all") and overview_folder.is_dir():
        print("\n=== Colors ===")
        colors = run_colors(client, overview_folder, overview_staging_path, out_dir,
                            args.resume, args.dry_run)
        if colors and not args.dry_run:
            colors_out = out_dir / "fabric_colors.json"
            colors_out.write_text(json.dumps(colors, indent=2, sort_keys=True), encoding="utf-8")
            print(f"Wrote fabric_colors.json ({len(colors)} colors)")

    if not args.dry_run and not args.page and not args.pages and args.stage == "all":
        print("\n=== Running generate.py ===")
        subprocess.run([sys.executable, "generate.py", "--quilt-id", quilt_id], check=False)
        print("\n=== Running tracking.py ===")
        subprocess.run([sys.executable, "tracking.py", "--quilt-id", quilt_id], check=False)

    if not args.dry_run:
        _cross_check_metadata(out_dir)

    print("\nDone.")


def _detect_grid_from_overview(overview_data: list[dict]) -> tuple[str, int, str] | None:
    """Return (grid_rows_str, grid_cols_int, grid_layout) from a pattern-side grid in overview data.

    grid_layout is 'row_letters' if letters label rows (down the side),
    or 'col_letters' if letters label columns (across the top).
    """
    for page in overview_data:
        if not isinstance(page, dict):
            continue
        grid = page.get("grid")
        if not grid:
            continue
        rows = grid.get("rows", [])
        cols = grid.get("columns", [])
        if not rows or not cols:
            continue
        row_letter_str = "".join(str(r).upper() for r in rows
                                  if len(str(r)) == 1 and str(r).isalpha())
        col_letter_str = "".join(str(c).upper() for c in cols
                                  if len(str(c)) == 1 and str(c).isalpha())
        if row_letter_str:
            return row_letter_str, len(cols), "row_letters"
        if col_letter_str:
            return col_letter_str, len(rows), "col_letters"
    return None


def _update_config_grid(out_dir: Path, grid_rows: str, grid_cols: int, grid_layout: str = "row_letters") -> None:
    config_path = out_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if (config.get("grid_rows") == grid_rows and config.get("grid_cols") == grid_cols
            and config.get("grid_layout") == grid_layout):
        return
    config["grid_rows"]   = grid_rows
    config["grid_cols"]   = grid_cols
    config["grid_layout"] = grid_layout
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"  [grid] Detected {grid_layout} {len(grid_rows)}×{grid_cols} grid — saved to config.json")


def _finalize(out_dir, cut_staging_path, cut_folder, assy_staging_path, assy_vis_path, overview_staging_path,
              grid_rows: str = "ABCDEFGH", grid_cols: int = 8):  # noqa: E501
    overview_staging_path_obj = Path(overview_staging_path)
    if overview_staging_path_obj.exists():
        staging = load_staging(overview_staging_path_obj)
        data = [v["data"] for v in staging.values() if v.get("status") in ("ok", "warning") and "data" in v]
        if data:
            (out_dir / "overview_data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"Finalized overview_data.json ({len(data)} pages)")

    if Path(assy_staging_path).exists():
        blocks = _assemble_blocks(load_staging(assy_staging_path), grid_rows, grid_cols)
        guide  = _assemble_guide(load_staging(assy_vis_path))
        write_assembly_data(blocks, out_dir / "assembly_data.py")
        (out_dir / "assembly_guide.json").write_text(json.dumps(guide, indent=2), encoding="utf-8")
        print(f"Finalized assembly_guide.json ({len(guide)} blocks)")

    cut_folder_path = Path(cut_folder)
    if Path(cut_staging_path).exists() and cut_folder_path.is_dir():
        staging = load_staging(cut_staging_path)
        rows: list[dict] = []
        for img in sorted_images(cut_folder_path, "cut"):
            v = staging.get(img.name, {})
            if v.get("status") in ("ok", "warning") and "data" in v:
                rows.extend(v["data"])
        if rows:
            blocks_for_repair = blocks if "blocks" in dir() and blocks else _load_blocks_if_available(out_dir)
            if blocks_for_repair:
                rows, repair_log = _repair_suffix_order(rows, blocks_for_repair)
                if repair_log:
                    print(f"\n  Suffix order repairs ({sum(1 for l in repair_log if '[auto-repair]' in l)} auto, "
                          f"{sum(1 for l in repair_log if '[manual]' in l)} manual review):")
                    for line in repair_log:
                        print(line)
            write_cut_guide_data(rows, out_dir / "cut_guide_data.py")

    colors_staging = Path(out_dir) / "colors_raw.json"
    if colors_staging.exists():
        staging = load_staging(colors_staging)
        merged: dict[str, str] = {}
        for v in staging.values():
            if v.get("status") in ("ok", "warning") and isinstance(v.get("data"), dict):
                merged.update(v["data"])
        if merged:
            (Path(out_dir) / "fabric_colors.json").write_text(
                json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"Finalized fabric_colors.json ({len(merged)} colors)")


if __name__ == "__main__":
    main()
