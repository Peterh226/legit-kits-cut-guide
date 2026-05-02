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
    --stage STAGE       Which stage to run: cut, assy, overview, all (default: all)
    --resume            Skip already-processed pages in the staging file
    --page N            Process only page N (1-based)
    --pages START-END   Process a range of pages, e.g. --pages 5-10
    --finalize          Write final output files from staging without API calls
    --status            Print per-stage staging status and exit
    --dry-run           Call API and validate, but don't write output files
    --api-key KEY       Anthropic API key (overrides ANTHROPIC_API_KEY env var)

Output (written to quilts/<quilt-id>/):
    overview_data.json      Master fabric list and metadata
    assembly_data.py        Block -> fragment mapping
    assembly_guide.json     Visual assembly data
    cut_guide_data.py       All piece rows

Staging files (quilts/<quilt-id>/, one per stage):
    overview_raw.json / assy_raw.json / assy_visual_raw.json / cut_raw.json
    Each is a dict keyed by image filename; each value has status, data, warnings.
    Re-running with --resume skips entries already marked ok or warning.
    Re-running with --page N or --pages START-END overwrites just those entries.
"""

import argparse
import base64
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

def validate_cut_rows(rows: list, img_name: str) -> list[str]:
    if not isinstance(rows, list):
        return ["Response is not a list"]
    warnings = []
    if len(rows) == 0:
        warnings.append("No rows extracted")
        return warnings
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
    return warnings


def validate_assy_entries(entries: list, img_name: str) -> list[str]:
    if not isinstance(entries, list):
        return ["Response is not a list"]
    warnings = []
    if len(entries) == 0:
        warnings.append("No blocks extracted")
        return warnings
    for e in entries:
        if not isinstance(e, dict):
            warnings.append(f"Entry is not a dict: {e!r}")
            continue
        bid = e.get("block_id", "")
        if not re.match(r"^[A-H][1-8]$", bid):
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
    images = sorted_images(folder, "overview")
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
                    warnings = validate_assy_entries(entries, img.name)
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

    return _assemble_blocks(staging), _assemble_guide(visual_staging)


def _assemble_blocks(staging: dict) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    for v in staging.values():
        if v.get("status") in ("ok", "warning") and "data" in v:
            for entry in v["data"]:
                blocks[entry["block_id"]] = entry["fragments"]
    all_block_ids = [f"{r}{c}" for r in "ABCDEFGH" for c in "12345678"]
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
            raw = call_claude(client, img, prompt)
            rows = _parse_json(raw, img.name)
            if rows is None:
                staging[img.name] = {"status": "error", "error": "JSON parse failed", "raw": raw[:1000], "ts": _ts()}
                print("ERROR: JSON parse failed")
            else:
                rows, fixed = resolve_fabric_codes(rows, fabric_lookup)
                warnings = validate_cut_rows(rows, img.name)
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


def _copy_overview_image(overview_folder: Path, out_dir: Path) -> None:
    images = sorted_images(overview_folder, "overview")
    if not images:
        return
    dest = out_dir / "quilt_overview.jpg"
    if dest.exists():
        print(f"  quilt_overview.jpg already exists — skipping (edit manually if needed)")
        return
    dest.write_bytes(images[0].read_bytes())
    print(f"  Copied {images[0].name} -> quilt_overview.jpg (replace manually if needed)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Extract Legit Kits pattern data via Claude vision API")
    parser.add_argument("pattern_folder", help="Folder with cut/, assy/, overview/ subfolders")
    parser.add_argument("--quilt-id",  help="Quilt identifier (default: lowercased folder name)")
    parser.add_argument("--stage",     choices=["cut", "assy", "overview", "all"], default="all")
    parser.add_argument("--resume",    action="store_true", help="Skip already-processed pages")
    parser.add_argument("--page",      type=int,  help="Process only this page (1-based)")
    parser.add_argument("--pages",     help="Process page range e.g. 5-10")
    parser.add_argument("--finalize",  action="store_true", help="Write output files from staging; no API calls")
    parser.add_argument("--status",    action="store_true", help="Show staging status and exit")
    parser.add_argument("--dry-run",   action="store_true", help="Call API but don't write output files")
    parser.add_argument("--api-key",   help="Anthropic API key")
    args = parser.parse_args()

    pattern_folder = Path(args.pattern_folder).resolve()
    if not pattern_folder.is_dir():
        sys.exit(f"Error: {pattern_folder} is not a directory")

    quilt_id = args.quilt_id or pattern_folder.name.lower()
    out_dir  = Path(__file__).parent / "quilts" / quilt_id
    out_dir.mkdir(parents=True, exist_ok=True)

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
        _finalize(out_dir, cut_staging_path, cut_folder, assy_staging_path, assy_vis_path, overview_staging_path)
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
            _copy_overview_image(overview_folder, out_dir)
        fabric_lookup = build_fabric_lookup(overview_data)
        print(f"Fabric lookup: {len(fabric_lookup)} fabrics")

    elif (out_dir / "overview_data.json").exists():
        overview_data = json.loads((out_dir / "overview_data.json").read_text(encoding="utf-8"))
        fabric_lookup = build_fabric_lookup(overview_data)
        print(f"Loaded existing overview_data.json — {len(fabric_lookup)} fabrics")

    if args.stage in ("assy", "all") and assy_folder.is_dir():
        print("\n=== Assembly ===")
        blocks, guide = run_assy(client, assy_folder, assy_staging_path, assy_vis_path,
                                 args.resume, args.page, args.pages, args.dry_run)
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
                rows = sorted(kept + rows, key=lambda r: (int(r.get("page") or 0), int(r.get("piece_num") or 0)))
            write_cut_guide_data(rows, out_path)

    if not args.dry_run and not args.page and not args.pages and args.stage == "all":
        print("\n=== Running generate.py ===")
        subprocess.run([sys.executable, "generate.py", "--quilt-id", quilt_id], check=False)
        print("\n=== Running tracking.py ===")
        subprocess.run([sys.executable, "tracking.py", "--quilt-id", quilt_id], check=False)

    print("\nDone.")


def _finalize(out_dir, cut_staging_path, cut_folder, assy_staging_path, assy_vis_path, overview_staging_path):
    overview_staging_path_obj = Path(overview_staging_path)
    if overview_staging_path_obj.exists():
        staging = load_staging(overview_staging_path_obj)
        data = [v["data"] for v in staging.values() if v.get("status") in ("ok", "warning") and "data" in v]
        if data:
            (out_dir / "overview_data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"Finalized overview_data.json ({len(data)} pages)")

    if Path(assy_staging_path).exists():
        blocks = _assemble_blocks(load_staging(assy_staging_path))
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
            write_cut_guide_data(rows, out_dir / "cut_guide_data.py")


if __name__ == "__main__":
    main()
