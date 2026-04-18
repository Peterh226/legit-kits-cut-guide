# Legit Kits Cut Guide Generator

A Python tool that converts Legit Kits quilt pattern cut guides from scanned PDFs into formatted Excel spreadsheets — one row per piece, with auto-filter, alternating row colors, and summary/statistics sheets.

Currently includes the complete **Land of the Free** pattern (86 fabrics, 1,614 piece rows).

---

## Output

### `generate.py` → `LandOfTheFree_CutGuide.xlsx`

| Sheet | Contents |
|---|---|
| **Cut Guide** | One row per piece: Fabric Code, Fabric Name, SKU, Fabric Size, Piece #, Template Code, Quantity, Page |
| **By Fabric Code** | One row per fabric with total piece count |
| **Statistics** | Summary counts and per-fabric breakdown |

Both data sheets have auto-filter enabled. The Cut Guide sheet is frozen at row 1.

### `tracking.py` → `LandOfTheFree_Tracker.xlsx`

Progress tracker workbook with 6 sheets including piece counts by fabric and a block completion checklist.

---

## Requirements

```bash
pip install openpyxl pillow anthropic
```

---

## Usage

### Generate cut guide Excel from existing data

```bash
python generate.py
python generate.py --output my_pattern.xlsx
```

### Validate data before generating

```bash
python lint.py
```

### Extract data from scanned images (Claude API)

```bash
python extract.py <pattern_folder>
```

The pattern folder must contain three subfolders of scanned images:

```
<pattern_folder>/
├── cut/       cut_01.png, cut_02.png, ...   (cut guide pages)
├── assy/      assy_01.png, assy_02.png, ...  (assembly guide pages)
└── overview/  overview_01.png, ...           (overview/kit contents pages)
```

`extract.py` calls the Claude vision API to extract structured data from each image, writes `data/cut_guide_data.py` and `data/assembly_data.py`, then runs `generate.py` and `tracking.py` automatically.

Requires `ANTHROPIC_API_KEY` to be set in the environment.

---

## Project Structure

```
legit-kits-cut-guide/
├── generate.py              # Produces cut guide Excel workbook
├── tracking.py              # Produces progress tracker Excel workbook
├── lint.py                  # Validates data/cut_guide_data.py
├── extract.py               # Extracts data from scans via Claude API
├── data/
│   ├── __init__.py
│   ├── cut_guide_data.py    # Fabric and piece data (86 fabrics, 1,614 rows)
│   └── assembly_data.py     # Block assembly data (64 blocks)
└── README.md
```

---

## Data Format

### cut_guide_data.py

Each row in `DATA` is an 8-tuple:

```python
(fabric_code, fabric_name, sku, fabric_size, piece_num, template_code, quantity, page)
```

**Example:**
```python
("AF", "Saffron", "1320", "Fat 1/8YD", 1, "F3m", 3, 1),
```

The `page` field is the cut guide page number (not the PDF page number).

### assembly_data.py

`BLOCKS` maps each block ID to its ordered list of fragment IDs:

```python
BLOCKS = {
    "A1": ["A1"],                              # single-fragment block
    "B7": ["B7a","B7b","B7c","B7d",            # multi-fragment block
            "B7e","B7f","B7g","B7h"],
    ...
}
```

---

## Data Source

Cut guides are printed booklets included with [Legit Kits](https://legitkits.com/) quilt kits. The PDF is created by scanning each page of the booklet. Pattern data is transcribed/extracted for personal organizational use.
