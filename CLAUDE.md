# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## User View
Legit Quilt Kit
- Create a quilt based on several sub components.
- Each quilt has a unique Name
-   Example names
-     Land Of The Free
-     Skulliver
-     Sewphia
- Each quilt is made up of blocks
-   block columns are enumerated 1 to n (configurable per quilt; typically 4 or 8)
-   block rows are enumerated A through N (configurable per quilt; typically A-D or A-H)
-   grid dimensions are stored in `config.json` (`grid_rows`, `grid_cols`) and auto-detected from the overview stage
-   from the overview jpg files, the images that contain Finished Quilt or Pattern Side each will display the row and column numbers for each block.
- Each block is made up of 0 or more segments
-   If a block has 0 segments, then it is only made up of pieces
- Segments are made up from more than one piece.
- A piece is a unique unit of fabric cut to the shape defined from a pattern sheet. Pattern sheets are not a part of this application.
-   See `cut_guide_data.py` — `DATA`
- Blocks are identified by Row and Column (Like A1)
- Segments are identified by the containing block plus a sequential letter
- Pieces are identified with numbers inside a circle on the images.
-   In the list of cuts on the Cut Guide jpg's, the piece identifiers are in parenthesis (like 3 in a circle in the graphics area, (3) after a segment id in the list)

- User workflow
- Processing of quilt data (images) and validation of data will result in a database that can be used by the web application.
- The Web app can host multiple quilts. A user can switch between quilts as desired.

-   Run extract.py to create data files for a new quilt
-     Input
-       - JPG files organized into overview/, cut/, and assy/ folders under the quilt scan folder
-       - Scans live at: `C:\Users\peter\OneDrive - heathprof.com\Quilting\Scans\<quilt-name>\`
-     Output
-       - `quilts/<id>/cut_guide_data.py`, `assembly_data.py`, `assembly_guide.json`, `overview_data.json`
-       - Staging files (`cut_raw.json`, etc.) for resume/re-run support
-       - Excel files for QA and validation
-       - Additional data extraction/tuning may be needed — use --page / --pages to re-run individual pages
-       - Once validated, the data is automatically available in the Web App (auto-discovered)
-   Open Web App
-     - New quilts are auto-discovered from the quilts/ folder — no manual wiring needed
-     - User can switch between quilts via the selector in the UI
-     - Only one quilt is active at a time in the UI

## Running the Web App

```bash
cd quilt-tracker-app
python3 app.py           # default port 3001
python3 app.py --port 3001
```

Access at **http://localhost:3001** (or `http://<pi-ip>:3001` on the RPi).

## Running the Data Tools

```bash
python generate.py                        # Generate CutGuide.xlsx for active quilt
python tracking.py                        # Generate Tracker.xlsx for active quilt
python lint.py                            # Validate cut_guide_data.py
python extract.py <scan_folder>           # Extract all stages from scanned images
```

`extract.py` requires `ANTHROPIC_API_KEY` in the environment (or a `.env` file).

### extract.py options

```bash
# Full extraction for a new quilt (auto-derives quilt-id from folder name)
python extract.py "C:\Users\peter\OneDrive - heathprof.com\Quilting\Scans\NewQuilt"

# Run only one stage
python extract.py "<scan_folder>" --stage cut
python extract.py "<scan_folder>" --stage assy
python extract.py "<scan_folder>" --stage overview
python extract.py "<scan_folder>" --stage colors   # extract hex color per fabric from Color Guide pages

# Resume after a crash — skips already-processed pages
python extract.py "<scan_folder>" --stage cut --resume

# Re-run a single bad page (1-based)
python extract.py "<scan_folder>" --stage cut --page 15

# Re-run a range of pages
python extract.py "<scan_folder>" --stage cut --pages 30-40

# Check what has been processed and what errored
python extract.py "<scan_folder>" --status

# Write final output files from existing staging data (no API calls)
python extract.py "<scan_folder>" --finalize

# Process but don't write output files (for inspection)
python extract.py "<scan_folder>" --stage cut --dry-run

# Skip the default 90° CCW rotation of cut images (use if scans are already upright)
python extract.py "<scan_folder>" --stage cut --no-rotate-cuts

# Fallback: per-page Haiku rotation check after copying (for mixed/non-standard scans)
python extract.py "<scan_folder>" --stage cut --fix-rotation

# Capture cover-page metadata interactively (finished size, complexity, design #,
# colors/pieces expected). Also copies overview_000.jpg -> cover.jpg.
# No API calls; safe to re-run to edit fields.
python extract.py "<scan_folder>" --configure
python extract.py --quilt-id <id> --configure   # without a scan folder
```

Each page is checkpointed immediately after processing into staging files
(`quilts/<id>/cut_raw.json`, etc.). A crash only loses the page in progress.
`overview_001.jpg` is auto-copied as `quilt_overview.jpg` on first overview run
(replace manually if a different image is needed as the background grid).

The grid dimensions and layout are auto-detected from the overview stage (Pattern Side /
Finished Quilt pages) and written to `config.json`. They can also be set manually before
running the assy stage.

- `grid_layout: "row_letters"` (default) — letters label rows (down the side), numbers label columns (across the top). Block ID = letter + number e.g. `A2`.
- `grid_layout: "col_letters"` — letters label columns (across the top), numbers label rows (down the side). Block ID = letter + number e.g. `B1` = column B, row 1. Example: Sewphia.

## Architecture

**Flask Web App (`quilt-tracker-app/`)** — Python 3 / Flask, port 3001:
- `app.py` — routes, progress logic, pattern data loading; auto-discovers quilts from `quilts/`
  - `GET /api/fabrics` — returns fabric list with per-fabric cut segment counts and hex color for the active quilt
  - `POST /api/progress/archive` — generates a zip with an HTML summary report and downloads it
  - `POST /api/progress/reset` — clears all progress for the active quilt
  - Progress writes record `cut_at` / `assembled_at` ISO8601 timestamps in `progress.json`
- `templates/index.html` — single-page UI with quilt selector, Quilt/Colors view toggle, Reset button in header
- `static/app.js`, `static/style.css` — frontend, no build step; grid renders dynamically based on per-quilt grid dimensions and orientation; includes Finished/Pattern Side toggle (pattern side flips background image horizontally and reverses column order)
- **Quilt view** — the normal block grid workflow; click a block to see Segments / Fabrics / Assemble tabs
- **Colors view** — toggle via "Colors" button in header; shows a tile grid of all fabrics with approximate background color, cut count (X/Y), and ✓ done when complete; click a tile to see its segments with checkboxes; checking a segment updates the quilt grid block status in real time
- **Reset / Archive** — "Reset…" button in header opens a modal: "Archive & Reset" downloads a zip (HTML summary report with per-day stats and timeline) then clears progress; "Reset Only" clears progress with confirmation
- **Assemble tab** — visible before all segments are cut; shows X/Y segments ready; checkboxes are disabled until all segments are cut
- Progress stored per-quilt in `quilt-tracker-app/progress/<quilt-id>/`:
  - `progress.json` — fragment-level status (not started / in progress / complete) with `cut_at` / `assembled_at` timestamps
  - `piece_progress.json` — piece-level cut checkboxes
  - `sewing_progress.json` — sewing step checkboxes

**Per-quilt data layer (`quilts/<quilt-id>/`):**
- `cut_guide_data.py` — `DATA`: list of 8-tuples `(fabric_code, fabric_name, sku, fabric_size, piece_num, template_code, quantity, page)` — `piece_num` = Cut #, `template_code` = Segment ID, `quantity` = cut count (number in parentheses after segment ID)
- `assembly_data.py` — `BLOCKS`: dict mapping block ID → list of fragment IDs (count depends on grid size: 64 for 8×8, 16 for 4×4, etc.)
- `assembly_guide.json` — visual assembly data (bboxes, circles, sewing steps) generated by `extract.py`
- `fabric_colors.json` — hex color per fabric code, generated by `extract.py --stage colors`; used by the Colors view to render color-tinted tiles; keys are fabric codes (e.g. `"AF": "#d4a12b"`)
- `overview_data.json` — fabric list and pattern name (gitignored; not needed by app at runtime)
- `config.json` — `quilt_name`, `start_date`, `grid_rows` (string of letter labels e.g. `"ABCDEFGH"`), `grid_cols` (int), `grid_layout` (`"row_letters"` default or `"col_letters"`), `block_orientation` (`"portrait"` default or `"landscape"`); also a `metadata` block (`finished_size`, `complexity` 1-3, `design_number`, `colors_expected`, `pieces_expected`) populated by `extract.py --configure`. Authoritative source for quilt identity, grid size, and cover-page metadata
- `quilt_overview.jpg` — background grid image (overview_001 = finished quilt photo); used for both Finished and Pattern Side views (pattern side is horizontally flipped)
- `cover.jpg` — optional, copied from `overview_000.jpg` during `extract.py --configure` or any full overview run; shown in the Quilt Overview panel
- `assy/` — assembly guide images (one per multi-fragment block)

**Staging files (`quilts/<quilt-id>/`, gitignored):**
- `cut_raw.json`, `assy_raw.json`, `assy_visual_raw.json`, `overview_raw.json`, `colors_raw.json`
- One entry per source image; status: ok / warning / error
- Used by `extract.py --resume` and `--finalize`

**Active quilts:**
- `land-of-the-free` — Land of the Free (86 fabrics, 1,614 cut rows, 8×8 grid, 86 colors)
- `skulliver` — Skulliver (106 fabrics, 912 cut rows, 8×8 grid, 106 colors)
- `sewphia` — Sewphia (71 fabrics, 1,103 cut rows, 4×4 grid A-D × 1-4, 71 colors)

**Excel generators:**
- `generate.py` → CutGuide xlsx (Cut Guide, By Fabric Code, Statistics sheets)
- `tracking.py` → Tracker xlsx (6 sheets including block completion checklist)
- `lint.py` — validates `cut_guide_data.py` for duplicate/missing pieces

## How to Add a New Quilt

1. **Scan the quilt** — scan all pages into JPGs organized into three subfolders:
   ```
   C:\Users\peter\OneDrive - heathprof.com\Quilting\Scans\<quilt-name>\
       overview\   ← overview/grid photos
       cut\        ← cut guide pages
       assy\       ← assembly guide pages
   ```
   **Cut page scan convention:** feed cut pages into the scanner in portrait
   orientation with the page title on the long-edge that ends up on the *right*
   side of the JPG. `extract.py` rotates cut images 90° CCW on copy by default,
   which makes the title land at the top. Pass `--no-rotate-cuts` if your scans
   are already upright, or `--fix-rotation` to fall back to per-page Haiku
   detection for mixed/non-standard scans.

2. **Run extract.py** (from the repo root, on a machine with `ANTHROPIC_API_KEY` set):
   ```bash
   python extract.py "C:\Users\peter\OneDrive - heathprof.com\Quilting\Scans\<quilt-name>"
   ```
   This auto-derives the quilt ID from the folder name and generates:
   - `quilts/<id>/config.json` — grid dimensions, quilt name, layout settings
   - `quilts/<id>/cut_guide_data.py` — fabric/piece cutting data
   - `quilts/<id>/assembly_data.py` — assembly sequence data
   - `quilts/<id>/assembly_guide.json` — visual assembly guide
   - `quilts/<id>/quilt_overview.jpg` — copied from overview_001.jpg (replace if needed)
   - `quilts/<id>/assy/assy_NNN.jpg` — assembly step photos
   - Staging files (`cut_raw.json`, etc.) and Excel QA files

3. **Validate** — review the generated Excel files, run lint:
   ```bash
   python lint.py
   ```
   Use `--page` / `--pages` / `--resume` options to fix any problem pages, then `--finalize`.

4. **Adjust config.json if needed** — check `grid_layout` (`row_letters` vs `col_letters`) and `block_orientation` (`portrait` vs `landscape`). See Configuration section below.

5. **Capture cover-page metadata** (interactive):
   ```bash
   python extract.py "<scan_folder>" --configure
   ```
   Prompts for finished size, complexity (1=Faster, 2=Moderate, 3=Detailed), design #, and the colors / pieces counts shown on the cover (used as a cross-check against extracted data — a warning is printed at the end of any future full extract if they disagree). Also copies `overview_000.jpg` to `cover.jpg` for display in the Quilt Overview panel.

6. **Commit and push:**
   ```bash
   git add quilts/<id>/
   git commit -m "Add <quilt-name> quilt data"
   git push
   ```

7. **Deploy on the Pi:**
   ```bash
   cd ~/legit-kits-cut-guide && git pull && pm2 restart quilttracker
   ```
   The new quilt is auto-discovered from the `quilts/` folder — no code changes needed. It will appear immediately in the quilt selector in the UI.

---

## Deployment on Raspberry Pi

The app runs on the same RPi 4 as HomeTempDashboard (port 3001 vs 3000), managed by pm2.
Pi host: **`peterh226@192.168.50.143`**.

**One-time setup:**
```bash
pip install flask openpyxl pillow anthropic
pm2 start ~/legit-kits-cut-guide/quilt-tracker-app/app.py --name quilttracker --interpreter python3
pm2 save
```

**Update workflow (code/data tracked in git):**
```bash
cd ~/legit-kits-cut-guide && git pull && pm2 restart quilttracker
```
Then shift-refresh in the browser for CSS/JS changes.

**Updating cut images on the Pi**

The `quilts/*/cut/` folders are gitignored (~98MB total), so `git pull` does **not** sync them.
After running `extract.py` or rotating images locally, copy the affected quilt's `cut/` folder up
manually. From the repo root on the dev machine:

```bash
# Replace contents (drops stale files):
rsync -av --delete quilts/<quilt-id>/cut/ peterh226@192.168.50.143:~/legit-kits-cut-guide/quilts/<quilt-id>/cut/

# Or simple scp (won't remove stale files):
scp -r quilts/<quilt-id>/cut peterh226@192.168.50.143:~/legit-kits-cut-guide/quilts/<quilt-id>/
```

Then shift-refresh the browser to bypass cached images. No pm2 restart needed for image-only changes.

**Useful commands:**
```bash
pm2 logs quilttracker    # live console output
pm2 status               # check if running
```

## Configuration

No config file needed for the web app. Each quilt's data lives in `quilts/<id>/` and is
auto-discovered on startup. `config.json` in each quilt folder sets `quilt_name`, `start_date`,
`grid_rows`, `grid_cols`, `grid_layout`, and `block_orientation`. Defaults: `"ABCDEFGH"` / `8` / `"row_letters"` / `"portrait"`.

**block_orientation** controls block cell aspect ratio in the grid:
- `"portrait"` (default) — block cells are taller than wide (45×60px); short side faces the column numbers. LotF, Skulliver.
- `"landscape"` — block cells are wider than tall (60×45px); long side faces the column numbers. Sewphia.

**Grid views** — the UI toggle switches between:
- **Finished** (default): overview_001 image unflipped; column numbers run n→1 left to right.
- **Pattern Side**: overview_001 image CSS-flipped horizontally; column numbers run 1→n left to right.

Progress files (`progress/`, `piece_progress/`, `sewing_progress/` under `quilt-tracker-app/progress/<id>/`)
are gitignored — they live only on the Pi and persist quilt progress across sessions.
