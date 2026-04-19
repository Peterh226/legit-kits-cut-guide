/* Quilt Tracker — client-side logic */

let patternData = null;
let selectedBlock = null;
let currentBlock = null;
let currentAssy = null;

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
    const [blockRes, assyRes] = await Promise.all([
        fetch(`/api/block/${block_id}`),
        fetch(`/api/assembly/${block_id}`),
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
        complete: "Complete",
    }[block.status] || block.status;

    const fragsHtml = block.fragments.map(f => `
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

    const assyHtml = assy ? renderAssyDiagram(assy, block.fragments) : "";

    panel.innerHTML = `
        <h2>Block ${block.id}</h2>
        <div class="block-status-badge badge-${block.status}">${statusLabel}</div>
        ${assyHtml}
        <div class="fragment-list">${fragsHtml}</div>
        ${piecesHtml}
    `;
}

function renderAssyDiagram(assy, fragments) {
    const fragState = Object.fromEntries(fragments.map(f => [f.id, f]));
    const [l, t, r, b] = assy.bbox;

    const highlight = `<rect x="${l}%" y="${t}%" width="${r - l}%" height="${b - t}%"
        fill="rgba(233,69,96,0.08)" stroke="#e94560" stroke-width="2" stroke-dasharray="6 3"/>`;

    const circles = assy.circles.map(c => {
        const state = fragState[c.fragment_id] || {};
        const color = state.assembled ? "#4caf50" : state.cut ? "#ff9800" : "#2196f3";
        const label = c.fragment_id.replace(/^[A-H]\d/, "");  // strip block prefix, show just "a","b"...
        return `
            <circle cx="${c.cx}%" cy="${c.cy}%" r="14"
                fill="${color}" fill-opacity="0.88" stroke="#fff" stroke-width="1.5"
                style="cursor:pointer" onclick="clickCircle('${c.fragment_id}')"/>
            <text x="${c.cx}%" y="${c.cy}%" text-anchor="middle" dominant-baseline="middle"
                font-size="10" font-weight="bold" fill="#fff" pointer-events="none"
                font-family="Arial">${label || c.fragment_id}</text>`;
    }).join("");

    let stepsHtml = "";
    if (assy.sewing_sequence && assy.sewing_sequence.length) {
        const steps = assy.sewing_sequence.map((s, i) =>
            `<div class="sewing-step"><span class="step-num">${i + 1}</span>${s}</div>`
        ).join("");
        stepsHtml = `<div class="sewing-steps"><h3>Sewing Order</h3>${steps}</div>`;
    }

    return `
        <div class="assy-diagram">
            <div class="assy-img-wrap">
                <img src="/static/assy/${assy.image}" alt="Assembly diagram for block">
                <svg xmlns="http://www.w3.org/2000/svg">
                    ${highlight}
                    ${circles}
                </svg>
            </div>
            ${stepsHtml}
        </div>
    `;
}

async function clickCircle(frag_id) {
    if (!currentBlock) return;
    const state = currentBlock.fragments.find(f => f.id === frag_id);
    if (!state) return;
    await updateProgress(currentBlock.id, frag_id, "assembled", !state.assembled);
    await loadDetail(currentBlock.id);
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

// ── Reset ─────────────────────────────────────────────────────────────────

async function resetProgress() {
    if (!confirm("Reset all progress? This cannot be undone.")) return;
    const res = await fetch("/api/progress/reset", { method: "POST" });
    const data = await res.json();
    renderStats(data.stats);
    selectedBlock = null;
    currentBlock = null;
    currentAssy = null;
    document.getElementById("detail-panel").innerHTML =
        "<h2>Block Detail</h2><p class='detail-empty'>Click a block on the quilt to see its pieces and track progress.</p>";
    await refreshPattern();
}

// ── Start ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", init);
