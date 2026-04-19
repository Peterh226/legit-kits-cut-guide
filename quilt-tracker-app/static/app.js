/* Quilt Tracker — client-side logic */

let patternData = null;
let selectedBlock = null;

// ── Boot ──────────────────────────────────────────────────────────────────

async function init() {
    await refreshPattern();
}

async function refreshPattern() {
    const res = await fetch("/api/pattern");
    patternData = await res.json();
    renderStats(patternData.stats);
    renderGrid(patternData.grid);
    if (selectedBlock) {
        await loadDetail(selectedBlock);
    }
}

// ── Stats bar ─────────────────────────────────────────────────────────────

function renderStats(stats) {
    document.getElementById("stat-complete").textContent    = stats.complete;
    document.getElementById("stat-progress").textContent    = stats.in_progress;
    document.getElementById("stat-remaining").textContent   = stats.not_started;
    document.getElementById("stat-pct").textContent         = stats.pct_complete + "%";
}

// ── Quilt grid ────────────────────────────────────────────────────────────

function renderGrid(grid) {
    const rows = "ABCDEFGH";
    const container = document.getElementById("quilt-grid");
    container.innerHTML = "";

    // Column labels
    const labelRow = document.createElement("div");
    labelRow.className = "quilt-labels";
    labelRow.innerHTML = "<span></span>" +
        [1,2,3,4,5,6,7,8].map(c => `<span>${c}</span>`).join("");
    container.appendChild(labelRow);

    // Block rows
    const byId = Object.fromEntries(grid.map(b => [b.id, b]));

    for (const rowLetter of rows) {
        const rowEl = document.createElement("div");
        rowEl.className = "quilt-row";

        const label = document.createElement("div");
        label.className = "row-label";
        label.textContent = rowLetter;
        rowEl.appendChild(label);

        for (let col = 1; col <= 8; col++) {
            const block_id = `${rowLetter}${col}`;
            const block = byId[block_id];
            const el = document.createElement("div");
            el.className = `block ${block ? block.status : "not_started"}`;
            el.id = `block-${block_id}`;
            el.innerHTML = `
                <span class="block-id">${block_id}</span>
                <span class="block-info">${block ? block.piece_count + "p" : ""}</span>
            `;
            el.addEventListener("click", () => selectBlock(block_id));
            if (selectedBlock === block_id) el.classList.add("selected");
            rowEl.appendChild(el);
        }
        container.appendChild(rowEl);
    }
}

// ── Block selection ───────────────────────────────────────────────────────

async function selectBlock(block_id) {
    selectedBlock = block_id;
    // Highlight
    document.querySelectorAll(".block").forEach(el => el.classList.remove("selected"));
    const el = document.getElementById(`block-${block_id}`);
    if (el) el.classList.add("selected");
    await loadDetail(block_id);
}

async function loadDetail(block_id) {
    const res = await fetch(`/api/block/${block_id}`);
    const block = await res.json();
    renderDetail(block);
}

// ── Detail panel ──────────────────────────────────────────────────────────

function renderDetail(block) {
    const panel = document.getElementById("detail-panel");

    const statusLabel = {
        not_started: "Not Started",
        in_progress: "In Progress",
        complete: "Complete",
    }[block.status] || block.status;

    let fragsHtml = block.fragments.map(f => `
        <div class="fragment-row">
            <span class="fragment-id">${f.id}</span>
            <label class="check-group">
                <input type="checkbox" ${f.cut ? "checked" : ""}
                    onchange="updateProgress('${block.id}','${f.id}','cut',this.checked)">
                <span>Cut</span>
            </label>
            <label class="check-group">
                <input type="checkbox" ${f.assembled ? "checked" : ""}
                    onchange="updateProgress('${block.id}','${f.id}','assembled',this.checked)">
                <span>Assembled</span>
            </label>
        </div>
    `).join("");

    let piecesHtml = "";
    if (block.pieces && block.pieces.length > 0) {
        const rows = block.pieces.map(p => `
            <div class="piece-row">
                <span class="p-num">${p.piece_num}</span>
                <span class="p-code">${p.fabric_code} ${p.fabric_name}</span>
                <span class="p-tmpl">${p.template}</span>
                <span class="p-qty">×${p.quantity}</span>
            </div>
        `).join("");
        piecesHtml = `
            <div class="pieces-section">
                <h3>Pieces (${block.pieces.length})</h3>
                <div class="piece-row" style="font-size:0.7rem;color:#666;border-bottom:1px solid #333">
                    <span>#</span><span>Fabric</span><span>Template</span><span>Qty</span>
                </div>
                ${rows}
            </div>
        `;
    }

    panel.innerHTML = `
        <h2>Block ${block.id}</h2>
        <div class="block-status-badge badge-${block.status}">${statusLabel}</div>
        <div class="fragment-list">${fragsHtml}</div>
        ${piecesHtml}
    `;
}

// ── Progress update ───────────────────────────────────────────────────────

async function updateProgress(block_id, fragment_id, field, value) {
    const res = await fetch("/api/progress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_id, fragment_id, field, value }),
    });
    const data = await res.json();

    // Update block color in grid
    const el = document.getElementById(`block-${block_id}`);
    if (el) {
        el.className = `block ${data.status}${selectedBlock === block_id ? " selected" : ""}`;
    }

    // Update stats
    renderStats(data.stats);

    // Update status badge in detail panel
    const badge = document.querySelector(".block-status-badge");
    if (badge) {
        const label = { not_started: "Not Started", in_progress: "In Progress", complete: "Complete" }[data.status];
        badge.className = `block-status-badge badge-${data.status}`;
        badge.textContent = label;
    }

    // If assembled was checked, also check cut checkbox visually
    if (field === "assembled" && value) {
        const cutCheckbox = document.querySelector(
            `.fragment-row input[onchange*="'${fragment_id}','cut'"]`
        );
        if (cutCheckbox) cutCheckbox.checked = true;
    }
}

// ── Start ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", init);
