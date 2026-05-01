# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## User View
Legit Quilt Kit
- Create a quilt based on several sub components.
- Each quilt has a unique Name
-   Example names
-     Land Of The Free
-     Skulliver
- Each quilt is made up of blocks
-   block columns are enumerated 1 to n, where n is typically 8
-   block rows are enumerated A through H typically, but can be less
-   from the assembly jpg files, the images that contain Finished Quilt or Pattern Side each will display the row and column numbers for each block.
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
-       - JPG files organized into overview/, cut/, and assy/ folders under the Quilt name
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
python extract.py <pattern_folder>        # Extract all stages from scanned images
```

`extract.py` requires `ANTHROPIC_API_KEY` in the environment (or a `.env` file).

### extract.py options

```bash
# Full extraction for a new quilt (auto-derives quilt-id from folder name)
python extract.py ../Skulliver

# Run only one stage
python extract.py ../Skulliver --stage cut
python extract.py ../Skulliver --stage assy
python extract.py ../Skulliver --stage overview

# Resume after a crash — skips already-processed pages
python extract.py ../Skulliver --stage cut --resume

# Re-run a single bad page (1-based)
python extract.py ../Skulliver --stage cut --page 15

# Re-run a range of pages
python extract.py ../Skulliver --stage cut --pages 30-40

# Check what has been processed and what errored
python extract.py ../Skulliver --status

# Write final output files from existing staging data (no API calls)
python extract.py ../Skulliver --finalize

# Process but don't write output files (for inspection)
python extract.py ../Skulliver --stage cut --dry-run
```

Each page is checkpointed immediately after processing into staging files
(`quilts/<id>/cut_raw.json`, etc.). A crash only loses the page in progress.
`overview_001.jpg` is auto-copied as `quilt_overview.jpg` on first overview run
(replace manually if a different image is needed as the background grid).

## Architecture

**Flask Web App (`quilt-tracker-app/`)** — Python 3 / Flask, port 3001:
- `app.py` — routes, progress logic, pattern data loading; auto-discovers quilts from `quilts/`
- `templates/index.html` — single-page UI with quilt selector
- `static/app.js`, `static/style.css` — frontend, no build step
- Progress stored per-quilt in `quilt-tracker-app/progress/<quilt-id>/`:
  - `progress.json` — fragment-level status (not started / in progress / complete)
  - `piece_progress.json` — piece-level cut checkboxes
  - `sewing_progress.json` — sewing step checkboxes

**Per-quilt data layer (`quilts/<quilt-id>/`):**
- `cut_guide_data.py` — `DATA`: list of 8-tuples `(fabric_code, fabric_name, sku, fabric_size, cut_num, segment_id, sew_sequence, page)` — maps to Cut Guide Excel columns Cut #, Segment ID, Sew Sequence, Page
- `assembly_data.py` — `BLOCKS`: dict mapping block ID → list of fragment IDs (64 blocks)
- `assembly_guide.json` — visual assembly data (bboxes, circles, sewing steps) generated by `extract.py`
- `overview_data.json` — fabric list and pattern name (gitignored; not needed by app at runtime)
- `config.json` — `quilt_name` and `start_date`; authoritative source for quilt name
- `quilt_overview.jpg` — background grid image shown behind the block grid
- `assy/` — assembly guide images (one per multi-fragment block)

**Staging files (`quilts/<quilt-id>/`, gitignored):**
- `cut_raw.json`, `assy_raw.json`, `assy_visual_raw.json`, `overview_raw.json`
- One entry per source image; status: ok / warning / error
- Used by `extract.py --resume` and `--finalize`

**Active quilts:**
- `land-of-the-free` — Land of the Free (86 fabrics, 1,614 cut rows)
- `skulliver` — Skulliver (106 fabrics, 912 cut rows)

**Excel generators:**
- `generate.py` → CutGuide xlsx (Cut Guide, By Fabric Code, Statistics sheets)
- `tracking.py` → Tracker xlsx (6 sheets including block completion checklist)
- `lint.py` — validates `cut_guide_data.py` for duplicate/missing pieces

## Deployment on Raspberry Pi

The app runs on the same RPi 4 as HomeTempDashboard (port 3001 vs 3000), managed by pm2.

**One-time setup:**
```bash
pip install flask openpyxl pillow anthropic
pm2 start ~/legit-kits-cut-guide/quilt-tracker-app/app.py --name quilttracker --interpreter python3
pm2 save
```

**Update workflow:**
```bash
cd ~/legit-kits-cut-guide && git pull && pm2 restart quilttracker
```
Then shift-refresh in the browser for CSS/JS changes.

**Useful commands:**
```bash
pm2 logs quilttracker    # live console output
pm2 status               # check if running
```

## Configuration

No config file needed for the web app. Each quilt's data lives in `quilts/<id>/` and is
auto-discovered on startup. `config.json` in each quilt folder sets `quilt_name` and `start_date`.

Progress files (`progress/`, `piece_progress/`, `sewing_progress/` under `quilt-tracker-app/progress/<id>/`)
are gitignored — they live only on the Pi and persist quilt progress across sessions.
