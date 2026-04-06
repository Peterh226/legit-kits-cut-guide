# Adding a New Pattern

This guide explains how to add cut guide data for a new Legit Kits pattern.

---

## Workflow

1. **Get the cut guide PDF** from your Legit Kits kit
2. **Upload the PDF to Claude** (claude.ai) with a prompt like:
   > "Please parse this Legit Kits cut guide PDF and give me the Python tuples to add to cut_guide_data.py"
3. **Paste the returned tuples** into `data/cut_guide_data.py`
4. **Run the generator** and verify the output

---

## Data Format

Each row in `DATA` is a 7-element tuple:

```python
(fabric_code, fabric_name, sku, fabric_size, piece_num, template_code, quantity)
```

| Field | Type | Example | Description |
|---|---|---|---|
| `fabric_code` | str | `"AF"` | Two-letter code from the cut guide (top-right of page) |
| `fabric_name` | str | `"Saffron"` | Color name |
| `sku` | str | `"1320"` | Legit Kits color number |
| `fabric_size` | str | `"Fat 1/8YD"` | Yardage/size included in kit |
| `piece_num` | int | `1` | Circled number on the layout diagram |
| `template_code` | str | `"F3m"` | Template identifier (letter + number + optional suffix) |
| `quantity` | int | `3` | Number of cuts — the value in parentheses on the guide |

---

## Style Conventions

Group entries by fabric code and add a comment header:

```python
# ------------------------------------------------------------------
# AF  Saffron  1320  Fat 1/8YD  (p.1)
# ------------------------------------------------------------------
("AF", "Saffron", "1320", "Fat 1/8YD", 1, "F3m", 3),
("AF", "Saffron", "1320", "Fat 1/8YD", 2, "F3s", 1),
...
```

The comment format is: `fabric_code  fabric_name  sku  fabric_size  (page reference)`

---

## Reading the Cut Guide PDF

Each page of the cut guide shows:
- **Top right**: fabric swatch color, fabric name, SKU number, and yardage — this is the fabric header
- **Two large letters** (e.g. `AF`) — the fabric code
- **A layout diagram** with circled numbers — the piece numbers
- **A legend** below or beside the diagram listing each piece:
  `① F3m(3)` means piece 1 uses template F3m and requires 3 cuts

Some pages show **two fabrics** (one in each half of the page). Parse each separately.

Some fabrics span **two pages** (e.g. Cappuccino UP, pages 58–59). Continue numbering sequentially.

---

## After Adding Data

```bash
python generate.py
```

Open the resulting `.xlsx` and spot-check a few fabrics against the original PDF to confirm the data was entered correctly.
