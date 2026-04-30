/* Quilt Tracker — client-side logic */

let patternData = null;
let selectedBlock = null;
let currentBlock = null;
let currentAssy = null;
let activeTab = null;      // 'cut', 'fabrics', or 'assemble'
let activeFrag = null;     // fragment id expanded in cut tab
let pieceChecks  = {};     // {block_id: {frag_id: {piece_num_str: bool}}}
let sewingChecks = {};     // {block_id: {step_index_str: bool}}
let activeQuilt  = null;   // current quilt id
let excelFiles   = [];     // cached list of xlsx files

// ── Quilt helpers ─────────────────────────────────────────────────────────

function qp() {
    return activeQuilt ? `?quilt=${encodeURIComponent(activeQuilt)}` : "";
}

function switchQuilt(quiltId) {
    activeQuilt = quiltId;
    localStorage.setItem("activeQuilt", quiltId);
    // Update selector if present
    const sel = document.getElementById("quilt-selector");
    if (sel) sel.value = quiltId;
    // Reset client state
    patternData  = null;
    selectedBlock = null;
    currentBlock  = null;
    currentAssy   = null;
    activeTab     = null;
    activeFrag    = null;
    pieceChecks   = {};
    sewingChecks  = {};
    init();
}

// ── Boot ──────────────────────────────────────────────────────────────────

async function init() {
    // Determine active quilt
    if (!activeQuilt) {
        const stored = localStorage.getItem("activeQuilt");
        const res = await fetch("/api/quilts");
        const quilts = await res.json();
        if (!quilts.length) return;
        const ids = quilts.map(q => q.id);
        activeQuilt = (stored && ids.includes(stored)) ? stored : ids[0];
        // Sync selector
        const sel = document.getElementById("quilt-selector");
        if (sel) sel.value = activeQuilt;
    }

    if (!excelFiles.length) {
        excelFiles = await fetch("/api/excel").then(r => r.json()).catch(() => []);
    }

    const [pp, sp] = await Promise.all([
        fetch("/api/piece_progress" + qp()).then(r => r.json()),
        fetch("/api/sewing_progress" + qp()).then(r => r.json()),
    ]);
    pieceChecks  = {};
    sewingChecks = {};
    for (const [bid, frags] of Object.entries(pp)) {
        pieceChecks[bid] = {};
        for (const [fid, pieces] of Object.entries(frags)) {
            pieceChecks[bid][fid] = {};
            for (const [num, val] of Object.entries(pieces)) pieceChecks[bid][fid][num] = val;
        }
    }
    for (const [bid, steps] of Object.entries(sp)) {
        sewingChecks[bid] = {};
        for (const [idx, val] of Object.entries(steps)) sewingChecks[bid][idx] = val;
    }
    await refreshPattern();
}

async function refreshPattern() {
    const res = await fetch("/api/pattern" + qp());
    patternData = await res.json();
    document.getElementById("quilt-title").textContent = patternData.name;
    document.title = "Quilt Tracker — " + patternData.name;
    renderStats(patternData.stats);
    renderGrid(patternData.grid);
    if (patternData.start_date) {
        const el = document.getElementById("start-date");
        if (el) el.textContent = "Started " + formatDate(patternData.start_date);
    }
    if (selectedBlock) await loadDetail(selectedBlock);
    else renderOverview(patternData.stats);
}

function formatDate(iso) {
    const [y, m, d] = iso.split("-");
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return `${months[+m - 1]} ${+d}, ${y}`;
}

// ── Stats bar ─────────────────────────────────────────────────────────────

function renderStats(stats) {
    document.getElementById("stat-complete").textContent  = stats.complete;
    document.getElementById("stat-progress").textContent  = stats.in_progress;
    document.getElementById("stat-remaining").textContent = stats.not_started;
    document.getElementById("stat-pct").textContent       = stats.pct_complete + "%";
}

// ── Quilt grid ────────────────────────────────────────────────────────────

function renderGrid(grid) {
    const rows = "ABCDEFGH";
    const container = document.getElementById("quilt-grid");
    container.innerHTML = "";

    const labelRow = document.createElement("div");
    labelRow.className = "quilt-labels";
    labelRow.innerHTML = "<span></span>" +
        [1,2,3,4,5,6,7,8].map(c => `<span>${c}</span>`).join("");
    container.appendChild(labelRow);

    const byId = Object.fromEntries(grid.map(b => [b.id, b]));

    const body = document.createElement("div");
    body.className = "quilt-body";

    const rowLabelsCol = document.createElement("div");
    rowLabelsCol.className = "quilt-row-labels";

    const gridArea = document.createElement("div");
    gridArea.className = "quilt-grid-area";

    for (const rowLetter of rows) {
        const label = document.createElement("div");
        label.className = "row-label";
        label.textContent = rowLetter;
        rowLabelsCol.appendChild(label);

        const rowEl = document.createElement("div");
        rowEl.className = "quilt-block-row";

        for (let col = 1; col <= 8; col++) {
            const block_id = `${rowLetter}${col}`;
            const block = byId[block_id];
            const el = document.createElement("div");
            el.className = `block ${block ? block.status : "not_started"}`;
            el.id = `block-${block_id}`;
            el.innerHTML = `
                <span class="block-id">${block_id}</span>
                <span class="block-info">${block ? block.fragments.length + "s " + block.piece_count + "p" : ""}</span>
            `;
            el.addEventListener("click", () => selectBlock(block_id));
            if (selectedBlock === block_id) el.classList.add("selected");
            rowEl.appendChild(el);
        }
        gridArea.appendChild(rowEl);
    }

    body.appendChild(rowLabelsCol);
    body.appendChild(gridArea);
    container.appendChild(body);

    gridArea.style.backgroundImage = `url("/quilts/${encodeURIComponent(activeQuilt)}/overview.jpg")`;
}

// ── Block selection ───────────────────────────────────────────────────────

async function selectBlock(block_id) {
    if (selectedBlock === block_id) {
        selectedBlock = null;
        activeTab = null;
        activeFrag = null;
        document.querySelectorAll(".block").forEach(el => el.classList.remove("selected"));
        renderOverview(patternData.stats);
        return;
    }
    selectedBlock = block_id;
    activeTab = null;
    activeFrag = null;
    document.querySelectorAll(".block").forEach(el => el.classList.remove("selected"));
    const el = document.getElementById(`block-${block_id}`);
    if (el) el.classList.add("selected");
    await loadDetail(block_id);
}

async function loadDetail(block_id) {
    const [blockRes, assyRes] = await Promise.all([
        fetch(`/api/block/${block_id}` + qp()),
        fetch(`/api/assembly/${block_id}` + qp()),
    ]);
    currentBlock = await blockRes.json();
    currentAssy = assyRes.ok ? await assyRes.json() : null;
    renderDetail(currentBlock, currentAssy);
}

// ── Detail panel ──────────────────────────────────────────────────────────

function renderDetail(block, assy) {
    const panel = document.getElementById("detail-panel");

    const statusLabel = {
        not_started: "Not Started",
        in_progress: "In Progress",
        complete:    "Complete",
    }[block.status] || block.status;

    if (!activeTab) {
        activeTab = block.fragments.some(f => f.cut) ? "assemble" : "cut";
    }

    const totalSegs  = block.fragments.length;
    const nReady     = block.fragments.filter(f => f.cut).length;
    const nAssembled = block.fragments.filter(f => f.assembled).length;

    const blockChecks = pieceChecks[block.id] || {};
    const totalPieces = (block.pieces || []).length;
    const nPiecesCut  = (block.pieces || []).filter(p => {
        const frag = block.fragments.find(f => matchesFrag(p.template, f.id));
        return frag && (blockChecks[frag.id] || {})[`${p.fabric_code}_${p.piece_num}`];
    }).length;

    panel.innerHTML = `
        <div class="block-header-row">
            <h2>Block ${block.id}</h2>
            <div class="block-summary">
                <span class="${nPiecesCut === totalPieces ? "sum-done" : "sum-pend"}">${nPiecesCut}/${totalPieces} cut</span>
                <span class="sum-sep">·</span>
                <span class="${nReady === totalSegs ? "sum-done" : "sum-pend"}">${nReady}/${totalSegs} segs ready</span>
                <span class="sum-sep">·</span>
                <span class="${nAssembled === totalSegs ? "sum-done" : "sum-pend"}">${nAssembled}/${totalSegs} assembled</span>
            </div>
        </div>
        <div class="block-status-badge badge-${block.status}">${statusLabel}</div>
        <div class="detail-tabs">
            <button class="tab-btn ${activeTab === "cut"      ? "active" : ""}" data-tab="cut"      onclick="switchTab('cut')">Segments</button>
            <button class="tab-btn ${activeTab === "fabrics"  ? "active" : ""}" data-tab="fabrics"  onclick="switchTab('fabrics')">Fabrics</button>
            <button class="tab-btn ${activeTab === "assemble" ? "active" : ""}" data-tab="assemble" onclick="switchTab('assemble')">Assemble</button>
        </div>
        <div id="tab-cut"      class="tab-content" style="display:${activeTab === "cut"      ? "block" : "none"}">${renderCutTab(block)}</div>
        <div id="tab-fabrics"  class="tab-content" style="display:${activeTab === "fabrics"  ? "block" : "none"}">${renderFabricsTab(block)}</div>
        <div id="tab-assemble" class="tab-content" style="display:${activeTab === "assemble" ? "block" : "none"}">${renderAssembleTab(block, assy)}</div>
    `;
}

function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll(".tab-btn").forEach(b =>
        b.classList.toggle("active", b.dataset.tab === tab)
    );
    document.getElementById("tab-cut").style.display      = tab === "cut"      ? "block" : "none";
    document.getElementById("tab-fabrics").style.display  = tab === "fabrics"  ? "block" : "none";
    document.getElementById("tab-assemble").style.display = tab === "assemble" ? "block" : "none";
}

// ── Overview panel ───────────────────────────────────────────────────────

function renderOverview(stats) {
    const panel = document.getElementById("detail-panel");
    const pct = stats.pct_complete;

    const xlsxHtml = excelFiles.length ? `
        <div class="excel-links">
            <h3>Spreadsheets</h3>
            ${excelFiles.map(f => `<a href="/api/excel/${encodeURIComponent(f)}" class="excel-link">${f}</a>`).join("")}
        </div>` : "";

    panel.innerHTML = `
        <h2>Quilt Overview</h2>
        <div class="overview-progress-bar">
            <div class="overview-progress-fill" style="width:${pct}%"></div>
        </div>
        <div class="overview-pct">${pct}% complete</div>
        <div class="overview-stats">
            <div class="ov-stat complete">
                <span class="ov-val">${stats.complete}</span>
                <span class="ov-label">of ${stats.total} blocks complete</span>
            </div>
            <div class="ov-stat in_progress">
                <span class="ov-val">${stats.in_progress}</span>
                <span class="ov-label">of ${stats.total} in progress</span>
            </div>
            <div class="ov-stat not_started">
                <span class="ov-val">${stats.not_started}</span>
                <span class="ov-label">of ${stats.total} not started</span>
            </div>
        </div>
        ${xlsxHtml}
        <p class="tab-hint" style="margin-top:16px">Click any block to view details. Click again to return here.</p>
    `;
}

// ── Template matching ─────────────────────────────────────────────────────

function matchesFrag(template, frag_id) {
    if (template === frag_id) return true;
    if (!template.startsWith(frag_id)) return false;
    const next = template[frag_id.length];
    return next !== undefined && /[a-z]/.test(next);
}

// ── Cut tab ───────────────────────────────────────────────────────────────

function renderCutTab(block) {
    const allCut = block.fragments.every(f => f.cut);
    const sorted = [...block.fragments].sort((a, b) => a.id.localeCompare(b.id));

    const fragsHtml = sorted.map(f => {
        const isActive = activeFrag === f.id;
        const pieceRows = isActive ? renderFragPieces(f.id) : "";
        const fragPieces = (block.pieces || []).filter(p => matchesFrag(p.template, f.id));
        const fragMap = ((pieceChecks[block.id] || {})[f.id]) || {};
        const allPiecesCut = fragPieces.length > 0 &&
            fragPieces.every(p => fragMap[`${p.fabric_code}_${p.piece_num}`]);
        const segDisabled = !f.cut && !allPiecesCut;
        return `
            <div class="fragment-row ${f.cut ? "frag-cut" : ""}">
                <label class="check-group">
                    <input type="checkbox" ${f.cut ? "checked" : ""} ${segDisabled ? "disabled" : ""}
                        onchange="toggleFragCut('${block.id}','${f.id}',this.checked)">
                </label>
                <span class="fragment-id ${isActive ? "frag-active" : ""}"
                    onclick="toggleFragDiagram('${f.id}')">${f.id}</span>
                <span class="frag-count">${f.piece_count} pc</span>
            </div>
            ${pieceRows}
        `;
    }).join("");

    const hint = allCut
        ? `<p class="tab-hint">All segments ready — switch to <a href="#" onclick="switchTab('assemble');return false">Assemble</a>.</p>`
        : "";

    return `<div class="fragment-list">${fragsHtml}</div>${hint}`;
}

// ── Fabrics tab ───────────────────────────────────────────────────────────

function renderFabricsTab(block) {
    const pieces = block.pieces || [];
    if (!pieces.length) return '<p class="tab-hint">No pieces for this block.</p>';

    const blockChecks = pieceChecks[block.id] || {};

    // Group by fabric code
    const byFabric = {};
    for (const p of pieces) {
        if (!byFabric[p.fabric_code]) byFabric[p.fabric_code] = { name: p.fabric_name, pieces: [] };
        byFabric[p.fabric_code].pieces.push(p);
    }

    const sections = Object.entries(byFabric).sort(([a], [b]) => a.localeCompare(b)).map(([code, fabric]) => {
        const total = fabric.pieces.length;
        const done = fabric.pieces.filter(p => {
            const frag = block.fragments.find(f => matchesFrag(p.template, f.id));
            return frag && (blockChecks[frag.id] || {})[`${p.fabric_code}_${p.piece_num}`];
        }).length;

        const rows = fabric.pieces.map(p => {
            const frag = block.fragments.find(f => matchesFrag(p.template, f.id));
            const fragId = frag ? frag.id : null;
            const pieceKey = `${p.fabric_code}_${p.piece_num}`;
            const checked = fragId && (blockChecks[fragId] || {})[pieceKey];
            return `
                <div class="piece-check-row">
                    <input type="checkbox" ${checked ? "checked" : ""} ${fragId ? "" : "disabled"}
                        onchange="checkPiece('${block.id}','${fragId}','${pieceKey}',this.checked)">
                    <span class="pc-tmpl">${p.template}</span>
                    <span class="pc-num">(${p.piece_num})</span>
                    <span class="pc-fabric">${code}</span>
                </div>`;
        }).join("");

        return `
            <div class="fabric-group ${done === total ? "fabric-done" : ""}">
                <div class="fabric-group-header">
                    <span class="fabric-code">${code}</span>
                    <span class="fabric-name">${fabric.name}</span>
                    <span class="fabric-tally">${done}/${total}</span>
                </div>
                <div class="frag-piece-list">${rows}</div>
            </div>`;
    }).join("");

    return `<div class="fabric-list">${sections}</div>`;
}

function renderFragPieces(frag_id) {
    const fragPieces = (currentBlock.pieces || [])
        .filter(p => matchesFrag(p.template, frag_id))
        .sort((a, b) => a.piece_num - b.piece_num);
    if (!fragPieces.length) return "";

    const blockChecks = pieceChecks[currentBlock.id] || {};
    const fragMap     = blockChecks[frag_id] || {};

    const rows = fragPieces.map((p, i) => {
        const pieceKey = `${p.fabric_code}_${p.piece_num}`;
        const checked = fragMap[pieceKey] || false;
        return `
            <div class="piece-check-row">
                <input type="checkbox" ${checked ? "checked" : ""}
                    onchange="checkPiece('${currentBlock.id}','${frag_id}','${pieceKey}',this.checked)">
                <span class="pc-tmpl">${p.template}</span>
                <span class="pc-num">(${i + 1})</span>
                <span class="pc-fabric">${p.fabric_code}</span>
            </div>`;
    }).join("");

    return `<div class="frag-piece-list">${rows}</div>`;
}

function toggleFragDiagram(frag_id) {
    activeFrag = activeFrag === frag_id ? null : frag_id;
    document.getElementById("tab-cut").innerHTML = renderCutTab(currentBlock);
}

async function toggleFragCut(block_id, frag_id, checked) {
    await updateProgress(block_id, frag_id, "cut", checked);
    await loadDetail(block_id);
}

async function checkPiece(block_id, frag_id, piece_num, checked) {
    if (!pieceChecks[block_id]) pieceChecks[block_id] = {};
    if (!pieceChecks[block_id][frag_id]) pieceChecks[block_id][frag_id] = {};
    pieceChecks[block_id][frag_id][String(piece_num)] = checked;

    fetch("/api/piece_progress" + qp(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id, frag_id, piece_num, checked }),
    });

    if (activeTab === "fabrics") {
        document.getElementById("tab-fabrics").innerHTML = renderFabricsTab(currentBlock);
    } else {
        document.getElementById("tab-cut").innerHTML = renderCutTab(currentBlock);
    }
    refreshBlockSummary(currentBlock);
}

function refreshBlockSummary(block) {
    const blockChecks = pieceChecks[block.id] || {};
    const totalPieces = (block.pieces || []).length;
    const nPiecesCut  = (block.pieces || []).filter(p => {
        const frag = block.fragments.find(f => matchesFrag(p.template, f.id));
        return frag && (blockChecks[frag.id] || {})[`${p.fabric_code}_${p.piece_num}`];
    }).length;
    const totalSegs   = block.fragments.length;
    const nReady      = block.fragments.filter(f => f.cut).length;
    const nAssembled  = block.fragments.filter(f => f.assembled).length;

    const s = document.querySelector(".block-summary");
    if (!s) return;
    s.innerHTML = `
        <span class="${nPiecesCut === totalPieces ? "sum-done" : "sum-pend"}">${nPiecesCut}/${totalPieces} cut</span>
        <span class="sum-sep">·</span>
        <span class="${nReady === totalSegs ? "sum-done" : "sum-pend"}">${nReady}/${totalSegs} segs ready</span>
        <span class="sum-sep">·</span>
        <span class="${nAssembled === totalSegs ? "sum-done" : "sum-pend"}">${nAssembled}/${totalSegs} assembled</span>
    `;
}

// ── Assemble tab ──────────────────────────────────────────────────────────

function renderAssembleTab(block, assy) {
    const allCut = block.fragments.every(f => f.cut);
    if (!allCut) {
        return `<p class="tab-hint">Mark all segments as ready in the Segments tab first.</p>`;
    }

    if (!assy || !assy.sewing_sequence || !assy.sewing_sequence.length) {
        const f = block.fragments[0];
        return `
            <div class="sewing-checklist">
                <div class="sewing-check-row ${f.assembled ? "step-done" : ""}">
                    <input type="checkbox" ${f.assembled ? "checked" : ""}
                        onchange="updateProgress('${block.id}','${f.id}','assembled',this.checked)">
                    <span class="sc-num">1</span>
                    <span class="sc-text">Block assembled</span>
                </div>
            </div>`;
    }

    return renderAssyDiagram(assy, block);
}

function renderAssyDiagram(assy, block) {
    const blockChecks = sewingChecks[block.id] || {};
    const steps = assy.sewing_sequence;
    const total = steps.length;
    const nDone = steps.filter((_, i) => blockChecks[String(i)]).length;

    const [l, t, r, b] = assy.bbox;
    const highlight = `<rect x="${l}%" y="${t}%" width="${r - l}%" height="${b - t}%"
        fill="rgba(233,69,96,0.06)" stroke="#e94560" stroke-width="2" stroke-dasharray="6 3"/>`;

    const diagramHtml = `
        <div class="assy-img-wrap">
            <img src="/quilts/${encodeURIComponent(activeQuilt)}/assy/${assy.image}" alt="Assembly diagram">
            <svg xmlns="http://www.w3.org/2000/svg">${highlight}</svg>
        </div>`;

    const stepRows = steps.map((s, i) => {
        const done = !!blockChecks[String(i)];
        return `
            <div class="sewing-check-row ${done ? "step-done" : ""}">
                <input type="checkbox" ${done ? "checked" : ""}
                    onchange="checkSewingStep('${block.id}',${i},this.checked)">
                <span class="sc-num">${i + 1}</span>
                <span class="sc-text">${s}</span>
            </div>`;
    }).join("");

    const progress = nDone === total
        ? `<p class="tab-hint" style="color:#4caf50">All ${total} steps complete!</p>`
        : `<p class="tab-hint">${nDone}/${total} sewing steps done</p>`;

    return `
        <div class="assy-diagram">
            ${diagramHtml}
            <div class="sewing-checklist">
                <h3>Sewing Steps</h3>
                ${stepRows}
            </div>
            ${progress}
        </div>`;
}

async function checkSewingStep(block_id, step_index, checked) {
    if (!sewingChecks[block_id]) sewingChecks[block_id] = {};
    sewingChecks[block_id][String(step_index)] = checked;

    fetch("/api/sewing_progress" + qp(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id, step_index, checked }),
    });

    const steps = currentAssy && currentAssy.sewing_sequence ? currentAssy.sewing_sequence : [];
    const allDone = steps.length > 0 && steps.every((_, i) => sewingChecks[block_id][String(i)]);
    if (allDone) {
        for (const f of currentBlock.fragments) {
            await updateProgress(block_id, f.id, "assembled", true);
        }
        await loadDetail(block_id);
    } else {
        document.getElementById("tab-assemble").innerHTML = renderAssembleTab(currentBlock, currentAssy);
    }
}

// ── Progress update ───────────────────────────────────────────────────────

async function updateProgress(block_id, fragment_id, field, value) {
    const res = await fetch("/api/progress" + qp(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id, fragment_id, field, value }),
    });
    const data = await res.json();

    if (patternData) patternData.stats = data.stats;
    const el = document.getElementById(`block-${block_id}`);
    if (el) el.className = `block ${data.status}${selectedBlock === block_id ? " selected" : ""}`;
    renderStats(data.stats);

    const badge = document.querySelector(".block-status-badge");
    if (badge) {
        const label = { not_started: "Not Started", in_progress: "In Progress", complete: "Complete" }[data.status];
        badge.className = `block-status-badge badge-${data.status}`;
        badge.textContent = label;
    }
}

// ── Reset ─────────────────────────────────────────────────────────────────

async function resetProgress() {
    if (!confirm("Reset all progress? This cannot be undone.")) return;
    const res = await fetch("/api/progress/reset" + qp(), { method: "POST" });
    const data = await res.json();
    renderStats(data.stats);
    selectedBlock = null;
    currentBlock = null;
    currentAssy = null;
    activeTab = null;
    activeFrag = null;
    pieceChecks  = {};
    sewingChecks = {};
    renderOverview(data.stats);
    await refreshPattern();
}

// ── Start ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", init);
