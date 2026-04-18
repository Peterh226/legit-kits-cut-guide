# Adding a New Pattern

This guide explains how to add cut guide data for a new Legit Kits pattern.

---

## Workflow

1. **Scan the booklets** — scan each page as a separate PNG image (one page per file)
2. **Organize into folders** under a pattern folder:
   ```
   <pattern_folder>/
   ├── cut/       cut_01.png, cut_02.png, ...   (cut guide pages)
   ├── assy/      assy_01.png, assy_02.png, ...  (assembly guide pages)
   └── overview/  overview_01.png, ...           (overview/kit contents pages)
   ```
3. **Run the extractor** (requires `ANTHROPIC_API_KEY`):
   ```bash
   python extract.py <pattern_folder>
   ```
   This calls the Claude vision API on each image, writes `data/cut_guide_data.py`
   and `data/assembly_data.py`, then runs `generate.py` and `tracking.py`.

4. **Review the output** — open the generated `.xlsx` files and spot-check a few
   fabrics against the original scans to confirm accuracy.

5. **Run lint** to catch any data issues:
   ```bash
   python lint.py
   ```

---

## Data Format

Each row in `DATA` is an 8-element tuple:

```python
(fabric_code, fabric_name, sku, fabric_size, piece_num, template_code, quantity, page)
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
| `page` | int | `1` | Cut guide page number |

---

## Style Conventions

Group entries by fabric code and add a comment header:

```python
# ------------------------------------------------------------------
# AF  Saffron  1320  Fat 1/8YD  (p.1)
# ------------------------------------------------------------------
("AF", "Saffron", "1320", "Fat 1/8YD", 1, "F3m", 3, 1),
("AF", "Saffron", "1320", "Fat 1/8YD", 2, "F3s", 1, 1),
...
```

---

## Reading the Cut Guide Pages

Each scanned page shows one or more fabrics. For each fabric:
- **Top right**: fabric swatch color, fabric name, SKU number, and yardage — the fabric header
- **Two large letters** (e.g. `AF`) — the fabric code
- **A layout diagram** with circled numbers — the piece numbers
- **A legend** listing each piece: `① F3m(3)` = piece 1, template F3m, 3 cuts

The piece list is **split across two areas** of the page — read both sides to get all pieces.

Some pages show **two fabrics** (one in each half of the page) — parse each separately.

Some fabrics span **two pages** (e.g. Cappuccino UP, pages 58–59) — continue numbering sequentially.
