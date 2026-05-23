"""
Quilt Tracker App
==================
Flask web app for tracking quilt progress.
Reads pattern data from ../quilts/<quilt-id>/ and stores progress in progress/<quilt-id>/.

Usage:
    python3 app.py
    python3 app.py --port 3001

Access at http://<pi-ip>:3001
"""

import argparse
import io
import json
import subprocess
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, render_template, send_file

app = Flask(__name__)

QUILTS_DIR   = Path(__file__).parent.parent / "quilts"
PROGRESS_DIR = Path(__file__).parent / "progress"

_cache = {}  # quilt_id -> {"pattern": ..., "assy_guide": ...}


# ---------------------------------------------------------------------------
# Quilt discovery
# ---------------------------------------------------------------------------

def get_quilt_ids():
    if not QUILTS_DIR.exists():
        return []
    return sorted(p.name for p in QUILTS_DIR.iterdir() if p.is_dir())


def get_quilt_info(quilt_id):
    config_path = QUILTS_DIR / quilt_id / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    return {"id": quilt_id, "name": config.get("quilt_name", quilt_id)}


def get_active_quilt():
    quilt_id = request.args.get("quilt")
    available = get_quilt_ids()
    if quilt_id in available:
        return quilt_id
    return available[0] if available else None


# ---------------------------------------------------------------------------
# Pattern loading (cached per quilt)
# ---------------------------------------------------------------------------

def get_quilt_data(quilt_id):
    if quilt_id not in _cache:
        data_dir  = QUILTS_DIR / quilt_id
        assy_path = data_dir / "assembly_guide.json"
        assy_guide = json.loads(assy_path.read_text(encoding="utf-8")) if assy_path.exists() else {}
        _cache[quilt_id] = {"pattern": _load_pattern(data_dir), "assy_guide": assy_guide}
    return _cache[quilt_id]


def _load_pattern(data_dir):
    cut_globals = {}
    exec((data_dir / "cut_guide_data.py").read_text(encoding="utf-8"), cut_globals)
    DATA = cut_globals["DATA"]

    asm_globals = {}
    exec((data_dir / "assembly_data.py").read_text(encoding="utf-8"), asm_globals)
    BLOCKS = asm_globals["BLOCKS"]

    overview_path = data_dir / "overview_data.json"
    overview = json.loads(overview_path.read_text(encoding="utf-8")) if overview_path.exists() else []

    config_path = data_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if config.get("quilt_name"):
        pattern_name = config["quilt_name"]
    else:
        pattern_name = "Quilt"
        for page in overview:
            if page.get("quilt_name"):
                pattern_name = page["quilt_name"]
                break
    start_date  = config.get("start_date", "")
    grid_rows         = config.get("grid_rows", "ABCDEFGH")
    grid_cols         = int(config.get("grid_cols", 8))
    grid_layout       = config.get("grid_layout", "row_letters")
    block_orientation = config.get("block_orientation", "portrait")

    fabrics = {}
    for row in DATA:
        code, name, sku, size, piece_num, tmpl, qty, page = row
        if code not in fabrics:
            fabrics[code] = {"name": name, "sku": sku, "size": size, "page": page, "pieces": []}
        fabrics[code]["pieces"].append({"piece_num": piece_num, "template": tmpl, "quantity": qty})

    tmpl_to_block = {}
    for block_id in BLOCKS:
        for frag in BLOCKS[block_id]:
            tmpl_to_block[frag] = block_id

    block_pieces = defaultdict(list)
    for row in DATA:
        code, name, sku, size, piece_num, tmpl, qty, page = row
        matched_block = None
        for frag_id, block_id in tmpl_to_block.items():
            if tmpl == frag_id:
                matched_block = block_id
                break
            if tmpl.startswith(frag_id):
                next_ch = tmpl[len(frag_id):]
                if next_ch and next_ch[0].islower():
                    matched_block = block_id
                    break
        if matched_block:
            block_pieces[matched_block].append({
                "fabric_code": code, "fabric_name": name,
                "piece_num": piece_num, "template": tmpl,
                "quantity": qty, "page": page,
            })

    blocks = {}
    for block_id, frags in BLOCKS.items():
        blocks[block_id] = {
            "fragments": frags,
            "is_single": len(frags) == 1,
            "pieces":    block_pieces.get(block_id, []),
        }

    return {
        "name":              pattern_name,
        "start_date":        start_date,
        "blocks":            blocks,
        "fabrics":           fabrics,
        "grid_rows":         grid_rows,
        "grid_cols":         grid_cols,
        "grid_layout":       grid_layout,
        "block_orientation": block_orientation,
    }


# ---------------------------------------------------------------------------
# Progress state (per quilt)
# ---------------------------------------------------------------------------

def _progress_files(quilt_id):
    d = PROGRESS_DIR / quilt_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "progress.json", d / "piece_progress.json", d / "sewing_progress.json"


def load_progress(quilt_id):
    f, _, _ = _progress_files(quilt_id)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_progress(quilt_id, progress):
    f, _, _ = _progress_files(quilt_id)
    f.write_text(json.dumps(progress, indent=2), encoding="utf-8")


def load_piece_progress(quilt_id):
    _, f, _ = _progress_files(quilt_id)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_piece_progress(quilt_id, data):
    _, f, _ = _progress_files(quilt_id)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_sewing_progress(quilt_id):
    _, _, f = _progress_files(quilt_id)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_sewing_progress(quilt_id, data):
    _, _, f = _progress_files(quilt_id)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def compute_block_status(block_id, pattern, progress):
    frags = pattern["blocks"][block_id]["fragments"]
    bp = progress.get(block_id, {})
    done = sum(1 for f in frags if bp.get(f, {}).get("assembled", False))
    if done == len(frags):
        return "complete"
    if done > 0 or any(bp.get(f, {}).get("cut", False) for f in frags):
        return "in_progress"
    return "not_started"


def build_stats(pattern, progress):
    statuses = [compute_block_status(b, pattern, progress) for b in pattern["blocks"]]
    total = len(statuses)
    complete = statuses.count("complete")
    in_progress = statuses.count("in_progress")
    return {
        "total":        total,
        "complete":     complete,
        "in_progress":  in_progress,
        "not_started":  total - complete - in_progress,
        "pct_complete": round(complete / total * 100) if total else 0,
    }


# ---------------------------------------------------------------------------
# Routes — quilt discovery & images
# ---------------------------------------------------------------------------

@app.route("/api/quilts")
def api_quilts():
    return jsonify([get_quilt_info(qid) for qid in get_quilt_ids()])


@app.route("/quilts/<quilt_id>/overview.jpg")
def quilt_overview_image(quilt_id):
    path = QUILTS_DIR / quilt_id / "quilt_overview.jpg"
    if not path.exists():
        return "", 404
    response = send_file(path, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/quilts/<quilt_id>/assy/<filename>")
def quilt_assy_image(quilt_id, filename):
    path = QUILTS_DIR / quilt_id / "assy" / filename
    if not path.exists():
        return "", 404
    return send_file(path, mimetype="image/jpeg")


# ---------------------------------------------------------------------------
# Routes — pattern & progress
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    quilts = [get_quilt_info(qid) for qid in get_quilt_ids()]
    first_name = quilts[0]["name"] if quilts else "Quilt Tracker"
    return render_template("index.html", pattern_name=first_name, quilts=quilts, version=GIT_VERSION)


@app.route("/api/pattern")
def api_pattern():
    quilt_id = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404
    qd = get_quilt_data(quilt_id)
    pattern  = qd["pattern"]
    progress = load_progress(quilt_id)
    grid = []
    for block_id in pattern["blocks"]:
        b = pattern["blocks"][block_id]
        frags = b["fragments"]
        bp = progress.get(block_id, {})
        grid.append({
            "id":          block_id,
            "row":         block_id[0],
            "col":         int(block_id[1]),
            "fragments":   [
                {"id": f, "cut": bp.get(f, {}).get("cut", False),
                 "assembled": bp.get(f, {}).get("assembled", False)}
                for f in frags
            ],
            "status":      compute_block_status(block_id, pattern, progress),
            "is_single":   b["is_single"],
            "piece_count": len(b["pieces"]),
        })
    return jsonify({
        "name":              pattern["name"],
        "start_date":        pattern["start_date"],
        "grid":              grid,
        "grid_rows":         pattern["grid_rows"],
        "grid_cols":         pattern["grid_cols"],
        "grid_layout":       pattern["grid_layout"],
        "block_orientation": pattern["block_orientation"],
        "stats":             build_stats(pattern, progress),
    })


@app.route("/api/block/<block_id>")
def api_block(block_id):
    quilt_id = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404
    pattern  = get_quilt_data(quilt_id)["pattern"]
    if block_id not in pattern["blocks"]:
        return jsonify({"error": "Unknown block"}), 404
    progress = load_progress(quilt_id)
    b  = pattern["blocks"][block_id]
    bp = progress.get(block_id, {})

    frag_qty = {f: 0 for f in b["fragments"]}
    for piece in b["pieces"]:
        tmpl = piece["template"]
        for frag in b["fragments"]:
            if tmpl == frag:
                frag_qty[frag] += 1
                break
            if tmpl.startswith(frag):
                next_ch = tmpl[len(frag):]
                if next_ch and next_ch[0].islower():
                    frag_qty[frag] += 1
                    break

    return jsonify({
        "id":     block_id,
        "status": compute_block_status(block_id, pattern, progress),
        "fragments": [
            {"id": f, "cut": bp.get(f, {}).get("cut", False),
             "assembled": bp.get(f, {}).get("assembled", False),
             "piece_count": frag_qty.get(f, 0)}
            for f in b["fragments"]
        ],
        "pieces": b["pieces"],
    })


@app.route("/api/progress", methods=["POST"])
def api_update_progress():
    quilt_id = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404
    data     = request.json
    block_id = data.get("block_id")
    frag_id  = data.get("fragment_id")
    field    = data.get("field")
    value    = data.get("value")

    if not all([block_id, frag_id, field in ("cut", "assembled")]):
        return jsonify({"error": "Invalid request"}), 400
    pattern = get_quilt_data(quilt_id)["pattern"]
    if block_id not in pattern["blocks"]:
        return jsonify({"error": "Unknown block"}), 404

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    progress = load_progress(quilt_id)
    progress.setdefault(block_id, {}).setdefault(frag_id, {})
    progress[block_id][frag_id][field] = value
    if value:
        progress[block_id][frag_id][field + "_at"] = now
    else:
        progress[block_id][frag_id].pop(field + "_at", None)
    if field == "assembled" and value:
        progress[block_id][frag_id]["cut"] = True
        if "cut_at" not in progress[block_id][frag_id]:
            progress[block_id][frag_id]["cut_at"] = now
    save_progress(quilt_id, progress)
    return jsonify({
        "status": compute_block_status(block_id, pattern, progress),
        "stats":  build_stats(pattern, progress),
    })


@app.route("/api/assembly/<block_id>")
def api_assembly(block_id):
    quilt_id   = get_active_quilt()
    assy_guide = get_quilt_data(quilt_id)["assy_guide"] if quilt_id else {}
    if block_id not in assy_guide:
        return jsonify({"error": "No assembly data"}), 404
    return jsonify(assy_guide[block_id])


@app.route("/api/sewing_progress")
def api_get_sewing_progress():
    quilt_id = get_active_quilt()
    return jsonify(load_sewing_progress(quilt_id) if quilt_id else {})


@app.route("/api/sewing_progress", methods=["POST"])
def api_set_sewing_progress():
    quilt_id   = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404
    data       = request.json
    block_id   = data.get("block_id")
    step_index = data.get("step_index")
    checked    = data.get("checked")
    sp = load_sewing_progress(quilt_id)
    sp.setdefault(block_id, {})[str(step_index)] = checked
    save_sewing_progress(quilt_id, sp)
    return jsonify({"ok": True})


@app.route("/api/piece_progress")
def api_get_piece_progress():
    quilt_id = get_active_quilt()
    return jsonify(load_piece_progress(quilt_id) if quilt_id else {})


@app.route("/api/piece_progress", methods=["POST"])
def api_set_piece_progress():
    quilt_id  = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404
    data      = request.json
    block_id  = data.get("block_id")
    frag_id   = data.get("frag_id")
    piece_num = data.get("piece_num")
    checked   = data.get("checked")
    pp = load_piece_progress(quilt_id)
    pp.setdefault(block_id, {}).setdefault(frag_id, {})[str(piece_num)] = checked
    save_piece_progress(quilt_id, pp)
    return jsonify({"ok": True})


@app.route("/api/excel")
def api_excel_list():
    quilt_id = get_active_quilt()
    if not quilt_id:
        return jsonify([])
    files = sorted(p.name for p in (QUILTS_DIR / quilt_id).glob("*.xlsx"))
    return jsonify(files)


@app.route("/api/excel/<filename>")
def api_excel_download(filename):
    if "/" in filename or "\\" in filename or not filename.endswith(".xlsx"):
        return "", 400
    for quilt_id in get_quilt_ids():
        path = QUILTS_DIR / quilt_id / filename
        if path.exists():
            resp = send_file(path, as_attachment=True)
            resp.headers["Cache-Control"] = "no-store"
            return resp
    return "", 404


def build_archive_summary(quilt_id):
    pattern       = get_quilt_data(quilt_id)["pattern"]
    progress      = load_progress(quilt_id)
    piece_progress = load_piece_progress(quilt_id)

    total_blocks      = len(pattern["blocks"])
    blocks_complete   = sum(1 for b in pattern["blocks"] if compute_block_status(b, pattern, progress) == "complete")
    blocks_in_progress = sum(1 for b in pattern["blocks"] if compute_block_status(b, pattern, progress) == "in_progress")

    total_segments    = sum(len(b["fragments"]) for b in pattern["blocks"].values())
    segments_cut      = sum(1 for bp in progress.values() for fp in bp.values() if isinstance(fp, dict) and fp.get("cut"))
    segments_assembled = sum(1 for bp in progress.values() for fp in bp.values() if isinstance(fp, dict) and fp.get("assembled"))

    total_pieces   = sum(len(b["pieces"]) for b in pattern["blocks"].values())
    pieces_checked = sum(
        1 for block_checks in piece_progress.values()
        for frag_checks in block_checks.values()
        for v in frag_checks.values() if v
    )

    # Collect timestamps from progress.json
    cut_by_date = defaultdict(int)
    assy_by_date = defaultdict(int)
    all_timestamps = []
    for bp in progress.values():
        for fp in bp.values():
            if not isinstance(fp, dict):
                continue
            if fp.get("cut_at"):
                d = fp["cut_at"][:10]
                cut_by_date[d] += 1
                all_timestamps.append(fp["cut_at"])
            if fp.get("assembled_at"):
                d = fp["assembled_at"][:10]
                assy_by_date[d] += 1
                all_timestamps.append(fp["assembled_at"])

    summary = {
        "quilt_name":   pattern["name"],
        "start_date":   pattern["start_date"],
        "archive_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "blocks":    {"total": total_blocks, "complete": blocks_complete,
                      "in_progress": blocks_in_progress,
                      "not_started": total_blocks - blocks_complete - blocks_in_progress},
        "segments":  {"total": total_segments, "cut": segments_cut, "assembled": segments_assembled},
        "pieces":    {"total": total_pieces, "checked": pieces_checked},
        "timeline":  None,
    }

    if all_timestamps:
        all_timestamps.sort()
        first_ts = all_timestamps[0]
        last_ts  = all_timestamps[-1]
        all_dates = sorted(set(t[:10] for t in all_timestamps))
        active_days = len(all_dates)
        first_date = datetime.strptime(first_ts[:10], "%Y-%m-%d")
        last_date  = datetime.strptime(last_ts[:10],  "%Y-%m-%d")
        calendar_days = (last_date - first_date).days + 1
        per_day = [
            {"date": d,
             "segments_cut": cut_by_date[d],
             "segments_assembled": assy_by_date[d]}
            for d in all_dates
        ]
        most_productive = max(per_day, key=lambda d: d["segments_cut"] + d["segments_assembled"])
        summary["timeline"] = {
            "first_action":    first_ts,
            "last_action":     last_ts,
            "calendar_days":   calendar_days,
            "active_days":     active_days,
            "avg_segments_cut_per_active_day":      round(segments_cut / active_days, 1) if active_days else 0,
            "avg_segments_assembled_per_active_day": round(segments_assembled / active_days, 1) if active_days else 0,
            "most_productive_day": most_productive,
            "per_day": per_day,
        }
    else:
        summary["timeline_note"] = "Timestamp tracking was not enabled when this progress was recorded — timing data unavailable."

    return summary


def build_archive_html(s):
    tl = s.get("timeline")
    if tl:
        mpd = tl["most_productive_day"] or {}
        per_day_rows = "\n".join(
            f"<tr><td>{d['date']}</td><td>{d['segments_cut']}</td><td>{d['segments_assembled']}</td></tr>"
            for d in tl["per_day"]
        )
        timeline_html = f"""
        <section>
            <h2>Timeline</h2>
            <table class="kv">
                <tr><th>First action</th><td>{tl['first_action'][:10]}</td></tr>
                <tr><th>Last action</th><td>{tl['last_action'][:10]}</td></tr>
                <tr><th>Calendar days</th><td>{tl['calendar_days']}</td></tr>
                <tr><th>Active days</th><td>{tl['active_days']}</td></tr>
                <tr><th>Avg segments cut / active day</th><td>{tl['avg_segments_cut_per_active_day']}</td></tr>
                <tr><th>Avg segments assembled / active day</th><td>{tl['avg_segments_assembled_per_active_day']}</td></tr>
                <tr><th>Most productive day</th><td>{mpd.get('date','—')}
                    ({mpd.get('segments_cut',0)} cut, {mpd.get('segments_assembled',0)} assembled)</td></tr>
            </table>
            <h3>Daily Breakdown</h3>
            <table>
                <thead><tr><th>Date</th><th>Segments Cut</th><th>Segments Assembled</th></tr></thead>
                <tbody>{per_day_rows}</tbody>
            </table>
        </section>"""
    else:
        note = s.get("timeline_note", "No timing data available.")
        timeline_html = f'<section><h2>Timeline</h2><p class="note">{note}</p></section>'

    b, seg, p = s["blocks"], s["segments"], s["pieces"]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{s['quilt_name']} — Archive</title>
<style>
  body {{ font-family: Arial, sans-serif; background: #f9f9f9; color: #333; padding: 32px; max-width: 820px; margin: auto; }}
  h1   {{ color: #c0392b; margin-bottom: 4px; }}
  h2   {{ color: #1a5276; margin: 28px 0 12px; border-bottom: 2px solid #d5d8dc; padding-bottom: 4px; }}
  h3   {{ color: #1a5276; margin: 20px 0 8px; }}
  section {{ background: #fff; border-radius: 8px; padding: 24px 28px; margin-bottom: 20px;
              box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
  .meta {{ color: #777; margin-bottom: 24px; font-size: 0.9rem; }}
  .stat-row {{ display: flex; gap: 32px; flex-wrap: wrap; }}
  .stat {{ text-align: center; min-width: 110px; }}
  .stat .big {{ font-size: 2rem; font-weight: bold; color: #1a5276; }}
  .stat .lbl {{ font-size: 0.8rem; color: #888; margin-top: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ background: #f0f3f4; font-weight: bold; }}
  table.kv th {{ width: 260px; }}
  .note {{ color: #888; font-style: italic; }}
</style>
</head>
<body>
<h1>{s['quilt_name']} — Completion Archive</h1>
<p class="meta">Archived: {s['archive_date']} &nbsp;|&nbsp; Started: {s['start_date'] or '—'}</p>

<section>
  <h2>Summary</h2>
  <div class="stat-row">
    <div class="stat"><div class="big">{b['complete']}/{b['total']}</div><div class="lbl">Blocks Complete</div></div>
    <div class="stat"><div class="big">{seg['assembled']}/{seg['total']}</div><div class="lbl">Segments Assembled</div></div>
    <div class="stat"><div class="big">{seg['cut']}/{seg['total']}</div><div class="lbl">Segments Cut</div></div>
    <div class="stat"><div class="big">{p['checked']}/{p['total']}</div><div class="lbl">Pieces Checked Off</div></div>
  </div>
</section>

{timeline_html}
</body>
</html>"""


@app.route("/api/progress/archive", methods=["POST"])
def api_archive():
    quilt_id = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404

    summary = build_archive_summary(quilt_id)
    html    = build_archive_html(summary)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.json", json.dumps(summary, indent=2))
        zf.writestr("summary.html", html)
        pf, ppf, spf = _progress_files(quilt_id)
        if pf.exists():  zf.write(pf,  "progress.json")
        if ppf.exists(): zf.write(ppf, "piece_progress.json")
        if spf.exists(): zf.write(spf, "sewing_progress.json")
    buf.seek(0)

    slug     = quilt_id.replace("-", "_")
    date_str = summary["archive_date"].replace("-", "")
    filename = f"{slug}_archive_{date_str}.zip"
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=filename)


@app.route("/api/fabrics")
def api_fabrics():
    quilt_id = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404

    pattern  = get_quilt_data(quilt_id)["pattern"]
    progress = load_progress(quilt_id)

    colors_path = QUILTS_DIR / quilt_id / "fabric_colors.json"
    colors = json.loads(colors_path.read_text(encoding="utf-8")) if colors_path.exists() else {}

    def frag_for_template(template, fragments):
        for frag in fragments:
            if template == frag:
                return frag
            if template.startswith(frag) and len(template) > len(frag) and template[len(frag)].islower():
                return frag
        return None

    # Collect unique (block_id, frag_id) per fabric, preserving pattern order
    seen_pairs: dict[str, set] = defaultdict(set)
    frag_cut: dict = {}

    for block_id, block in pattern["blocks"].items():
        bp = progress.get(block_id, {})
        for piece in block["pieces"]:
            code    = piece["fabric_code"]
            frag_id = frag_for_template(piece["template"], block["fragments"])
            if frag_id:
                key = (block_id, frag_id)
                if key not in seen_pairs[code]:
                    seen_pairs[code].add(key)
                    fp = bp.get(frag_id, {})
                    frag_cut[key] = bool(fp.get("cut")) if isinstance(fp, dict) else False

    result = []
    for code, fab in pattern["fabrics"].items():
        pairs = sorted(seen_pairs.get(code, set()))
        segs  = [{"block_id": b, "frag_id": f, "cut": frag_cut.get((b, f), False)} for b, f in pairs]
        result.append({
            "code":     code,
            "name":     fab["name"],
            "sku":      fab.get("sku", ""),
            "size":     fab.get("size", ""),
            "color":    colors.get(code),
            "segments": segs,
            "total":    len(segs),
            "cut":      sum(1 for s in segs if s["cut"]),
        })

    result.sort(key=lambda f: f["code"])
    return jsonify(result)


@app.route("/api/progress/reset", methods=["POST"])
def api_reset():
    quilt_id = get_active_quilt()
    if not quilt_id:
        return jsonify({"error": "No quilts found"}), 404
    save_progress(quilt_id, {})
    save_piece_progress(quilt_id, {})
    save_sewing_progress(quilt_id, {})
    pattern = get_quilt_data(quilt_id)["pattern"]
    return jsonify({"ok": True, "stats": build_stats(pattern, {})})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _generate_excel_files():
    root = Path(__file__).parent.parent
    for quilt_id in get_quilt_ids():
        config_path = QUILTS_DIR / quilt_id / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        quilt_name = config.get("quilt_name", quilt_id)
        slug = "".join(w.capitalize() for w in quilt_name.split())
        quilt_dir = QUILTS_DIR / quilt_id
        print(f"Generating {slug}_CutGuide.xlsx ...")
        subprocess.run([sys.executable, str(root / "generate.py"), "--quilt-id", quilt_id],
                       check=False, cwd=str(root))
        print(f"Generating {slug}_Tracker.xlsx ...")
        subprocess.run([sys.executable, str(root / "tracking.py"), "--quilt-id", quilt_id],
                       check=False, cwd=str(root))


def _git_version():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent.parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


GIT_VERSION = _git_version()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    quilts = get_quilt_ids()
    print(f"Quilt Tracker: http://{args.host}:{args.port}  [{GIT_VERSION}]")
    print(f"Quilts found: {', '.join(quilts) if quilts else 'none'}")
    _generate_excel_files()
    app.run(host=args.host, port=args.port, debug=False)
