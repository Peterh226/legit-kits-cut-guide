#!/usr/bin/env python3
"""
Detect and fix rotation of cut guide images in quilts/*/cut/ folders.

Uses Claude Haiku to check whether each image has its footer ("Page X of Y")
in the lower-right corner. Rotates in-place with Pillow if not.

Usage:
    python fix_cut_rotation.py                          # all quilts
    python fix_cut_rotation.py --quilt land-of-the-free # one quilt
    python fix_cut_rotation.py --dry-run                # report only, no changes
    python fix_cut_rotation.py --api-key <key>          # bypass env var
"""

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image

QUILTS_DIR = Path(__file__).parent / "quilts"
THUMB_SIZE  = (600, 800)   # downsample before sending to Claude
MODEL       = "claude-haiku-4-5-20251001"

PROMPT = (
    "This is a scanned cut guide page from a quilt kit. "
    "When correctly oriented: the page title appears at the TOP and "
    "a footer reading 'Page X of Y' appears in the LOWER-RIGHT corner. "
    "How many degrees CLOCKWISE must this image be rotated to reach correct orientation? "
    "Reply with exactly one of: 0, 90, 180, 270"
)


def _thumb_b64(img_path: Path) -> str:
    img = Image.open(img_path).convert("RGB")
    img.thumbnail(THUMB_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return base64.standard_b64encode(buf.getvalue()).decode()


def detect_rotation(client, img_path: Path) -> int:
    data = _thumb_b64(img_path)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    text = resp.content[0].text.strip().split()[0]
    try:
        deg = int(text)
        return deg if deg in (0, 90, 180, 270) else 0
    except ValueError:
        return 0


def rotate_image(img_path: Path, degrees_cw: int) -> None:
    """Rotate image clockwise by degrees_cw and save in-place."""
    img = Image.open(img_path)
    # Pillow rotate() is CCW, so negate for CW
    rotated = img.rotate(-degrees_cw, expand=True)
    rotated.save(img_path, quality=92)


def process_quilt(client, quilt_id: str, dry_run: bool) -> dict:
    cut_dir = QUILTS_DIR / quilt_id / "cut"
    if not cut_dir.exists():
        print(f"  [{quilt_id}] no cut/ folder — skipping")
        return {}

    images = sorted(cut_dir.glob("*.jpg"))
    results = {}
    rotated_count = 0

    for img_path in images:
        deg = detect_rotation(client, img_path)
        results[img_path.name] = deg
        if deg != 0:
            rotated_count += 1
            action = "would rotate" if dry_run else "rotating"
            print(f"  {img_path.name}: {action} {deg}° CW")
            if not dry_run:
                rotate_image(img_path, deg)
        else:
            print(f"  {img_path.name}: ok")

    print(f"  [{quilt_id}] {rotated_count} of {len(images)} needed rotation")
    return results


def main():
    parser = argparse.ArgumentParser(description="Detect and fix cut image rotation")
    parser.add_argument("--quilt",   help="Quilt ID to process (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Detect only, no changes")
    parser.add_argument("--api-key", help="Anthropic API key (overrides env)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY not set. Use --api-key or set the env var.")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    quilts = [args.quilt] if args.quilt else [d.name for d in sorted(QUILTS_DIR.iterdir()) if d.is_dir()]

    all_results = {}
    for quilt_id in quilts:
        print(f"\n=== {quilt_id} ===")
        all_results[quilt_id] = process_quilt(client, quilt_id, args.dry_run)

    # Save a report so results can be reviewed
    report_path = Path("cut_rotation_report.json")
    report_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
