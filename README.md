# Legit Kits Cut Guide Generator

A Python tool that converts Legit Kits quilt pattern cut guides from PDF into a formatted Excel spreadsheet — one row per piece, with auto-filter, alternating row colors, and a fabric summary sheet.

Currently includes the complete **Land of the Free** pattern (86 fabrics, 1,538 piece rows).

---

## Output

Running the script produces `LandOfTheFree_CutGuide.xlsx` with two sheets:

| Sheet | Contents |
|---|---|
| **Cut Guide** | One row per piece: Fabric Code, Fabric Name, SKU, Fabric Size, Piece #, Template Code, Quantity |
| **By Fabric Code** | One row per fabric with total piece count |

Both sheets have auto-filter enabled. The Cut Guide sheet is frozen at row 1.

---

## Requirements

Python 3.8+ and one dependency:

```bash
pip install openpyxl
```

---

## Usage

```bash
# Generate with default filename
python generate.py

# Specify a custom output name
python generate.py --output my_pattern.xlsx
```

---

## Project Structure

```
legit-kits-cut-guide/
├── generate.py              # Main script — run this
├── data/
│   ├── __init__.py
│   └── cut_guide_data.py    # All fabric and piece data
├── README.md
├── CONTRIBUTING.md
└── .gitignore
```

---

## Adding a New Pattern

All data lives in `data/cut_guide_data.py` as a Python list of tuples. Each tuple is:

```python
(fabric_code, fabric_name, sku, fabric_size, piece_num, template_code, quantity)
```

**Example:**
```python
("AF", "Saffron", "1320", "Fat 1/8YD", 1, "F3m", 3),
```

To add a new pattern, append its entries to the `DATA` list and re-run `generate.py`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

---

## Data Source

Cut guide PDFs are produced by [Legit Kits](https://legitkits.com/) and are included with their quilt kits. Pattern data is transcribed for personal organizational use.
