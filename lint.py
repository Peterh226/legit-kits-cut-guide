"""
Legit Kits Cut Guide — Data Linter
====================================
Checks cut_guide_data.py for common errors before generating the spreadsheet.

Usage:
    python lint.py                        # first quilt in quilts/
    python lint.py --quilt-id sewphia
    python lint.py --quilt-id skulliver

Exit codes:
    0  no issues found
    1  warnings found (review recommended)
    2  errors found (fix before generating)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Quilt loading (same pattern as generate.py)
# ---------------------------------------------------------------------------

def _load_quilt(quilt_id):
    root = Path(__file__).parent
    quilt_dir = root / "quilts" / quilt_id
    if not quilt_dir.exists():
        raise SystemExit(f"Error: quilts/{quilt_id}/ not found")
    cut_ns = {}
    exec((quilt_dir / "cut_guide_data.py").read_text(encoding="utf-8"), cut_ns)
    asm_ns = {}
    asm_path = quilt_dir / "assembly_data.py"
    if asm_path.exists():
        exec(asm_path.read_text(encoding="utf-8"), asm_ns)
    blocks = asm_ns.get("BLOCKS", {})
    config_path = quilt_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    return cut_ns["DATA"], blocks, config


def _default_quilt_id():
    quilts_dir = Path(__file__).parent / "quilts"
    ids = sorted(p.name for p in quilts_dir.iterdir() if p.is_dir()) if quilts_dir.exists() else []
    if not ids:
        raise SystemExit("Error: no quilts found in quilts/")
    return ids[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_FIELDS = 8

def _row_label(row):
    return f"({row[0]} / {row[1]} / piece {row[4]})"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_field_count(data):
    """Every tuple must have exactly 8 fields."""
    errors = []
    for i, row in enumerate(data):
        if len(row) != EXPECTED_FIELDS:
            errors.append(f"  Row {i+1}: expected {EXPECTED_FIELDS} fields, got {len(row)}: {row}")
    return errors


def check_ambiguous_codes(data):
    """Warn on fabric codes likely to be misread."""
    warnings = []
    codes = sorted({row[0] for row in data})

    single_letter = [c for c in codes if len(c) == 1]
    if single_letter:
        warnings.append(
            f"  Single-letter codes (easy to misread — verify against scan): {', '.join(single_letter)}"
        )

    for c in codes:
        longer = [other for other in codes if other != c and other.startswith(c)]
        if longer:
            warnings.append(
                f"  Code '{c}' is a prefix of {longer} — double-check both against scan"
            )

    return warnings


def check_duplicate_fabric_codes(data):
    """Each fabric code should map to exactly one fabric name."""
    code_pages = defaultdict(set)
    code_names = defaultdict(set)
    for row in data:
        code, name, sku, size, piece, template, qty, page = row
        code_pages[code].add(page)
        code_names[code].add(name)

    warnings = []
    for code, pages in code_pages.items():
        if len(pages) > 2:
            warnings.append(
                f"  Code '{code}' appears on {len(pages)} pages: {sorted(pages)} — verify it's not a duplicate"
            )

    errors = []
    for code, names in code_names.items():
        if len(names) > 1:
            errors.append(
                f"  Code '{code}' has multiple fabric names: {names} — likely a data entry error"
            )

    return warnings, errors


def check_piece_numbering(data):
    """Within each fabric code, piece numbers should start at 1 and be sequential."""
    by_code = defaultdict(list)
    for row in data:
        by_code[row[0]].append(row[4])

    warnings = []
    for code, pieces in sorted(by_code.items()):
        pieces_sorted = sorted(pieces)
        if pieces_sorted[0] != 1:
            warnings.append(f"  {code}: piece numbers start at {pieces_sorted[0]}, expected 1")
        expected = list(range(1, len(pieces_sorted) + 1))
        if pieces_sorted != expected:
            missing = sorted(set(expected) - set(pieces_sorted))
            if missing:
                warnings.append(f"  {code}: missing piece numbers {missing}")

    return warnings


def check_quantities(data):
    """Quantities should be positive integers. Flag zeros or suspiciously large values."""
    errors   = []
    warnings = []
    for row in data:
        qty = row[6]
        if not isinstance(qty, int) or qty <= 0:
            errors.append(f"  {_row_label(row)}: invalid quantity '{qty}' (must be a positive integer)")
        elif qty > 15:
            warnings.append(f"  {_row_label(row)}: quantity {qty} is unusually large — verify against scan")
    return warnings, errors


def check_page_numbers(data):
    """Page numbers should be positive integers; flag gaps in coverage."""
    errors   = []
    warnings = []
    for i, row in enumerate(data):
        page = row[7]
        if not isinstance(page, int) or page <= 0:
            errors.append(f"  Row {i+1} {_row_label(row)}: invalid page '{page}'")

    all_pages = sorted({row[7] for row in data})
    if all_pages:
        full_range = list(range(all_pages[0], all_pages[-1] + 1))
        missing    = sorted(set(full_range) - set(all_pages))
        if missing:
            warnings.append(
                f"  No data for pages: {missing} — may be continuation pages or graphics-only; verify against scan"
            )

    return warnings, errors


def check_template_codes(data):
    """Template codes should match the expected pattern (e.g. F3m, B12, C210, A1L)."""
    pattern = re.compile(r'^[A-Z]{1,2}\d+[a-zA-Z]?$')
    warnings = []
    for row in data:
        tmpl = row[5]
        if not pattern.match(str(tmpl)):
            warnings.append(f"  {_row_label(row)}: unusual template code '{tmpl}' — verify against scan")
    return warnings


# ---------------------------------------------------------------------------
# Cross-check: cut data vs assembly data and config metadata
# ---------------------------------------------------------------------------

def _block_of(template):
    """Return the block id portion of a template code (e.g. 'B1' from 'B1a')."""
    m = re.match(r'^([A-Z]\d+|\d+[A-Z])', str(template))
    return m.group(1) if m else None


def check_unknown_templates(data, blocks):
    """Cut templates that do not appear in assembly_data BLOCKS.

    Likely vision misreads — flagged for manual review. Case-insensitive match
    is used too so L/l case-only differences are reported separately.
    """
    all_segs = {seg for frags in blocks.values() for seg in frags}
    all_segs_ci = {s.lower(): s for s in all_segs}
    warnings = []
    unknown = sorted({r[5] for r in data} - all_segs)
    for t in unknown:
        ci = all_segs_ci.get(str(t).lower())
        if ci and ci != t:
            warnings.append(f"  cut template '{t}' differs from assembly '{ci}' only in letter case")
        else:
            count = sum(1 for r in data if r[5] == t)
            block = _block_of(t)
            block_segs = sorted(blocks.get(block, [])) if block else []
            warnings.append(
                f"  cut template '{t}' has no matching assembly segment "
                f"({count} cut row(s); assembly segments for block {block}: {block_segs})"
            )
    return warnings


def check_empty_assembly_segments(data, blocks):
    """Assembly segments with zero cut rows — possible missing pieces."""
    cut_templates = {r[5] for r in data}
    warnings = []
    for block, frags in sorted(blocks.items()):
        for seg in frags:
            if seg not in cut_templates:
                # Tolerate case-only mismatch (separately flagged by check_unknown_templates)
                if any(t.lower() == seg.lower() for t in cut_templates):
                    continue
                warnings.append(f"  assembly segment '{seg}' has no cut rows")
    return warnings


def check_sew_sequence_gaps(data):
    """For each segment, the sew sequence values across all fabrics should be 1..N
    with no gaps. A gap means a piece is missing from cut extraction."""
    by_seg = defaultdict(list)
    for r in data:
        # r = (fabric, name, sku, size, piece_num, template, sew_seq, page)
        by_seg[r[5]].append((r[6], r[0], r[7]))  # (seq, fabric, page)

    warnings = []
    for seg, entries in sorted(by_seg.items()):
        seqs = sorted({e[0] for e in entries if isinstance(e[0], int)})
        if not seqs:
            continue
        max_seq = seqs[-1]
        missing = sorted(set(range(1, max_seq + 1)) - set(seqs))
        if missing:
            fabrics = sorted({e[1] for e in entries})
            pages = sorted({e[2] for e in entries})
            warnings.append(
                f"  {seg}: missing sew sequence {missing} (max seen={max_seq}; "
                f"fabrics={fabrics}; pages seen={pages})"
            )
    return warnings


def check_metadata_counts(data, config):
    """Cross-check against config.json metadata.colors_expected / pieces_expected."""
    meta = config.get("metadata", {})
    if not meta.get("colors_expected") and not meta.get("pieces_expected"):
        return []
    actual_colors = len({r[0] for r in data})
    actual_pieces = len(data)
    warnings = []
    if meta.get("colors_expected") is not None and meta["colors_expected"] != actual_colors:
        warnings.append(
            f"  Colors: cover says {meta['colors_expected']}, extracted {actual_colors} "
            f"(delta {actual_colors - meta['colors_expected']:+d})"
        )
    if meta.get("pieces_expected") is not None and meta["pieces_expected"] != actual_pieces:
        warnings.append(
            f"  Pieces: cover says {meta['pieces_expected']}, extracted {actual_pieces} "
            f"(delta {actual_pieces - meta['pieces_expected']:+d})"
        )
    return warnings


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_lint(data, quilt_name, blocks=None, config=None):
    blocks = blocks or {}
    config = config or {}
    all_errors   = []
    all_warnings = []

    errs = check_field_count(data)
    if errs:
        all_errors.append(("Field count", errs))

    warns = check_ambiguous_codes(data)
    if warns:
        all_warnings.append(("Ambiguous fabric codes", warns))

    warns, errs = check_duplicate_fabric_codes(data)
    if warns:
        all_warnings.append(("Fabric code page spread", warns))
    if errs:
        all_errors.append(("Fabric code name mismatch", errs))

    warns = check_piece_numbering(data)
    if warns:
        all_warnings.append(("Piece numbering", warns))

    warns, errs = check_quantities(data)
    if warns:
        all_warnings.append(("Unusual quantities", warns))
    if errs:
        all_errors.append(("Invalid quantities", errs))

    warns, errs = check_page_numbers(data)
    if warns:
        all_warnings.append(("Page coverage", warns))
    if errs:
        all_errors.append(("Invalid page numbers", errs))

    warns = check_template_codes(data)
    if warns:
        all_warnings.append(("Unusual template codes", warns))

    warns = check_metadata_counts(data, config)
    if warns:
        all_warnings.append(("Cover-page cross-check (config metadata)", warns))

    if blocks:
        warns = check_unknown_templates(data, blocks)
        if warns:
            all_warnings.append(("Cut templates not in assembly", warns))

        warns = check_empty_assembly_segments(data, blocks)
        if warns:
            all_warnings.append(("Assembly segments with no cut rows", warns))

        warns = check_sew_sequence_gaps(data)
        if warns:
            all_warnings.append(("Sew-sequence gaps", warns))

    print(f"Linting {quilt_name}: {len(data)} rows, "
          f"{len({r[0] for r in data})} fabrics...\n")

    if all_warnings:
        print("WARNINGS (review recommended):")
        for section, items in all_warnings:
            print(f"\n  [{section}]")
            for item in items:
                print(item)

    if all_errors:
        print("\nERRORS (fix before generating):")
        for section, items in all_errors:
            print(f"\n  [{section}]")
            for item in items:
                print(item)

    print()
    if not all_errors and not all_warnings:
        print("No issues found.")
        return 0
    elif not all_errors:
        print(f"No errors. {sum(len(i) for _, i in all_warnings)} warning(s) to review.")
        return 1
    else:
        print(f"{sum(len(i) for _, i in all_errors)} error(s) found. Fix before running generate.py.")
        return 2


if __name__ == "__main__":
    import contextlib, io
    parser = argparse.ArgumentParser(description="Lint Legit Kits cut guide data")
    parser.add_argument("--quilt-id", "-q", help="Quilt ID (default: first in quilts/)")
    args = parser.parse_args()
    quilt_id = args.quilt_id or _default_quilt_id()
    data, blocks, config = _load_quilt(quilt_id)
    quilt_name = config.get("quilt_name", quilt_id)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = run_lint(data, quilt_name, blocks=blocks, config=config)
    output = buf.getvalue()
    print(output, end="")

    report_path = Path(__file__).parent / "quilts" / quilt_id / "lint_report.txt"
    report_path.write_text(output, encoding="utf-8")
    print(f"Report written to quilts/{quilt_id}/lint_report.txt")

    sys.exit(exit_code)
