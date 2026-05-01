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
import json
import subprocess
import sys
from collections import defaultdict
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
    start_date = config.get("start_date", "")

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
        "name":       pattern_name,
        "start_date": start_date,
        "blocks":     blocks,
        "fabrics":    fabrics,
        "grid":       [f"{r}{c}" for r in "ABCDEFGH" for c in "12345678"],
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
        "name":       pattern["name"],
        "start_date": pattern["start_date"],
        "grid":       grid,
        "stats":      build_stats(pattern, progress),
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

    progress = load_progress(quilt_id)
    progress.setdefault(block_id, {}).setdefault(frag_id, {})
    progress[block_id][frag_id][field] = value
    if field == "assembled" and value:
        progress[block_id][frag_id]["cut"] = True
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
    root = Path(__file__).parent.parent
    files = sorted(p.name for p in root.glob("*.xlsx"))
    return jsonify(files)


@app.route("/api/excel/<filename>")
def api_excel_download(filename):
    if "/" in filename or "\\" in filename or not filename.endswith(".xlsx"):
        return "", 400
    path = Path(__file__).parent.parent / filename
    if not path.exists():
        return "", 404
    return send_file(path, as_attachment=True)


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
        if not (root / f"{slug}_CutGuide.xlsx").exists():
            print(f"Generating {slug}_CutGuide.xlsx ...")
            subprocess.run([sys.executable, str(root / "generate.py"), "--quilt-id", quilt_id],
                           check=False, cwd=str(root))
        if not (root / f"{slug}_Tracker.xlsx").exists():
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
