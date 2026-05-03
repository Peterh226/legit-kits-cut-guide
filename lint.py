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
    g = {}
    exec((quilt_dir / "cut_guide_data.py").read_text(encoding="utf-8"), g)
    config_path = quilt_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    return g["DATA"], config.get("quilt_name", quilt_id)


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
# Runner
# ---------------------------------------------------------------------------

def run_lint(data, quilt_name):
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
    data, quilt_name = _load_quilt(quilt_id)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = run_lint(data, quilt_name)
    output = buf.getvalue()
    print(output, end="")

    report_path = Path(__file__).parent / "quilts" / quilt_id / "lint_report.txt"
    report_path.write_text(output, encoding="utf-8")
    print(f"Report written to quilts/{quilt_id}/lint_report.txt")

    sys.exit(exit_code)
