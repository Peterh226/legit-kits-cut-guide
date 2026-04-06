"""
Legit Kits Cut Guide Generator
================================
Reads fabric and piece data from data/cut_guide_data.py and produces
a formatted Excel workbook: LandOfTheFree_CutGuide.xlsx

Usage:
    python generate.py
    python generate.py --output my_custom_name.xlsx

Requirements:
    pip install openpyxl
"""

import argparse
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from data.cut_guide_data import DATA


# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------
HEADER_FILL   = PatternFill("solid", start_color="2F4F4F")
ALT_FILL      = PatternFill("solid", start_color="F2F2F2")
WHITE_FILL    = PatternFill("solid", start_color="FFFFFF")
HEADER_FONT   = Font(name="Arial", bold=True, color="FFFFFF", size=11)
DATA_FONT     = Font(name="Arial", size=10)
THIN          = Side(style="thin", color="CCCCCC")
BORDER        = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _header_cell(ws, row, col, value, width):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font   = HEADER_FONT
    cell.fill   = HEADER_FILL
    cell.border = BORDER
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[get_column_letter(col)].width = width
    return cell


def _data_cell(ws, row, col, value, center=False):
    fill = ALT_FILL if row % 2 == 0 else WHITE_FILL
    cell = ws.cell(row=row, column=col, value=value)
    cell.font   = DATA_FONT
    cell.fill   = fill
    cell.border = BORDER
    cell.alignment = Alignment(
        horizontal="center" if center else "left",
        vertical="center"
    )
    return cell


def build_cut_guide_sheet(wb):
    ws = wb.active
    ws.title = "Cut Guide"

    headers = [
        ("Fabric Code",   14),
        ("Fabric Name",   16),
        ("SKU",           10),
        ("Fabric Size",   22),
        ("Piece #",       10),
        ("Template Code", 16),
        ("Quantity",      10),
    ]

    for col, (label, width) in enumerate(headers, 1):
        _header_cell(ws, 1, col, label, width)
    ws.row_dimensions[1].height = 28

    for row_idx, (code, name, sku, size, piece, template, qty) in enumerate(DATA, 2):
        _data_cell(ws, row_idx, 1, code,     center=True)
        _data_cell(ws, row_idx, 2, name)
        _data_cell(ws, row_idx, 3, sku)
        _data_cell(ws, row_idx, 4, size)
        _data_cell(ws, row_idx, 5, piece,    center=True)
        _data_cell(ws, row_idx, 6, template, center=True)
        _data_cell(ws, row_idx, 7, qty,      center=True)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:G{len(DATA) + 1}"
    return ws


def build_summary_sheet(wb):
    ws = wb.create_sheet("By Fabric Code")

    headers = [
        ("Fabric Code",  14),
        ("Fabric Name",  18),
        ("SKU",          10),
        ("Fabric Size",  22),
        ("Total Pieces", 14),
    ]
    for col, (label, width) in enumerate(headers, 1):
        _header_cell(ws, 1, col, label, width)
    ws.row_dimensions[1].height = 28

    # Aggregate piece counts per fabric code (preserving insertion order)
    seen = {}
    for code, name, sku, size, *_ in DATA:
        if code not in seen:
            seen[code] = {"name": name, "sku": sku, "size": size, "count": 0}
        seen[code]["count"] += 1

    for row_idx, (code, info) in enumerate(sorted(seen.items()), 2):
        _data_cell(ws, row_idx, 1, code,             center=True)
        _data_cell(ws, row_idx, 2, info["name"])
        _data_cell(ws, row_idx, 3, info["sku"],      center=True)
        _data_cell(ws, row_idx, 4, info["size"])
        _data_cell(ws, row_idx, 5, info["count"],    center=True)

    return ws


def generate(output_path: str = "LandOfTheFree_CutGuide.xlsx"):
    wb = Workbook()
    build_cut_guide_sheet(wb)
    build_summary_sheet(wb)
    wb.save(output_path)

    fabric_count = len({row[0] for row in DATA})
    print(f"Generated: {output_path}")
    print(f"  Fabrics : {fabric_count}")
    print(f"  Pieces  : {len(DATA)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Legit Kits Cut Guide spreadsheet")
    parser.add_argument(
        "--output", "-o",
        default="LandOfTheFree_CutGuide.xlsx",
        help="Output filename (default: LandOfTheFree_CutGuide.xlsx)"
    )
    args = parser.parse_args()
    generate(args.output)
