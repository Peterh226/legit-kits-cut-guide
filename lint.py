"""
Legit Kits Cut Guide — Data Linter
====================================
Checks data/cut_guide_data.py for common errors before generating the spreadsheet.

Usage:
    python lint.py
    python lint.py --fix      # auto-fix what it can (currently: none — all fixes are manual)

Exit codes:
    0  no issues found
    1  warnings found (review recommended)
    2  errors found (fix before generating)
"""

import argparse
import sys
from collections import defaultdict
from data.cut_guide_data import DATA


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
    """
    Warn on fabric codes that are likely to be misread:
    - Single letters (easy to miss that it's just one letter)
    - Codes where a letter looks like two (e.g. 'SI' vs 'S', 'IL' vs 'I')
    - Codes that differ from others by only one character
    """
    warnings = []
    codes = sorted({row[0] for row in data})

    single_letter = [c for c in codes if len(c) == 1]
    if single_letter:
        warnings.append(
            f"  Single-letter codes (easy to misread — verify against PDF): {', '.join(single_letter)}"
        )

    # Flag codes that are a prefix of another code
    for c in codes:
        longer = [other for other in codes if other != c and other.startswith(c)]
        if longer:
            warnings.append(
                f"  Code '{c}' is a prefix of {longer} — double-check both against PDF"
            )

    return warnings


def check_duplicate_fabric_codes(data):
    """Each fabric code should appear on only one page."""
    code_pages = defaultdict(set)
    code_names = defaultdict(set)
    for row in data:
        code, name, sku, size, piece, template, qty, page = row
        code_pages[code].add(page)
        code_names[code].add(name)

    warnings = []
    for code, pages in code_pages.items():
        if len(pages) > 2:
            # More than 2 pages is suspicious (UP Cappuccino spans 2 legitimately)
            warnings.append(
                f"  Code '{code}' appears on {len(pages)} pages: {sorted(pages)} — verify it's not a duplicate code"
            )

    errors = []
    for code, names in code_names.items():
        if len(names) > 1:
            errors.append(
                f"  Code '{code}' has multiple fabric names: {names} — likely a data entry error"
            )

    return warnings, errors


def check_piece_numbering(data):
    """
    Within each fabric code, piece numbers should:
    - Start at 1
    - Be sequential integers (gaps are suspicious)
    """
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
            dupes   = [p for p in pieces_sorted if pieces_sorted.count(p) > 1]
            if missing:
                warnings.append(f"  {code}: missing piece numbers {missing}")
            if dupes:
                # Duplicates are actually valid — same template used multiple times
                # so just note them at low severity
                pass

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
            warnings.append(f"  {_row_label(row)}: quantity {qty} is unusually large — verify against PDF")
    return warnings, errors


def check_page_numbers(data):
    """Page numbers should be positive integers within the expected range."""

    # Pages that are intentional continuations of a multi-page fabric —
    # data is stored under the first page, so these will always appear "missing".
    # Add to this list whenever a fabric spans more than one PDF page.
    KNOWN_CONTINUATION_PAGES = {
        19,  # Chocolate (HO) continues from p.19 — data stored under p.19 first half
        51,  # Sable (SL) continues from p.51 — data stored under p.51 first half
        59,  # Cappuccino (UP) continues from p.58 — data stored under p.58
    }

    errors   = []
    warnings = []
    for i, row in enumerate(data):
        page = row[7]
        if not isinstance(page, int) or page <= 0:
            errors.append(f"  Row {i+1} {_row_label(row)}: invalid page '{page}'")

    # Check for page gaps (might indicate a missing fabric)
    all_pages  = sorted({row[7] for row in data})
    if all_pages:
        full_range = list(range(all_pages[0], all_pages[-1] + 1))
        missing    = sorted(set(full_range) - set(all_pages) - KNOWN_CONTINUATION_PAGES)
        if missing:
            warnings.append(f"  No data found for pages: {missing} — were these pages skipped?")

    return warnings, errors


def check_template_codes(data):
    """
    Template codes follow a pattern: one or two uppercase letters followed by
    digits and optional lowercase suffix (e.g. F3m, G7L, A1, D2a).
    Flag anything that doesn't match.
    """
    import re
    pattern = re.compile(r'^[A-Z]{1,2}\d+[a-zA-Z]?$')
    warnings = []
    for row in data:
        tmpl = row[5]
        if not pattern.match(tmpl):
            warnings.append(f"  {_row_label(row)}: unusual template code '{tmpl}' — verify against PDF")
    return warnings


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_lint(data):
    all_errors   = []
    all_warnings = []

    # Field count
    errs = check_field_count(data)
    if errs:
        all_errors.append(("Field count", errs))

    # Ambiguous codes
    warns = check_ambiguous_codes(data)
    if warns:
        all_warnings.append(("Ambiguous fabric codes", warns))

    # Duplicate codes
    warns, errs = check_duplicate_fabric_codes(data)
    if warns:
        all_warnings.append(("Fabric code page spread", warns))
    if errs:
        all_errors.append(("Fabric code name mismatch", errs))

    # Piece numbering
    warns = check_piece_numbering(data)
    if warns:
        all_warnings.append(("Piece numbering", warns))

    # Quantities
    warns, errs = check_quantities(data)
    if warns:
        all_warnings.append(("Unusual quantities", warns))
    if errs:
        all_errors.append(("Invalid quantities", errs))

    # Page numbers
    warns, errs = check_page_numbers(data)
    if warns:
        all_warnings.append(("Page coverage", warns))
    if errs:
        all_errors.append(("Invalid page numbers", errs))

    # Template codes
    warns = check_template_codes(data)
    if warns:
        all_warnings.append(("Unusual template codes", warns))

    # ── Report ──────────────────────────────────────────────────────────────
    print(f"Linting {len(data)} rows across "
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
        print("✓ No issues found.")
        return 0
    elif not all_errors:
        print(f"✓ No errors. {sum(len(i) for _, i in all_warnings)} warning(s) to review.")
        return 1
    else:
        print(f"✗ {sum(len(i) for _, i in all_errors)} error(s) found. "
              f"Fix before running generate.py.")
        return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lint the cut guide data file")
    parser.parse_args()
    sys.exit(run_lint(DATA))
