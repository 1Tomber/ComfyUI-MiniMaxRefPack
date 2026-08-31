/**
 * MiniMax References Manager — native widgets (openrouter_api_key, model) stay on top,
 * untouched; everything custom lives in one DOM block BELOW them. Per the visual
 * spec the DOM/canvas split is VISIBLE: no wrapping panel, buttons and prompt sit
 * directly on ComfyUI's grey node body, and the canvas is a BLACK slab that
 * announces "this is the reference area":
 *   [⬆ Image] [⬆ Video] [⬆ Audio] [Save config] [Load config] · · · · · · · [⚙]
 *   <canvas> — black; Images (N/9) / Videos (N/3) / Audio (N/3), three fixed
 *              sections separated by drawn divider lines, a "+" add slot per row
 *   Prompt — one large plain textarea (bound to the hidden `direction` widget)
 * The ⚙ opens a modal for the `system_prompt` widget — never drawn on the node body,
 * only editable behind that modal (see openSystemPromptModal).
 *
 * RESIZABLE, WITH DERIVED HEIGHT. This node was fixed at 1340xN, per spec: "I would
 * like this node to have a fixed size that cannot be moved". That pin is gone, because
 * it was unshippable: 1340 CSS px is wider than plenty of ComfyUI viewports (measured
 * 1036 on the machine this was rewritten for), so the node could not be seen whole at
 * 1:1 zoom no matter what the user did. Tiles WRAP now.
 *
 * Three sizes, and only two of them are the user's:
 *   width   — dragged, floored by minNodeWidth() at whatever the BUTTON ROW needs
 *             (measured, because it depends on the font and on which buttons are shown)
 *   media   — derived from the reference counts and the width; adding a reference that
 *             starts a new line makes the NODE taller, it never steals from the prompt
 *   prompt  — dragged, stored per node in the references_json envelope so it survives a
 *             reload without costing a widget slot
 *
 * A height drag is therefore read as "make the prompt this much taller"
 * (absorbPointerIntoPrompt), because everything else in the block has the height it
 * needs rather than the height someone picked. It reads the POINTER, not the height the
 * frontend passes - see that function for why the passed height cannot carry intent.
 *
 * WHAT SURVIVED THE REWRITE, and matters more than the pin did: there is still NO
 * SCROLLING. Nothing is clipped, nothing hides behind a scrollbar, the section just gets
 * taller. So draw() is still one pass with no scroll offset threaded through hit-testing,
 * no clip region to keep in step with the shared <video> overlay, and paint still never
 * calls setSize — relayout() does that, from the places that change the content.
 *
 * RENDERING: the reference rows are a single <canvas>, not per-cell DOM — the LTX
 * Director hybrid (ltx_director.js draws its timeline onto one canvas at :2703 but
 * keeps buttons and the prompt as real DOM). Per-cell DOM kept producing
 * overlap/leakage bugs on this frontend; one drawn surface has nothing to overlap.
 * Everything inside the rows — headers, thumbnails, tag badges, delete, the
 * soundtrack toggle, the add slots, play glyphs, selection — is painted by draw(),
 * the single entry point. No requestAnimationFrame loop: scheduleDraw() coalesces
 * change events (refs, selection, thumb load, probe answer, preview state, the
 * canvas's one real mount via a ResizeObserver) into at most one paint per frame,
 * and draw() never calls setSize/computeSize, so paint can't recurse into layout.
 *
 * PLAYABLE PREVIEWS: click the drawn ▶ on a video or audio tile to play it, click
 * again (or click the playing video) to stop. ONE shared <video> overlay and ONE
 * shared <audio> element per node — never per-cell DOM. The overlay is a child of
 * the block, so ComfyUI's zoom transform applies to it automatically; positioning
 * is plain CSS-px math off the tile rect. Clicking a tile anywhere OUTSIDE the ▶
 * selects it, exactly as before.
 *
 * HIDING `references_json`, `direction` and `system_prompt`: mirrors WhatDreamsCost
 * multi_image_loader.js's hide-widget pattern (:122-146) — a plain `w.hidden = true`
 * gets silently overwritten on V3 (vueNodesMode), which re-derives widget visibility
 * from `hidden`/`type` on every reactive redraw, and a widget left with its native
 * computeSize reserves real layout height: three stacked, that read as a thin bar
 * overlapping our controls. The fix: Object.defineProperty-lock `hidden`/`type` as
 * always-on getters V3's own writes can't override, set computeSize = [0,0]
 * unconditionally, and poll every 50ms for 1s for `w.element` — V3 creates the DOM
 * element backing a multiline STRING widget ASYNCHRONOUSLY, so a one-shot
 * display:none misses it. Every property hideWidget() touches is try/catch-wrapped:
 * a prior version of this file assigned `domWidget.node = node` directly, which is a
 * getter-only accessor on the V3 frontend's BaseWidget and threw a TypeError that
 * aborted loading ANY workflow containing this node — fatal, not cosmetic. That
 * assignment is gone (the node is captured via closure instead); hideWidget()'s own
 * property writes are defensive against the same class of bug.
 *
 * Each video tile carries a soundtrack toggle (♪, bottom-left) — it's the only thing
 * that routes that video's audio to its index-matched video_audio_N output, so it's
 * real functionality, not decoration. Gated on GET /minimax_refpack/probe?file=
 * (media.py's has_audio): dim/pending until the probe answers, permanently disabled
 * for a clip with no audio track. See probeHasAudio()/syncProbes() and drawTile().
 *
 * NOT SURFACED ON THE NODE BODY: the system prompt itself — lives only behind the ⚙
 * modal (openSystemPromptModal), never drawn on the body.
 *
 * CROP + TRIM (openEditModal): one modal edits both. Entry points: the scissors chip
 * in a tile's TOP-LEFT corner (the only free corner — delete owns top-right, ♪ sits
 * bottom-left above the badge, ▶ is centred) and a double-click anywhere on the tile.
 * The modal shows the REAL media via /view (fileUrl) — an <img> for stills, a <video>
 * for clips — with a draggable fraction-space crop rect + corner handles and aspect
 * presets; video/audio get in/out trim handles on a bar plus 2dp second fields. Each
 * row ends in a right-aligned Clear button ("Clear crop" / "Clear trim") that resets
 * that edit — dimmed when there is nothing to clear, so the modal states at a glance
 * whether the reference carries an edit; clearing the crop also releases the aspect
 * lock and unhighlights every preset. No preset is highlighted on open — the highlight
 * means the user chose a lock, not the free default. Save
 * writes crop ([x,y,w,h] fractions) / trim ([start,end] seconds) onto the reference —
 * refs.py validates and media.py applies them — and drops the file's thumbCache entry
 * so the tile redraws from the thumb route with the edit baked in. The scissors chip
 * inverts to a light chip while an edit is set (same signalling as ♪), the badge grows
 * a third line ("2.00-6.50s · cropped"), and the click-to-play preview plays only the
 * trimmed span (seek to the in-point, timeupdate stops it at the out-point). All of it
 * keeps the module invariants: no per-cell DOM, one shared <video>/<audio> per node,
 * scheduleDraw coalescing, no requestAnimationFrame loop.
 */

import { app } from "../../scripts/app.js";

const NODE_NAME = "MiniMaxH3ReferencePack";

// ---------------------------------------------------------------------------
// 0.3.1 -> 0.3.2 widget migration.
//
// `use_openrouter` was a BOOLEAN. `prompt_provider` is a combo in the SAME slot,
// because appending it instead would have re-pointed every widget after it - the
// exact class of bug 0.3.1 shipped and fixed. litegraph restores widgets_values
// positionally and does not type-check, so a workflow saved at 0.3.1 hands the
// combo a raw `true`, which then fails validation at queue time.
//
// configure() applies widgets_values BEFORE calling onConfigure, so by the time we
// run the wrong value is already sitting on the widget and this is a repair rather
// than an interception. That is fine and is why it is written this way: one place,
// after the fact, no hooking of litegraph internals.
//
// The block between the two MMRP-MIGRATE markers is extracted and executed by
// tests/test_migration.py under node. Keep the markers, and keep this function
// free of imports and DOM access so it stays runnable in isolation.
// >>> MMRP-MIGRATE
const PROVIDER_VALUES = ["openrouter", "local", "none"];

// >>> MMRP-MODAL
// Dismiss on a backdrop click - but only when the PRESS started on the backdrop too.
//
// A `click` fires on the nearest common ancestor of its mousedown and mouseup targets, so
// the modal is safe from the click that opened it: that press was on the canvas. It is not
// safe from the SECOND press of a double-click. The editor opens on mousedown and appends
// this overlay synchronously, so the second press and release both land on the backdrop,
// and the modal a double-click just opened closes again on its own.
//
// The same rule fixes a second annoyance nobody had filed: selecting text in the prompt
// box and releasing the button outside the panel counted as a backdrop click and threw the
// edit away.
//
// Written as a decision function so it can be tested without a DOM.
function shouldDismissOnBackdrop(targetIsBackdrop, pressStartedOnBackdrop) {
    return targetIsBackdrop === true && pressStartedOnBackdrop === true;
}

function dismissOnBackdrop(overlay) {
    let pressStartedOnBackdrop = false;
    overlay.onmousedown = (e) => {
        pressStartedOnBackdrop = e.target === overlay;
    };
    overlay.onclick = (e) => {
        const dismiss = shouldDismissOnBackdrop(e.target === overlay, pressStartedOnBackdrop);
        pressStartedOnBackdrop = false;
        if (dismiss) overlay.remove();
    };
}
// <<< MMRP-MODAL

// >>> MMRP-UNDO
// Write into the prompt textarea without throwing its undo history away.
//
// Assigning `.value` resets the undo stack in Chromium and Firefox. So typing a sentence,
// inserting a tag and pressing Ctrl+Z did nothing at all - not "undid the insert", nothing
// - and every retag pass (reorder, delete, soundtrack toggle) did the same thing to
// whatever the user had been writing.
//
// execCommand("insertText") is deprecated and remains the only way to make a programmatic
// edit the browser's own undo can see. It needs focus and a selection, so both are set
// here; `restoreFocus` puts the caret back where it was, because a retag runs while the
// user is on the CANVAS and stealing focus would be a worse bug than the one being fixed.
//
// Returns whether it worked. It fires `input` on success, so a caller that falls back to
// assignment has to mirror the value itself.
function writeTextPreservingUndo(el, start, end, text, restoreFocus) {
    if (!el || typeof document === "undefined" || typeof document.execCommand !== "function") {
        return false;
    }
    const previous = document.activeElement;
    let ok = false;
    try {
        el.focus();
        el.setSelectionRange(start, end);
        ok = document.execCommand("insertText", false, text) !== false;
    } catch (e) {
        ok = false;   // a frontend that has removed it, or a detached element
    }
    if (ok && restoreFocus && previous && previous !== el && typeof previous.focus === "function") {
        try { previous.focus(); } catch (e) { /* the old element left the DOM */ }
    }
    return ok;
}
// <<< MMRP-UNDO

function migrateProviderValue(raw) {
    // Mirrors endpoint.normalize_provider in Python. Both exist on purpose: this one
    // fixes the graph the user is looking at, the Python one catches an API client
    // that never loaded a browser. Total by design - an unknown value resolves to the
    // default rather than throwing, because a broken workflow that will not queue is
    // worse than a workflow that quietly writes its prompt the old way.
    if (raw === true) return "openrouter";
    if (raw === false) return "none";
    const text = String(raw ?? "").trim().toLowerCase();
    if (PROVIDER_VALUES.includes(text)) return text;
    if (text === "true") return "openrouter";
    if (text === "false") return "none";
    return "openrouter";
}

function migrateProviderWidget(widget) {
    // Returns true when it changed something, so the caller can log it once.
    if (!widget) return false;
    const migrated = migrateProviderValue(widget.value);
    if (widget.value === migrated) return false;
    widget.value = migrated;
    return true;
}

// --- 0.3.3 reorder -------------------------------------------------------------
//
// widgets_values is a positional array, so the declaration order in nodes.py is a wire
// format. 0.3.3 regrouped the widgets by decision flow, which means every array saved
// before it now decodes into the wrong widgets - width into prompt_provider, and so on.
//
// Detection is by VALUE SHAPE rather than by properties.ver. A graph can reach us
// without a ver (hand-edited, older frontend, copied node), and being wrong here is
// worse than the bug it fixes: it would silently scramble a workflow that was fine.
// Length plus the type at the provider slot identifies each layout unambiguously.
const ORDER_0_3_1 = [
    "direction", "openrouter_api_key", "openrouter_model", "references_json",
    "system_prompt", "width", "height", "length_seconds", "prompt_provider",
    "reasoning_effort", "job_type", "max_reference_edge",
];
const ORDER_0_3_2 = ORDER_0_3_1.concat(["api_base", "local_model_slug"]);
const ORDER_0_3_3 = [
    "direction", "references_json", "system_prompt", "prompt_provider",
    "openrouter_api_key", "openrouter_model", "reasoning_effort", "api_base",
    "local_model_slug", "job_type", "width", "height", "length_seconds",
    "max_reference_edge",
];

// Two branches appended widgets, so the current layout is the 0.3.3 layout plus BOTH
// tails: system_prompt_replacement (a second system prompt, one per register) and the
// four local_* settings.
//
// Appended rather than filed where each belongs by meaning - the prompt beside its pair,
// the local knobs beside api_base - because widgets_values is positional: inserting
// mid-list re-points every widget after it in every saved workflow, which is the exact
// failure 0.3.3 caused and needed remapWidgetValues to dig out of. The UI groups the
// local ones by NAME instead (see MMRP-VISIBILITY), so the canvas still reads by decision
// flow even though the wire format cannot.
//
// The useful consequence: the current layout EXTENDS 0.3.3, so a 0.3.3 array restores
// correctly by position with no remapping at all.
const ORDER_CURRENT = ORDER_0_3_3.concat([
    "system_prompt_replacement",
    "local_ttl", "local_server", "local_send_reasoning", "local_extra_body",
]);

// True when restoring this layout POSITIONALLY already lands every value in the right
// widget - it is the current order, or an earlier order the current one merely extends.
// Widgets past the end of a shorter array simply keep their defaults. Appending is the
// only edit with that property, which is exactly why it is the safe one.
function isPrefixOfCurrent(order) {
    return Array.isArray(order) && order.every((name, i) => ORDER_CURRENT[i] === name);
}

function detectLayout(values) {
    if (!Array.isArray(values)) return null;
    // Slot 3 holds a provider string in 0.3.3 and everything after it; the LENGTH is what
    // separates those, since appending is the only change that has happened since.
    if (PROVIDER_VALUES.includes(String(values[3] ?? "").trim().toLowerCase())) {
        return values.length > ORDER_0_3_3.length ? ORDER_CURRENT : ORDER_0_3_3;
    }
    // 0.3.1: use_openrouter was a boolean at slot 8. 0.3.2: a provider string there.
    const slot8 = values[8];
    if (typeof slot8 === "boolean") return ORDER_0_3_1;
    if (PROVIDER_VALUES.includes(String(slot8 ?? "").trim().toLowerCase())) {
        return values.length > 12 ? ORDER_0_3_2 : ORDER_0_3_1;
    }
    return null;   // unrecognised: leave it alone rather than guess
}

function remapWidgetValues(values) {
    // -> {name: value} for whatever layout this array was written in, or null if the
    // array is not one we recognise. Names absent from the old layout simply do not
    // appear, so the caller leaves those widgets at their defaults.
    const order = detectLayout(values);
    if (!order) return null;
    const out = {};
    for (let i = 0; i < order.length && i < values.length; i++) {
        out[order[i]] = values[i];
    }
    // The old `model` slot is this pack's openrouter_model; the old generic override is
    // the local slug. Both renames happened in 0.3.3 alongside the reorder.
    if (out.model !== undefined && out.openrouter_model === undefined) {
        out.openrouter_model = out.model;
    }
    if (out.model_override !== undefined && out.local_model_slug === undefined) {
        out.local_model_slug = out.model_override;
    }
    if (out.prompt_provider !== undefined) {
        out.prompt_provider = migrateProviderValue(out.prompt_provider);
    }
    return out;
}

// Everything a migrating load should assign: the saved values re-placed by name, PLUS
// every other current widget put back to its default.
//
// The second half is the part that was missing. configure() applies widgets_values
// positionally before any of this runs, so re-placing by name only repairs the slots the
// old layout actually names - every widget added since keeps whatever landed in it.
// Loading a 0.3.1 graph left api_base holding 8 (that is length_seconds) and
// local_model_slug holding true (the old use_openrouter boolean). Both then serialise
// into every later save, and api_base is read verbatim the moment the provider is
// switched to local, so a graph that never had one ends up pointing at "8".
//
// A widget the saved graph could not have had a value for belongs at its default, not at
// its neighbour's. Returns null when the array is not a layout we recognise, which means
// "leave everything alone" rather than "reset everything".
function migrationPlan(values, currentNames, defaults) {
    const byName = remapWidgetValues(values);
    if (!byName) return null;
    const plan = { ...byName };
    for (const name of currentNames || []) {
        if (Object.prototype.hasOwnProperty.call(plan, name)) continue;
        if (!Object.prototype.hasOwnProperty.call(defaults || {}, name)) continue;
        plan[name] = defaults[name];
    }
    return plan;
}
// <<< MMRP-MIGRATE
const KINDS = ["image", "video", "audio"];
const CAPS = { image: 9, video: 3, audio: 3 };
const SECTION_LABEL = { image: "Images", video: "Videos", audio: "Audio" };

// LTX Director's flat neutral palette (WhatDreamsCost js/ltx_director.js), counted
// out of that file, not invented here. The canvas reads these directly; refpack.css
// carries the DOM half (buttons, prompt box, modal). The wells are nudged up from
// the reference's #111/#121212: those values assume a #1e1e1e panel behind them,
// and this canvas is BLACK — at #111 a tile well is 17/255 off its background,
// structurally present but invisible (same class of contrast bug as the prompt box).
const C = {
    well: "#1a1a1a",
    wellDeep: "#141414",
    surface: "#2a2a2a",
    raised: "#333",
    border: "#444",
    text: "#e0e0e0",
    textMuted: "#aaa",
    textFaint: "#888",
    textDim: "#666",
    danger: "#ff4444",
};

// Canvas row geometry, all in CSS px (draw() scales the backing store by
// devicePixelRatio, so layout math never sees physical pixels). Tile size started
// as LTX Director's 75px gallery cell (multi_image_loader.js:208), scaled 1.75×
// per spec ("make them about 1.75 times the current size") — the on-tile furniture
// (badge, delete, ♪, ▶) is scaled with it so the proportions hold.
const CL = {
    x0: 10, // inner side padding of the black slab (also clears the selection stroke)
    padTop: 8,
    headerH: 15,
    headGap: 12, // 8px of real air between a section label and its strip (UI review #7)
    tile: 131, // 75 × 1.75
    gap: 6,
    stripPad: 3, // above/below tiles, room for the selection stroke
    // Generous per spec: three DISTINCT sections, divider drawn at the midpoint
    // of this gap (see draw()).
    rowGap: 18,
    // On-tile furniture, scaled with the tile.
    del: 20, // delete chip circle DIAMETER — a quiet chip, not red (UI review #5)
    sound: 28, // soundtrack toggle box
    playR: 14, // play glyph circle radius — r20 buried a third of the thumbnail
    badge1: 18, // tag badge height, one line (11px type)
    badge2: 30, // tag badge height, two lines
    badge3: 42, // tag badge height, three lines (tag + vid_audio + the crop/trim line)
    // The per-row add affordance: an icon-only square, NOT a toolbar-button clone
    // (UI review #1). Fixed placement — always where the next tile would go, with
    // a 24px gap separating it from the reference group so it doesn't read as
    // another tile; it never centres itself and only ever moves rightwards.
    addBtn: 44,
    addGap: 24,
    // The subject picker, painted INSIDE the tile: 5 across, 2 down - "none" plus 1..9.
    // 5*24 + 4*2 = 128 inside a 131px tile, so it fits with a hair to spare and needs no
    // new furniture on a tile whose four corners are already spoken for.
    subCell: 24,
    subGap: 2,
    // The persistent badge, bottom-right above the tag strip - the mirror of the music
    // toggle's corner, and the only one still free.
    subPill: 18,
};
// ---------------------------------------------------------------------------
// Layout. THIS USED TO BE FIXED and is not any more.
//
// The node was pinned at 1340×N because the longest row is nine image tiles and that
// width guaranteed they always fit, which is what let the whole file skip scroll
// machinery. The pin turned out to be unshippable: 1340 CSS px is wider than plenty of
// ComfyUI viewports (measured 1036 on the machine this was rewritten for), so the node
// could not be seen whole at 1:1 zoom no matter what the user did.
//
// So tiles WRAP instead. What is preserved is the property that actually mattered — no
// scrolling, ever. Nothing is clipped and nothing hides behind a scrollbar; the section
// simply gets taller, and so does the node. That keeps draw() a single pass with no
// scroll offset to thread through hit-testing and no clip region to keep in step with
// the shared <video> overlay.
//
// The three sizes that follow are the whole model:
//   width   — the user's, dragged, floored so the uploads row and one tile still fit
//   media   — derived from the reference counts and the width, never dragged
//   prompt  — the user's, stored per node; adding media does NOT steal from it
// ---------------------------------------------------------------------------

// How many tiles fit on one line, given the drawable width inside the slab.
//
// The add square is reserved for unconditionally, not just when a row is full. That is
// what makes `linesFor` below correct without a special case: any line holding a full
// `perRow` tiles is guaranteed to have room for the square after it, so the square never
// forces an extra line of its own and never lands off the edge at cap.
// >>> MMRP-LAYOUT
function tilesPerRow(viewW) {
    const room = viewW - CL.addGap - CL.addBtn + CL.gap;
    return Math.max(1, Math.floor(room / (CL.tile + CL.gap)));
}

function linesFor(count, perRow) {
    return Math.max(1, Math.ceil(count / perRow));  // 1 even when empty: the add square
}

// Where every section starts, how tall each is, and how tall the slab ends up. Computed
// per paint from the live counts — the thing the old CANVAS_ROWS constant could not do.
function computeCanvasRows(refs, viewW) {
    const perRow = tilesPerRow(viewW);
    const rows = [];
    let y = CL.padTop;
    for (const kind of KINDS) {
        const lines = linesFor(refs[`${kind}s`].length, perRow);
        const stripH = CL.stripPad * 2 + lines * CL.tile + (lines - 1) * CL.gap;
        rows.push({ kind, y, stripY: y + CL.headerH + CL.headGap, perRow, lines, stripH });
        y += CL.headerH + CL.headGap + stripH + CL.rowGap;
    }
    // The trailing rowGap doubles as the slab's bottom padding.
    return { rows, perRow, height: y };
}
// <<< MMRP-LAYOUT

const CONTENT = {
    uploadsH: 30, // .mmrp-uploads fixed height, pinned in refpack.css
    gap: 8, // .mmrp-block flex gap
    promptH: 110, // starting height of .mmrp-direction, before the user drags it
    minPromptH: 44, // about two lines — below this the box stops being writable
    // Floor for the drawable slab width: one tile plus the add square. Narrower than
    // this and a section cannot show a single reference alongside its own add button.
    minRowW: CL.tile + CL.addGap + CL.addBtn,
    // Fallback until the uploads row has been laid out and can be measured. Roughly what
    // its six buttons occupy; only ever used for the beat before the first measurement.
    fallbackUploadsW: 620,
};

// The narrowest this node may be dragged.
//
// The binding constraint is the BUTTON ROW, not the tiles: three upload buttons, two
// config buttons, Local LLM and the gear come to far more than one tile plus an add
// square. It is measured rather than hardcoded because its width depends on the font the
// user's ComfyUI is running and on which buttons are currently shown (Local LLM hides
// off `local`), and a hardcoded guess would either clip the row or refuse widths that
// were fine. scrollWidth is the row's natural width because it is `flex-wrap: nowrap`.
// The natural width of the button row: its CHILDREN laid end to end, not the row's own
// box.
//
// scrollWidth is the obvious call here and it is wrong. `.mmrp-uploads` is width:100% of
// the block, so unless the buttons actually overflow, scrollWidth reports the CONTAINER's
// width - which makes the floor equal to the current width and the node can then never be
// dragged narrower than it already is. Verified live on frontend 1.51.9: asking for 1128
// inside a 1600px block came back 1620.
//
// Buttons that are hidden (Local LLM off `local`) and the hidden file inputs measure zero
// and are skipped, so the floor tracks what is actually on screen.
function uploadsNaturalWidth(row) {
    if (!row || !row.children.length) return 0;
    const style = getComputedStyle(row);
    const gap = parseFloat(style.columnGap || style.gap) || 0;
    let total = 0;
    let shown = 0;
    for (const child of row.children) {
        const w = child.offsetWidth;
        if (!w) continue;
        total += w;
        shown += 1;
    }
    return shown ? total + gap * (shown - 1) : 0;
}

function minNodeWidth(node) {
    const row = node._mmrpBody && node._mmrpBody.uploadRow;
    const uploads = uploadsNaturalWidth(row) || CONTENT.fallbackUploadsW;
    const slab = Math.max(uploads + CL.x0 * 2, CONTENT.minRowW + CL.x0 * 2);
    return Math.ceil(slab + domWidgetMargin(node) * 2);
}

// The prompt box's height, which is the user's and is remembered. Adding a reference
// grows the NODE and leaves this untouched — a flex remainder would have shrunk the text
// box every time a tile wrapped onto a new line, which is the opposite of wanted.
function promptHeightOf(node) {
    const stored = node && node._mmrpPromptH;
    return Math.max(CONTENT.minPromptH, Number.isFinite(stored) ? stored : CONTENT.promptH);
}

// Drawable width inside the black slab, for the node's current width.
function slabViewW(node) {
    const width = Math.max((node.size && node.size[0]) || 0, minNodeWidth(node));
    return width - domWidgetMargin(node) * 2 - CL.x0 * 2;
}

// The layout this node paints at right now. Cached on the node so draw(), hit-testing
// and the size math all read one answer rather than three that can disagree.
function layoutOf(node) {
    const rows = computeCanvasRows(node._mmrpRefs || { images: [], videos: [], audios: [] },
                                   slabViewW(node));
    node._mmrpLayout = rows;
    return rows;
}

function contentHeight(node) {
    return CONTENT.uploadsH + CONTENT.gap + layoutOf(node).height
        + CONTENT.gap + promptHeightOf(node);
}

// The node's size for its current content. Width is the user's, floored; height is
// derived and is never the user's directly — dragging the bottom edge is interpreted as
// resizing the PROMPT (see installSizeGuards), which then lands back here.
function nodeSize(node) {
    const widgetY = (node._mmrpDomWidget && node._mmrpDomWidget.last_y) || 80;
    const outputsMin = ((node.outputs && node.outputs.length) || 1) * 20 + 40;
    // Height is the content's; the node must also budget the DOM widget's own margin,
    // which the frontend subtracts on both edges (its formula - see syncDomWidgetSize).
    // Budgeting less than that lets the block's bottom margin eat the node's inset and
    // leaves .mmrp-direction flush against the node frame.
    const margin = domWidgetMargin(node);
    const width = Math.max((node.size && node.size[0]) || 0, minNodeWidth(node));
    return [width, Math.max(widgetY + contentHeight(node) + margin * 2, outputsMin)];
}

// The frontend's per-widget margin, which it subtracts from both axes when it sizes the
// element. Under a resizable node this margin IS the block's inset within the node -
// there is no second padding on top of it - so it appears in every size calculation.
// Read off the widget rather than assumed, defaulting to the 10 it has been on every
// frontend measured.
function domWidgetMargin(node) {
    const m = node._mmrpDomWidget && node._mmrpDomWidget.margin;
    return typeof m === "number" ? m : 10;
}

// The DOM block is sized from `widget.width` / `widget.computedHeight`, NOT from
// computeSize(). ComfyUI_frontend src/components/graph/DomWidgets.vue::updateWidgets():
//
//     const newWidth  = (widget.width ?? posNode.width) - margin * 2
//     const newHeight = (widget.computedHeight ?? 50)   - margin * 2
//
// computeSize() is still assigned on the widget below because litegraph consults it for
// the node's own layout, but it has never had anything to do with the block's size, and
// reading it as though it did is what hid this for four releases.
//
// Measured on frontend 1.51.9, dpr 1, node [1340, 1318]: `width` sat at 274 and `margin`
// at 10, so the black slab rendered 254px wide inside a node whose CSS is written for
// 1320 — the reference strip was 19% of the node. `computedHeight` sat at 714, leaving
// the block 694px against the 710 its flex column needs, and the 16px came out of
// .mmrp-direction, the one child with no pinned height.
//
// Both are derived from the frontend's own formula rather than hardcoded, so a frontend
// that changes `margin` stays correct: each is the size the BLOCK must end up, with the
// margin the frontend is about to subtract added back on.
// litegraph re-derives computedHeight from the widget's own computeSize() and adds a
// row pad, CLOBBERING anything assigned to it. Measured on frontend 1.51.9: computeSize
// returned 710, computedHeight came back 714, and the element was sized 714 - 2*10 = 694
// - so writing computedHeight directly looked like it worked and did nothing, and the
// block stayed 16px short of what its flex column needs.
//
// So the height is routed through computeSize instead, pre-compensated for both the pad
// and the margin. The pad is MEASURED rather than hardcoded to 4: it is litegraph's
// number, not ours, and the first call's default corrects itself on the next one.
function domWidgetHeightPad(node) {
    const w = node._mmrpDomWidget;
    if (!w || typeof w.computedHeight !== "number" || typeof w._mmrpReported !== "number") {
        return 4;
    }
    const pad = w.computedHeight - w._mmrpReported;
    return pad >= 0 && pad < 40 ? pad : 4;
}

// What computeSize must report for the ELEMENT to end up `wanted` tall.
function reportedHeightFor(node, wanted) {
    return wanted + domWidgetMargin(node) * 2 - domWidgetHeightPad(node);
}

function syncDomWidgetSize(node) {
    const w = node._mmrpDomWidget;
    if (!w) return;
    const margin = domWidgetMargin(node);
    // try/catch for the same reason hideWidget()'s property writes have it: assigning
    // `domWidget.node` directly once hit a getter-only accessor on the V3 BaseWidget and
    // threw a TypeError that aborted loading ANY workflow containing this node. Measured
    // `width` as a plain property on 1.51.9, but a frontend is free to make it an
    // accessor tomorrow, and a mis-sized node beats an unloadable one.
    // Both writes name the size the BLOCK must end up, with the margin the frontend is
    // about to subtract added back on. Width tracks the node, because the node is the
    // user's to drag now; height is whatever the content came to.
    //
    // computedHeight is written for a frontend that uses it directly. Where litegraph
    // recomputes it from computeSize (1.51.9 does), the pre-compensated value that
    // computeSize reports is what actually lands - see reportedHeightFor.
    try { w.width = nodeSize(node)[0]; } catch (e) { /* frontend owns it */ }
    try { w.computedHeight = contentHeight(node) + margin * 2; } catch (e) { /* ditto */ }
}

// ---------------------------------------------------------------------------
// The tag rule. Mirrors minimax_refpack/refs.py ReferenceSet.assign_tags() exactly:
//   1. images, slot order -> <Picture 1..n>
//   2. per video, slot order: soundtrack's <Audio j> is assigned BEFORE the video's own
//      <Video k>; standalone audio continues the same <Audio> counter afterwards
// `refs` is {images: [{file}], videos: [{file, use_soundtrack}], audios: [{file}]} — the
// working state keeps references grouped by kind, one array per kind, in socket order.
// ---------------------------------------------------------------------------
export function assignTags(refs) {
    const images = (refs && refs.images) || [];
    const videos = (refs && refs.videos) || [];
    const audios = (refs && refs.audios) || [];
    const tagged = { images: [], videos: [], audios: [] };

    images.forEach((ref, i) => tagged.images.push({ ref, tag: `<Picture ${i + 1}>` }));

    let audioN = 0;
    videos.forEach((ref, i) => {
        let audioTag = null;
        if (ref.use_soundtrack) {
            audioN += 1;
            audioTag = `<Audio ${audioN}>`;
        }
        tagged.videos.push({ ref, tag: `<Video ${i + 1}>`, audioTag });
    });

    audios.forEach((ref) => {
        audioN += 1;
        tagged.audios.push({ ref, tag: `<Audio ${audioN}>` });
    });

    return tagged;
}

// ---------------------------------------------------------------------------
// Keeping the tags in the prompt pointing at the reference the user meant.
//
// The tags are not decoration - they are the contract between the direction text and the
// sockets, and they are POSITIONAL. Delete <Picture 2> of three and the third image
// becomes <Picture 2>; turn a video's soundtrack off and EVERY <Audio N> after it
// renumbers, because a soundtrack claims its audio tag before its own video tag
// (refs.py assign_tags). Either way a direction that said "<Picture 2> wears the jacket"
// silently starts describing a different image, and nothing in the UI says so.
//
// The block between the markers is extracted and run under node by tests/test_retag.py,
// the same way MMRP-MIGRATE is. Keep it free of imports and DOM.
// >>> MMRP-RETAG

// Flat "<Kind N>" -> "<Kind M>" map between two tag assignments, keyed by file. A file
// that is gone from `after` contributes nothing: its tag has no successor, so mentions of
// it are left alone rather than being pointed at whatever inherited the number.
export function tagRemap(before, after) {
    const map = {};
    for (const kind of ["images", "videos", "audios"]) {
        // Paired by the Nth OCCURRENCE of a file name, not by the first one carrying it.
        //
        // Two references to the same file are legal and not unusual: addFiles does no
        // de-duplication, and an upload with an existing name overwrites on the server and
        // comes back under that name. They are not interchangeable either - the same file
        // can appear twice with different crops, rotations or subjects, which is a
        // reasonable thing to want.
        //
        // Matching on the first entry with that name collapsed them: deleting b from
        // [a, b, a] mapped <Picture 3> to <Picture 1> instead of <Picture 2>, so the
        // prompt ended up pointing at the wrong one of the two and the survivor was left
        // with no tag referring to it at all.
        //
        // Occurrence order is the honest pairing. It cannot collapse two entries onto one
        // tag, and for a genuine reorder of identical files it produces a permutation
        // rather than a wrong answer.
        const consumed = new Map();
        for (const wasTagged of before[kind]) {
            const file = wasTagged.ref.file;
            const nth = consumed.get(file) || 0;
            consumed.set(file, nth + 1);
            let seen = 0;
            let now = null;
            for (const candidate of after[kind]) {
                if (candidate.ref.file !== file) continue;
                if (seen === nth) { now = candidate; break; }
                seen++;
            }
            if (!now) continue;
            if (now.tag !== wasTagged.tag) map[wasTagged.tag] = now.tag;
            // A video's soundtrack tag renumbers independently of its <Video N>.
            if (wasTagged.audioTag && now.audioTag && now.audioTag !== wasTagged.audioTag) {
                map[wasTagged.audioTag] = now.audioTag;
            }
        }
    }
    return map;
}

// Move one reference within its own array. Pure, so tests/test_retag.py covers it.
export function moveRef(list, from, to) {
    if (!Array.isArray(list)) return list;
    if (from < 0 || from >= list.length) return list.slice();
    const out = list.slice();
    const [moved] = out.splice(from, 1);
    // `to` is an INSERTION point in the original array, so once the item is lifted out,
    // any index past it shifts down by one. Getting this wrong is the classic off-by-one
    // that makes "drop just after myself" move the item a slot too far.
    const at = Math.max(0, Math.min(out.length, to > from ? to - 1 : to));
    out.splice(at, 0, moved);
    return out;
}

// Apply that map to the direction text in ONE pass.
//
// One pass is the whole point, not an optimisation. Rewriting tag by tag chains: with
// {1->2, 2->1} a sequential replace turns every <Picture 1> into <Picture 2> and then
// every <Picture 2> - including the ones just written - back into <Picture 1>, so a swap
// collapses to a no-op. Matching each tag once and looking its replacement up makes the
// substitution simultaneous by construction.
//
// <Subject N> is deliberately NOT matched. Those are assigned by the user, not derived
// from position, so they never renumber and rewriting them would be corruption.
export function retag(text, map) {
    if (!text || !map || !Object.keys(map).length) return text;
    return text.replace(/<(Picture|Video|Audio) (\d+)>/g, (whole) => map[whole] || whole);
}
// <<< MMRP-RETAG

// Run a mutation and carry the direction text with it. `mutate` returns the new refs.
// Off => the refs still change, the text simply is not touched.
function withRetag(node, mutate) {
    const before = assignTags(node._mmrpRefs);
    const next = mutate();
    if (retagEnabled(node)) {
        const map = tagRemap(before, assignTags(next));
        const w = widgetByName(node, "direction");
        const current = (w && w.value) || "";
        const updated = retag(current, map);
        if (updated !== current) {
            if (w) {
                w.value = updated;
                if (w.callback) w.callback(updated);
            }
            const el = node._mmrpBody && node._mmrpBody.directionInput;
            if (el && !writeTextPreservingUndo(el, 0, el.value.length, updated, true)) {
                el.value = updated;   // no undo to preserve here, but the text is right
            }
            mlog("retagged", { changes: Object.keys(map).length });
        }
    }
    applyRefs(node, next);
}

// >>> MMRP-SUBJECT
// The picker's cells, in tile-local coordinates. Cell 0 is "none"; 1..9 are the subjects.
//
// Returned as data rather than drawn inline so draw() and the hit test consume ONE
// description of the grid - a picker whose painted cells and clickable cells disagree is
// the worst possible version of this control.
export function subjectCells(tile, cell, gap) {
    const cols = 5;
    const rows = 2;
    const w = cols * cell + (cols - 1) * gap;
    const h = rows * cell + (rows - 1) * gap;
    const x0 = Math.round((tile - w) / 2);
    const y0 = Math.round((tile - h) / 2);
    const out = [];
    for (let i = 0; i < cols * rows; i++) {
        out.push({
            n: i,                                   // 0 = none
            x: x0 + (i % cols) * (cell + gap),
            y: y0 + Math.floor(i / cols) * (cell + gap),
            w: cell,
            h: cell,
        });
    }
    return out;
}

// Toggle one subject on a reference. `0` clears every subject rather than toggling one,
// which is what the empty square means.
//
// Membership is a SET, so a reference can define several subjects - a photo of a woman in
// a room is both the character and the location, and the packaged prompt has a line for
// each. Sorted and de-duplicated to match refs.validate_subjects, so the JSON the widget
// holds is already in the shape Python will hand back.
export function toggleSubject(subjects, n) {
    const set = new Set(Array.isArray(subjects) ? subjects : []);
    if (!n) return [];
    if (set.has(n)) set.delete(n);
    else set.add(n);
    return [...set].sort((a, b) => a - b);
}

// What the pill shows. Capped, because nine numbers do not fit in an 18px corner and a
// reference in more than three subjects is not something to render, it is something to
// summarise.
export function subjectPillText(subjects, max = 3) {
    const list = Array.isArray(subjects) ? subjects : [];
    if (!list.length) return null;
    if (list.length <= max) return list.join(" ");
    return `${list.slice(0, max).join(" ")}+${list.length - max}`;
}
// <<< MMRP-SUBJECT

// ---- references_json <-> working-state conversion --------------------
// refs.py's Reference shape:
//   {"kind": "image"|"video"|"audio", "file": str, [use_soundtrack], [crop], [trim]}
// crop = [x, y, w, h] fractions (image/video), trim = [start, end] seconds
// (video/audio) — both omitted when unset, mirroring refs.py's to_dict().

function takeEdit(v) {
    return Array.isArray(v) ? v.slice() : null;
}

// Python's truthiness, for a flag that both halves have to read the same way.
//
// JS and Python agree on every scalar and disagree on containers: `!![]` is true while
// `bool([])` is false, and the same for {}. A list is a malformed value for this flag, so
// neither answer is meaningful - but "the two halves differ, though only on nonsense" is
// exactly the caveat that turns into a bug report later, and matching outright costs three
// lines. `absent` is the one case that is not truthiness at all: a missing key means the
// default, which is why it is a parameter rather than another falsy value.
function pythonTruthy(value, whenAbsent) {
    if (value === undefined) return whenAbsent;
    if (Array.isArray(value)) return value.length > 0;
    if (value !== null && typeof value === "object") return Object.keys(value).length > 0;
    return !!value;
}

export function fromReferencesList(list) {
    const refs = { images: [], videos: [], audios: [] };
    // Everything beyond kind/file/use_soundtrack that must survive the round trip. Both
    // directions whitelist, so a field missing from here is dropped between the widget and
    // the working state - which presents as "the edit did not save" rather than as an
    // error, and is worth naming in one place for that reason.
    // What survives the round trip, and what is announced when it does not.
    //
    // GATED ON THE KIND for orientation, matching refs.Reference.from_dict's
    // `visual = kind in ("image", "video")`. Spread into every kind, an AUDIO reference in
    // a hand-written references_json kept rotate/flip through the round trip and
    // re-serialised them - the tile badge summarising an orientation the server had
    // already dropped. crop and trim were gated per kind here all along; orientation was
    // the one that was not.
    //
    // NOT gated for subjects: refs.py allows those on all three kinds deliberately, since
    // a voice belongs to a subject as much as a face does.
    //
    // AND IT SAYS WHEN IT DISCARDS SOMETHING. Python RAISES for values this quietly
    // ignored - rotate: "90", subjects: [1, 10] - so a hand-written references_json
    // renders as perfectly fine here and then fails the whole node the moment it is
    // queued. Worse, the first edit rewrites the widget without the discarded value, so
    // the evidence goes with it. Dropping is still right, because keeping it would mean
    // the browser accepting what the server refuses; doing it in silence is not.
    const discard = (file, field, value) => {
        console.warn(
            `[MiniMaxRefPack] ignoring ${field}=${JSON.stringify(value)} on ${file}: ` +
            "refs.py does not accept that value, so it was dropped rather than sent"
        );
    };
    const extras = (r, kind) => {
        const visual = kind === "image" || kind === "video";
        const out = {};

        // Sorted and de-duplicated on the way IN, mirroring refs.validate_subjects.
        // Without it a hand-edited or older value renders as "2 1" on the pill while the
        // server uses [1, 2] - the UI and the payload disagreeing about one reference.
        if (Array.isArray(r.subjects) && r.subjects.length) {
            const kept = r.subjects.filter((n) => Number.isInteger(n) && n >= 1 && n <= 9);
            const dropped = r.subjects.filter((n) => !kept.includes(n));
            if (dropped.length) discard(r.file, "subjects", dropped);
            if (kept.length) out.subjects = [...new Set(kept)].sort((a, b) => a - b);
        }

        if (r.rotate !== undefined && r.rotate !== null && r.rotate !== 0) {
            if (!visual) discard(r.file, "rotate", r.rotate);
            else if (Number.isFinite(r.rotate)) out.rotate = r.rotate;
            else discard(r.file, "rotate", r.rotate);
        }
        if (r.flip) {
            if (visual) out.flip = r.flip;
            else discard(r.file, "flip", r.flip);
        }
        if (r.rotate_expand === false) {
            if (visual) out.rotate_expand = false;
            else discard(r.file, "rotate_expand", r.rotate_expand);
        }
        return out;    };
    for (const r of list || []) {
        if (!r || typeof r.file !== "string") continue;
        if (r.kind === "image")
            refs.images.push({ file: r.file, missing: !!r.missing, crop: takeEdit(r.crop),
                               ...extras(r, "image") });
        else if (r.kind === "video")
            refs.videos.push({
                file: r.file,
                // ABSENT means on; PRESENT means whatever it is truthy as. Both halves
                // of that matter, and the obvious spellings each get one of them wrong.
                //
                // refs.Reference.from_dict is `bool(d.get("use_soundtrack", True))`:
                // missing key -> True, and anything else -> bool(value), so null, 0 and ""
                // are all OFF. `!!r.use_soundtrack` matched the second half and broke the
                // first (absent became OFF); `r.use_soundtrack !== false` matched the
                // first and broke the second (null, 0 and "" became ON).
                //
                // Either way the two halves disagree about a hand-written config or
                // references_json, and not harmlessly: a video's soundtrack claims its
                // <Audio N> before any standalone audio does, so one falsy flag renumbers
                // every audio tag after it. The browser then shows one numbering while the
                // queued payload uses another, and the next edit round-trips the browser's
                // answer back into the file. Both serialisers always write the key, so
                // this only ever surfaces on a file somebody wrote themselves - which is
                // exactly what a portable config is for.
                use_soundtrack: pythonTruthy(r.use_soundtrack, true),
                missing: !!r.missing,
                crop: takeEdit(r.crop),
                trim: takeEdit(r.trim),
                ...extras(r, "video"),
            });
        else if (r.kind === "audio")
            refs.audios.push({ file: r.file, missing: !!r.missing, trim: takeEdit(r.trim),
                               ...extras(r, "audio") });
    }
    return refs;
}

export function toReferencesList(refs) {
    const out = [];
    const withEdits = (d, r) => {
        if (Array.isArray(r.crop)) d.crop = r.crop.slice();
        if (Array.isArray(r.trim)) d.trim = r.trim.slice();
        if (Array.isArray(r.subjects) && r.subjects.length) {
            d.subjects = r.subjects.slice();
        }
        if (Number.isFinite(r.rotate) && r.rotate) d.rotate = r.rotate;
        if (r.flip) d.flip = r.flip;
        if (r.rotate_expand === false) d.rotate_expand = false;
        return d;
    };
    // The whole reference is handed to withEdits now, not a hand-picked subset of it.
    // Passing { crop: r.crop } meant every field added later was silently dropped for
    // images and audio while working for video, which is the sort of asymmetry nobody
    // finds by reading.
    for (const r of refs.images) out.push(withEdits({ kind: "image", file: r.file }, r));
    for (const r of refs.videos)
        out.push(withEdits({ kind: "video", file: r.file, use_soundtrack: !!r.use_soundtrack }, r));
    for (const r of refs.audios) out.push(withEdits({ kind: "audio", file: r.file }, r));
    return out;
}

// ---------------------------------------------------------------------------
// Crop/trim pure helpers — fraction-space math only, no DOM, so a node harness
// can exercise them the way pytest exercises refs.py.
// ---------------------------------------------------------------------------

// The badge's extra line: "2.00-6.50s · cropped" / "2.00-6.50s" / "cropped",
// or null when the reference is untouched.
export function editSummary(ref) {
    const parts = [];
    if (ref && Array.isArray(ref.trim)) parts.push(`${ref.trim[0].toFixed(2)}-${ref.trim[1].toFixed(2)}s`);
    if (ref && Array.isArray(ref.crop)) parts.push("cropped");
    // Rotation says its angle, because 90 and 270 look alike on a thumbnail and the
    // difference is the whole point. A flip has nothing to state but itself.
    if (ref && ref.rotate) parts.push(`${Math.round(ref.rotate)}°`);
    if (ref && ref.flip) parts.push(ref.flip === "hv" ? "flipped ↔↕" : ref.flip === "h" ? "flipped ↔" : "flipped ↕");
    return parts.length ? parts.join(" · ") : null;
}

function hasEdit(ref) {
    return !!(ref && (Array.isArray(ref.crop) || Array.isArray(ref.trim)
                      || ref.rotate || ref.flip));
}

// >>> MMRP-ORIENT
// Turning the CROP RECT with the frame.
//
// A crop is fractions of the frame, so rotating the media without rotating the rect
// silently re-points it: [0,0,0.5,1] means "the left half", and after a quarter turn the
// left half is somewhere else. Moving the rect with the frame keeps the same PIXELS
// selected, which is what "turn it" means, and for a quarter turn it is exact.
//
// delta is a multiple of 90, clockwise.
export function rotateCropRect(rect, delta) {
    if (!Array.isArray(rect) || rect.length !== 4) return rect;
    let [x, y, w, h] = rect;
    let turns = ((Math.round(delta / 90) % 4) + 4) % 4;
    while (turns--) {
        // One clockwise quarter turn in fraction space: the axes swap, and the new x is
        // measured from the old BOTTOM edge.
        [x, y, w, h] = [1 - y - h, x, h, w];
    }
    return [x, y, w, h];
}

// Mirror the rect across the same axis the media was mirrored on.
export function mirrorCropRect(rect, axis) {
    if (!Array.isArray(rect) || rect.length !== 4) return rect;
    const [x, y, w, h] = rect;
    if (axis === "h") return [1 - x - w, y, w, h];
    if (axis === "v") return [x, 1 - y - h, w, h];
    return rect;
}

// A slider position -> the angle actually stored.
//
// Snapping matters for a reason beyond tidiness: a quarter turn is LOSSLESS (transpose /
// strides) while anything else resamples every frame. A slider parked on 89.6 would cost
// a full re-render of a clip and look identical, so the snap is what keeps the cheap path
// reachable by hand. `tolerance` is 0 when the user asks for a free angle explicitly.
export function snapAngle(value, tolerance = 3) {
    const raw = ((Number(value) % 360) + 360) % 360;
    if (!tolerance) return raw;
    for (const stop of [0, 90, 180, 270, 360]) {
        if (Math.abs(raw - stop) <= tolerance) return stop % 360;
    }
    return raw;
}

// The SCREEN axis the user clicked, translated into the SOURCE axis that produces it.
//
// The pipeline is exif -> flip -> rotate -> crop, so `flip` is stored in the SOURCE frame
// while the mirror buttons and the crop rect both live in the DISPLAYED one. Those frames
// only agree at 0 and 180. At 90 and 270 they are transposed, so clicking "mirror
// left-right" stored a source `h` flip that shows up on screen as a TOP-BOTTOM mirror -
// the wrong visual - while the crop rect was reflected left-right, which is the wrong
// axis for the pixels that actually moved. The box then covered different pixels than the
// ones inside it when it was drawn.
//
// Verified against the real media._orient_pil for all four quarter turns: the axis swaps
// exactly when the nearest quarter turn is odd.
//
// Off the quarter turns there is no exact answer - a source-axis flip composed with an
// arbitrary rotation is a mirror about a slanted line, which is neither h nor v on screen
// - so the nearest quarter turn is used, which is exact wherever exactness exists.
export function sourceFlipAxis(axis, rotate) {
    const quarter = (((Math.round((Number(rotate) || 0) / 90) % 4) + 4) % 4);
    if (quarter % 2 === 0) return axis;
    return axis === "h" ? "v" : "h";
}

// Toggle one mirror axis on or off, normalising to refs.py's spelling ("h"/"v"/"hv").
export function toggleFlipAxis(flip, axis) {
    const has = { h: false, v: false };
    for (const ch of String(flip || "")) if (ch in has) has[ch] = true;
    has[axis] = !has[axis];
    const out = (has.h ? "h" : "") + (has.v ? "v" : "");
    return out || null;
}
// <<< MMRP-ORIENT

function clamp01(v, lo, hi) {
    return Math.min(Math.max(v, lo), hi);
}

// What Save actually writes: round to 4 decimals (plenty below one pixel at 8K),
// clamp into the unit square with w capped at 1-x so refs.py's validate_crop can
// never reject a rect this produced, and collapse a (near-)full-frame rect to null
// — no crop at all, so an untouched reference serialises exactly as before.
export function normalizeCrop(rect) {
    if (!rect) return null;
    const r4 = (v) => Math.round(v * 1e4) / 1e4;
    const x = clamp01(r4(rect[0]), 0, 1);
    const y = clamp01(r4(rect[1]), 0, 1);
    const w = Math.min(r4(rect[2]), r4(1 - x));
    const h = Math.min(r4(rect[3]), r4(1 - y));
    if (w <= 0 || h <= 0) return null;
    if (x === 0 && y === 0 && w === 1 && h === 1) return null;
    return [x, y, w, h];
}

// What Save writes for the trim: [start, end] rounded to 2dp, or null when the
// window covers (within the 2dp rounding, 4ms) the whole clip — no trim at all,
// mirroring normalizeCrop's full-frame collapse. Also the predicate behind the
// modal's "Clear trim" disabled state: null here means there is nothing to clear.
export function normalizeTrim(trim, duration) {
    if (!trim || !duration) return null;
    const r2 = (v) => Math.round(v * 100) / 100;
    const s = r2(trim[0]);
    const e = r2(trim[1]);
    if (e <= s) return null;
    if (s <= 0.004 && e >= r2(duration) - 0.004) return null;
    return [s, e];
}

// One pointer-drag step over a fraction rect. mode = "move" | "nw"|"ne"|"sw"|"se";
// dx/dy are pointer deltas as fractions of the media box. `ratio` (a PIXEL w:h,
// e.g. 16/9) locks the rect's pixel aspect, which in fraction space means
// hFrac = wFrac * mediaW / (ratio * mediaH). The corner opposite the dragged one
// is the anchor and never moves.
export function dragCrop(rect, mode, dx, dy, ratio, mediaW, mediaH) {
    const MIN = 0.02;
    const [x, y, w, h] = rect;
    if (mode === "move") {
        return [clamp01(x + dx, 0, 1 - w), clamp01(y + dy, 0, 1 - h), w, h];
    }
    const ax = mode === "nw" || mode === "sw" ? x + w : x;
    const ay = mode === "nw" || mode === "ne" ? y + h : y;
    const cx = clamp01((mode === "nw" || mode === "sw" ? x : x + w) + dx, 0, 1);
    const cy = clamp01((mode === "nw" || mode === "ne" ? y : y + h) + dy, 0, 1);
    let nw = Math.max(Math.abs(cx - ax), MIN);
    let nh = Math.max(Math.abs(cy - ay), MIN);
    if (ratio && mediaW && mediaH) {
        nh = (nw * mediaW) / (ratio * mediaH);
        const maxH = cy >= ay ? 1 - ay : ay; // room on the side being dragged into
        if (nh > maxH) {
            nh = maxH;
            nw = (nh * ratio * mediaH) / mediaW;
        }
    }
    const nx = cx >= ax ? ax : ax - nw;
    const ny = cy >= ay ? ay : ay - nh;
    return [clamp01(nx, 0, 1 - nw), clamp01(ny, 0, 1 - nh), nw, nh];
}

// An aspect-preset click: reshape the current rect around its own centre to the
// given PIXEL ratio, spilling as little as possible past the frame.
export function setRectAspect(rect, ratio, mediaW, mediaH) {
    const [x, y, w, h] = rect;
    const cx = x + w / 2;
    const cy = y + h / 2;
    let nw = w;
    let nh = (w * mediaW) / (ratio * mediaH);
    if (nh > 1) {
        nw = nw / nh;
        nh = 1;
    }
    return [clamp01(cx - nw / 2, 0, 1 - nw), clamp01(cy - nh / 2, 0, 1 - nh), nw, nh];
}

/** Framing for "play the modified video": the crop region blown up to fill the same
 * footprint the untouched media occupies, so the preview shows what the socket emits
 * rather than a rectangle drawn over the original.
 *
 * `dw`/`dh` are the media's CURRENT displayed size in CSS px. The crop region is
 * scaled by min(dw/cw, dh/ch) - the largest scale that still fits the footprint - so
 * the region's own aspect is preserved and the box is never overflowed. The media
 * itself scales by the same factor and shifts by the crop origin, which is what puts
 * the region under the wrapper's visible window.
 */
export function cropPreviewBox(crop, dw, dh) {
    const [x, y, w, h] = crop;
    const cw = dw * w;
    const ch = dh * h;
    const scale = Math.min(dw / cw, dh / ch);
    return {
        wrapW: cw * scale,
        wrapH: ch * scale,
        mediaW: dw * scale,
        mediaH: dh * scale,
        left: -x * dw * scale,
        top: -y * dh * scale,
    };
}

function emptyRefs() {
    return { images: [], videos: [], audios: [] };
}

function cloneRefs(refs) {
    return {
        images: refs.images.map((r) => ({ ...r })),
        videos: refs.videos.map((r) => ({ ...r })),
        audios: refs.audios.map((r) => ({ ...r })),
    };
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiUpload(file) {
    const form = new FormData();
    form.append("image", file);
    form.append("type", "input");
    form.append("overwrite", "true");
    const res = await fetch("/upload/image", { method: "POST", body: form });
    if (!res.ok) throw new Error(`upload failed: ${res.status}`);
    const info = await res.json();
    return info.name;
}

// The tile thumb: cropped by the route (media.thumbnail_png), and for a trimmed
// video taken at the in-point (`t=`) so the tile previews the span that will
// actually be emitted, not frame 0 of the untrimmed file.
function thumbUrl(file, ref) {
    let url = `/minimax_refpack/thumb?file=${encodeURIComponent(file)}`;
    // Orientation goes FIRST in the query for no functional reason, but crop is applied
    // after it server-side and reading them in that order matches media.py's order.
    if (ref && ref.flip) url += `&flip=${encodeURIComponent(ref.flip)}`;
    if (ref && ref.rotate) url += `&rotate=${ref.rotate}`;
    if (ref && ref.rotate === undefined ? false : ref && ref.rotate_expand === false) {
        url += "&expand=0";
    }
    if (ref && Array.isArray(ref.crop)) url += `&crop=${ref.crop.join(",")}`;
    if (ref && Array.isArray(ref.trim)) url += `&t=${ref.trim[0]}`;
    return url;
}

// Raw file, for the click-to-play previews (thumbUrl is a server-generated still).
// Stock ComfyUI route — confirmed at server.py:511 (`@routes.get("/view")`, no /api prefix).
function fileUrl(file) {
    return `/view?filename=${encodeURIComponent(file)}&type=input`;
}

// NOTE: there is no apiSavePack/apiLoadPack/apiListPacks any more. Configs are files
// on the user's own machine (download + file picker, see "Config save/load" below),
// so the /minimax_refpack/packs routes are no longer called from the UI at all.

// `mode` is which packaged prompt to hand back — the two registers are separate files.
// Omitted, the route answers `standard`, which is what it always did and is why a
// replacement workflow used to be shown the wrong one.
async function apiSystemPromptDefault(mode) {
    const qs = mode ? `?mode=${encodeURIComponent(mode)}` : "";
    const res = await fetch(`/minimax_refpack/system_prompt${qs}`);
    if (!res.ok) throw new Error(`fetch failed: ${res.status}`);
    const data = await res.json();
    // The route echoes back the mode it RESOLVED, which is the point of it echoing at
    // all: it absorbs anything it does not recognise into `standard`, so what came back
    // is not necessarily what was asked for. Returning it lets the caller label the text
    // it is actually showing rather than the text it hoped for.
    return { text: data.default || "", mode: data.mode || mode || "standard" };
}

// Which packaged prompt this workflow is actually going to use.
//
// `auto` is genuinely unanswerable here: it is resolved at queue time by a classifier
// that reads the reference set and the direction text, so the browser cannot know. It
// resolves to `standard` because that is what auto falls back to on every failure path
// (prompt.classify_mode) and what it picks for almost every job — but the modal SAYS so
// rather than presenting a guess as a fact.
function systemPromptModeOf(node) {
    const jobType = String(widgetByName(node, "job_type")?.value ?? "").trim().toLowerCase();
    return jobType === "replacement" ? "replacement" : "standard";
}

// >>> MMRP-LOOPBACK
// Mirrors endpoint.is_loopback in Python, and like migrateProviderValue vs
// normalize_provider both halves exist ON PURPOSE. This one decides only what the browser
// bothers to ASK for; the Python one decides what the process will actually connect to.
// The server stays the sole enforcement point — a browser is not a place to enforce
// anything — so this copy being wrong costs a confusing message, never a probe that
// should not have happened.
//
// It exists so a LAN address typed into api_base gets an explanation on the modal instead
// of a 400 the user never sees.
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

function isLoopbackUrl(url) {
    let parsed;
    try {
        parsed = new URL(String(url ?? "").trim());
    } catch (e) {
        return false;   // "" and junk both land here, matching urlparse's refusal
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    // URL keeps an IPv6 host bracketed; Python's urlparse .hostname strips the brackets,
    // and endpoint.py's set carries both spellings. Strip so the two agree on `[::1]`.
    const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    return LOOPBACK_HOSTS.has(host);
}
// <<< MMRP-LOOPBACK

// Deliberately asks the SERVER to probe rather than probing from here. Two reasons, and
// the first is the one that matters: `localhost` has to mean whatever the ComfyUI process
// can reach, so a Dockerised or remote ComfyUI gets told the truth about its own network
// instead of about the machine this browser happens to be sitting on. The second is that
// a local LLM server has no reason to send CORS headers, so the browser could open the
// socket and still not be allowed to read the answer.
async function apiDetectServers(base) {
    const qs = base ? `?base=${encodeURIComponent(base)}` : "";
    const res = await fetch(`/minimax_refpack/detect${qs}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `detect failed: ${res.status}`);
    return data.servers || [];
}

// One probe per filename, cached module-wide and reused across every redraw (a
// naive per-draw fetch would hit this route constantly). probeResults holds the
// RESOLVED value so draw() — which is synchronous — can read it without awaiting:
// absent = still pending, true/false = probe's answer, null = probe itself failed
// (unknown, not "no audio").
const probeCache = new Map();
const probeResults = new Map();

function probeHasAudio(file) {
    if (!probeCache.has(file)) {
        probeCache.set(
            file,
            fetch(`/minimax_refpack/probe?file=${encodeURIComponent(file)}`)
                .then((res) => (res.ok ? res.json() : null))
                .then((data) => (data ? !!data.has_audio : null))
                .catch(() => null)
        );
    }
    return probeCache.get(file);
}

// Kick (or re-kick) probes for every current video and repaint when they answer.
// A saved use_soundtrack=true against a clip the probe says is SILENT (e.g. a config
// restored against a since-replaced file) is forced off — otherwise a stale <Audio N>
// tag points at nothing. Probe FAILURE (null) does not force off: "couldn't check"
// is not "no audio", so the user's saved intent survives a flaky probe.
function syncProbes(node) {
    for (const ref of node._mmrpRefs.videos) {
        const file = ref.file;
        probeHasAudio(file).then((hasAudio) => {
            probeResults.set(file, hasAudio);
            if (hasAudio === false) {
                const cur = node._mmrpRefs.videos.find((v) => v.file === file);
                if (cur && cur.use_soundtrack) {
                    // Clearing the flag releases that video's <Audio N>, renumbering every
                    // audio tag after it - so this goes through the retag pass like the
                    // manual toggle does. It fires when a saved workflow's clip has been
                    // replaced with a silent one, which is precisely when the user has a
                    // prompt already written against the old numbering.
                    withRetag(node, () => {
                        const next = cloneRefs(node._mmrpRefs);
                        next.videos.find((v) => v.file === file).use_soundtrack = false;
                        return next;
                    });
                    return; // withRetag -> applyRefs already repaints
                }
            }
            scheduleDraw(node);
        });
    }
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

function injectStyles() {
    if (document.getElementById("mmrp-styles")) return;
    const link = document.createElement("link");
    link.id = "mmrp-styles";
    link.rel = "stylesheet";
    link.href = new URL("./refpack.css", import.meta.url).href;
    document.head.appendChild(link);
}

// ---------------------------------------------------------------------------
// Hidden-widget helper — adapted from WhatDreamsCost multi_image_loader.js's
// hide-widget pattern: defineProperty-lock hidden/type against V3's reactive
// redraws, force computeSize unconditionally, poll for the async-created DOM
// element. See the header comment for why every half of this is load-bearing.
// ---------------------------------------------------------------------------

// Returns the hide-poll interval id (or undefined if there was no widget) so the
// caller can clear it early on node removal.
function hideWidget(w) {
    if (!w) return undefined;
    // Every write here is try/catch-wrapped: some frontend versions define these as
    // getter-only reactive accessors (that's exactly what broke on `domWidget.node =`,
    // see the header comment) — degrade instead of throwing and aborting workflow load.
    try {
        Object.defineProperty(w, "hidden", { get: () => true, set: () => {} });
    } catch (_) {
        try {
            w.hidden = true;
        } catch (_) {}
    }
    try {
        Object.defineProperty(w, "type", { get: () => "hidden", set: () => {} });
    } catch (_) {}
    try {
        if (!w.options) w.options = {};
        w.options.hidden = true;
    } catch (_) {}
    // computeSize = [0,0] UNCONDITIONALLY. Gating this behind a "skip on vueNodesMode"
    // check (a previous bug) left these multiline STRING widgets reserving real layout
    // height on V3 — stacked, that's the thin bar that overlapped the upload row.
    try {
        w.computeSize = () => [0, 0];
    } catch (_) {}
    if (!window.LiteGraph || !window.LiteGraph.vueNodesMode) {
        try {
            w.draw = () => {};
        } catch (_) {}
    }
    try {
        if (w.element) w.element.style.display = "none";
    } catch (_) {}
    // V3 creates the DOM element backing a multiline STRING widget ASYNCHRONOUSLY — it
    // does not exist yet at this point in onNodeCreated, so the display:none above is a
    // no-op the first time through. Poll briefly to catch it once it appears ("Catch
    // for V3 delayed DOM rendering to ensure no stubborn inputs appear" — same as the
    // reference). Self-clears after 1s; the onRemoved wrapper also clears it early.
    const hideInterval = setInterval(() => {
        try {
            if (w.element) w.element.style.display = "none";
        } catch (_) {}
    }, 50);
    setTimeout(() => clearInterval(hideInterval), 1000);
    return hideInterval;
}

// ---------------------------------------------------------------------------
// Thumbnail cache — module-wide, keyed by filename, shared across node instances.
// The thumb route serves a PNG for images AND videos (media.py thumbnail_png), so
// both kinds draw the same way; audio tiles get a drawn speaker glyph instead.
// liveNodes tracks which nodes to repaint when a thumb lands.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Structured logging. Same line shape as the Python side (minimax_refpack/logs.py):
// `[MiniMaxRefPack] event=<name> key=value`, so one grep covers the browser console
// and the ComfyUI console, and a bug report from either reads the same way.
//
// info = state changes worth seeing by default (uploads, edits, previews, config).
// debug = per-tile chatter (thumb retries), hidden until the console's Verbose level.
// warn = something the user will notice going wrong.
// ---------------------------------------------------------------------------

const LOG_PREFIX = "[MiniMaxRefPack]";

function logValue(value) {
    if (typeof value === "boolean") return value ? "true" : "false";
    if (typeof value === "number") {
        // 3dp, trailing zeros dropped - matches logs.py so the two consoles agree
        return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
    }
    if (Array.isArray(value)) return `[${value.map(logValue).join(",")}]`;
    const text = String(value);
    if (!text) return '""';
    return /[ "]/.test(text) ? `"${text.replace(/"/g, "'")}"` : text;
}

export function logLine(event, fields) {
    let line = `${LOG_PREFIX} event=${event}`;
    for (const [key, value] of Object.entries(fields || {})) {
        if (value === undefined || value === null) continue;
        line += ` ${key}=${logValue(value)}`;
    }
    return line;
}

const mlog = (event, fields) => console.info(logLine(event, fields));
const mdebug = (event, fields) => console.debug(logLine(event, fields));
const mwarn = (event, fields) => console.warn(logLine(event, fields));

const thumbCache = new Map(); // file -> { img, state: "loading"|"ok"|"error", tries, reqs, timer }
const liveNodes = new Set();

// A thumb request fails for reasons that have nothing to do with the file: thumb_route
// decodes SYNCHRONOUSLY on the aiohttp loop, so while ComfyUI is still starting up (model
// loads, Manager's registry fetch) the request stalls and whatever proxy sits in front of
// the pod kills it. The first version cached that <img> error forever — one unlucky
// request and the tile read "no preview" until the tab was reloaded, with the file sitting
// happily in input/ the whole time. So: back off and retry, and only claim "no preview"
// once the ladder is exhausted.
const THUMB_RETRY_MS = [1000, 2000, 4000, 8000, 15000];

function repaintLive() {
    for (const n of liveNodes) scheduleDraw(n);
}

// `reqs` is monotonic and only feeds the cache-buster — a retry must not be answered from
// whatever cached the failure. The first request stays clean so it can be cached normally.
// `entry.base` carries the crop/trim query the entry was created with; an edit drops the
// whole entry (dropThumb) rather than mutating it.
function requestThumb(entry) {
    entry.timer = null;
    entry.reqs += 1;
    entry.img.src = entry.reqs === 1 ? entry.base : `${entry.base}&retry=${entry.reqs - 1}`;
}

function getThumb(file, ref) {
    let entry = thumbCache.get(file);
    if (!entry) {
        const img = new Image();
        entry = { img, file, base: thumbUrl(file, ref), state: "loading", tries: 0, reqs: 0, timer: null };
        img.onload = () => {
            entry.state = "ok";
            entry.tries = 0;
            repaintLive();
        };
        img.onerror = () => {
            const delay = THUMB_RETRY_MS[entry.tries];
            entry.tries += 1;
            if (delay === undefined) {
                // Ladder spent (~30s). Say so on the tile — but the entry stays retryable,
                // see retryFailedThumbs().
                entry.state = "error";
                mwarn("thumb_failed", { file: entry.file, tries: entry.tries - 1 });
                repaintLive();
                return;
            }
            // Still trying: the tile shows the plain well, not a verdict it may have to
            // take back a second later.
            entry.state = "loading";
            mdebug("thumb_retry", { file: entry.file, attempt: entry.tries, in_ms: delay });
            entry.timer = setTimeout(() => requestThumb(entry), delay);
        };
        thumbCache.set(file, entry);
        requestThumb(entry);
    }
    return entry;
}

// Saving an edit invalidates the file's cached thumb so the next draw re-fetches
// through the route with the new crop/t baked into the URL.
function dropThumb(file) {
    const entry = thumbCache.get(file);
    if (!entry) return;
    if (entry.timer) clearTimeout(entry.timer);
    entry.img.onload = null;
    entry.img.onerror = null;
    thumbCache.delete(file);
}

// Giving up is not giving up for good. Anything that says the world may have changed — the
// tab coming back to the front, the browser going back online — puts every failed thumb
// back in flight, so a pod that took minutes to finish booting recovers on its own instead
// of demanding a page reload. Cheap: entries that loaded fine are skipped, and nothing
// here polls.
function retryFailedThumbs() {
    let any = false;
    for (const [file, entry] of thumbCache) {
        if (entry.state !== "error") continue;
        entry.state = "loading";
        entry.tries = 0;
        requestThumb(entry);
        any = true;
    }
    if (any) repaintLive();
}

window.addEventListener("focus", retryFailedThumbs);
window.addEventListener("online", retryFailedThumbs);

// The single repaint funnel: every change (refs, selection, thumb load, probe
// answer, preview toggle, the canvas's one real mount) lands here, coalesced to one
// draw() per frame. NOT a permanent rAF loop — nothing re-queues unless something
// changes again.
function scheduleDraw(node) {
    if (node._mmrpDrawQueued) return;
    node._mmrpDrawQueued = true;
    requestAnimationFrame(() => {
        node._mmrpDrawQueued = false;
        draw(node);
    });
}

// ---------------------------------------------------------------------------
// Drawing. All geometry in CSS px; hit regions are recorded during draw() in the
// same space getMousePos() reports, so hit-testing is a point-in-rect scan over
// what was actually painted last.
// ---------------------------------------------------------------------------

function pathRoundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
}

// object-fit: cover, in canvas terms — crop the source centrally to the tile's
// aspect instead of squashing it.
function drawCover(ctx, img, x, y, w, h) {
    const iw = img.naturalWidth;
    const ih = img.naturalHeight;
    if (!iw || !ih) return;
    const s = Math.max(w / iw, h / ih);
    const sw = w / s;
    const sh = h / s;
    ctx.drawImage(img, (iw - sw) / 2, (ih - sh) / 2, sw, sh, x, y, w, h);
}

// Monochrome vector speaker for audio tiles — a fillText emoji would render in
// color and break the greyscale palette.
function drawSpeaker(ctx, cx, cy, s, color) {
    ctx.save();
    ctx.fillStyle = color;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(cx - s * 0.7, cy - s * 0.28);
    ctx.lineTo(cx - s * 0.3, cy - s * 0.28);
    ctx.lineTo(cx + s * 0.1, cy - s * 0.65);
    ctx.lineTo(cx + s * 0.1, cy + s * 0.65);
    ctx.lineTo(cx - s * 0.3, cy + s * 0.28);
    ctx.lineTo(cx - s * 0.7, cy + s * 0.28);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.arc(cx + s * 0.25, cy, s * 0.45, -Math.PI / 3, Math.PI / 3);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx + s * 0.25, cy, s * 0.78, -Math.PI / 3, Math.PI / 3);
    ctx.stroke();
    ctx.restore();
}

// The click-to-play affordance: dark circle, drawn ▶ or ❚❚ (vector, no emoji).
function drawPlayGlyph(ctx, cx, cy, playing) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.beginPath();
    ctx.arc(cx, cy, CL.playR, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = C.text;
    if (playing) {
        ctx.fillRect(cx - 6, cy - 6.5, 4.5, 13);
        ctx.fillRect(cx + 1.5, cy - 6.5, 4.5, 13);
    } else {
        ctx.beginPath();
        ctx.moveTo(cx - 4.5, cy - 7);
        ctx.lineTo(cx + 8, cy);
        ctx.lineTo(cx - 4.5, cy + 7);
        ctx.closePath();
        ctx.fill();
    }
}

// The crop/trim entry point: a scissors chip in the tile's top-left — the same quiet
// dark-circle family as the delete chip (vector, no emoji). Inverts to a light chip
// while the reference carries an edit, mirroring how ♪ signals on/off.
function drawEditChip(ctx, x, y, edited) {
    const dR = CL.del / 2;
    const cx = x + dR + 3;
    const cy = y + dR + 3;
    ctx.fillStyle = edited ? C.text : "rgba(0, 0, 0, 0.65)";
    ctx.beginPath();
    ctx.arc(cx, cy, dR, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = edited ? "#000" : "#555";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, dR - 0.5, 0, Math.PI * 2);
    ctx.stroke();
    // Scissors: two crossing blades up, two finger loops down.
    const g = edited ? "#000" : "#fff";
    ctx.strokeStyle = g;
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(cx - 2.2, cy + 1.6);
    ctx.lineTo(cx + 4, cy - 4.2);
    ctx.moveTo(cx + 2.2, cy + 1.6);
    ctx.lineTo(cx - 4, cy - 4.2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx - 3.1, cy + 3.1, 1.7, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx + 3.1, cy + 3.1, 1.7, 0, Math.PI * 2);
    ctx.stroke();
}

// One tile: well, thumbnail/glyph, tag badge, delete, edit chip, soundtrack state,
// play glyph, missing treatment, selection stroke. `lines` is the pre-computed badge
// text (up to three: tag, a video's <Audio N>, the crop/trim summary); `playState` is
// null | "play" | "pause" (null = no preview affordance: images, missing refs).
function drawTile(ctx, kind, ref, lines, x, y, selected, soundState, badgeH, playState) {
    const T = CL.tile;

    ctx.save();
    pathRoundRect(ctx, x, y, T, T, 4);
    ctx.clip();

    ctx.fillStyle = kind === "audio" ? C.surface : C.well;
    ctx.fillRect(x, y, T, T);

    if (kind === "audio") {
        // Speaker in the upper half; the play glyph takes the lower half.
        drawSpeaker(ctx, x + T / 2, y + 34, 18, ref.missing ? C.textDim : C.textFaint);
    } else {
        const entry = getThumb(ref.file, ref);
        if (entry.state === "ok") {
            if (ref.missing) ctx.globalAlpha = 0.6;
            drawCover(ctx, entry.img, x, y, T, T);
            ctx.globalAlpha = 1;
        } else if (entry.state === "error" && !ref.missing) {
            ctx.fillStyle = C.textDim;
            ctx.font = "11px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText("no preview", x + T / 2, y + T / 2);
        }
    }

    if (ref.missing) {
        ctx.fillStyle = C.danger;
        ctx.font = "bold 11px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("missing", x + T / 2, y + T - badgeH - 8);
    }

    if (playState) {
        // Video: slightly above center to clear the badge; audio: lower half.
        drawPlayGlyph(ctx, x + T / 2, kind === "audio" ? y + 80 : y + 58, playState === "pause");
    }

    // Tag badge — drawn text on a translucent strip pinned to the tile's bottom.
    // Deliberately dark: it always overlays a thumbnail or a lightened well, never
    // the black ground, and #e0e0e0 text carries it.
    ctx.fillStyle = "rgba(0, 0, 0, 0.7)";
    ctx.fillRect(x, y + T - badgeH, T, badgeH);
    ctx.fillStyle = C.text;
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    lines.forEach((line, i) => {
        ctx.fillText(line, x + T / 2, y + T - badgeH + 13 + i * 13);
    });

    // Delete affordance — always visible (canvas has no cheap hover), but QUIET:
    // a dark chip, not a red square. Red is the node's one accent and it belongs
    // to selection, not to a destructive secondary action (UI review #5).
    const dR = CL.del / 2;
    const dcx = x + T - dR - 3;
    const dcy = y + dR + 3;
    ctx.fillStyle = "rgba(0, 0, 0, 0.65)";
    ctx.beginPath();
    ctx.arc(dcx, dcy, dR, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#555";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(dcx, dcy, dR - 0.5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(dcx - 4, dcy - 4);
    ctx.lineTo(dcx + 4, dcy + 4);
    ctx.moveTo(dcx + 4, dcy - 4);
    ctx.lineTo(dcx - 4, dcy + 4);
    ctx.stroke();

    // Crop/trim entry point — top-left, the one corner nothing else owns. A missing
    // file has no media to show in the editor, so it gets no chip.
    if (!ref.missing) drawEditChip(ctx, x, y, hasEdit(ref));

    if (soundState) {
        const S = CL.sound;
        const sy = y + T - badgeH - S - 3;
        const on = soundState === "on";
        // FULLY OPAQUE chip (LEDGER "see-through music icon"): the old fills were
        // rgba(255,255,255,0.15) when on / rgba(0,0,0,0.65) otherwise, compositing
        // the thumbnail through — on a light or busy clip the toggle all but
        // vanished. Solid fills only, with a border to lift the chip's edge off a
        // dark thumbnail; "on" inverts to a light chip with a black glyph so the
        // two states can't be confused. Same rect, same radius — geometry untouched.
        ctx.fillStyle = on ? C.text : "#111";
        pathRoundRect(ctx, x + 3, sy, S, S, 4);
        ctx.fill();
        ctx.strokeStyle = on ? "#000" : "#555";
        ctx.lineWidth = 1;
        pathRoundRect(ctx, x + 3.5, sy + 0.5, S - 1, S - 1, 4);
        ctx.stroke();
        ctx.fillStyle =
            soundState === "pending" ? "#555" : soundState === "none" ? "#444" : on ? "#000" : C.textMuted;
        ctx.font = "16px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("♪", x + 3 + S / 2, sy + 20);
        if (soundState === "none") {
            // Struck through: this clip has no audio track, permanently disabled.
            ctx.strokeStyle = "#555";
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(x + 7, sy + S - 5);
            ctx.lineTo(x + S - 1, sy + 5);
            ctx.stroke();
        }
    }

    // Subject membership, bottom-right above the tag strip - the mirror of the music
    // toggle's corner. Only drawn when the user has actually grouped this reference; an
    // empty badge on every tile would be noise on the majority that carry none.
    const pill = subjectPillText(ref.subjects);
    if (pill) {
        ctx.font = "bold 11px sans-serif";
        const pw = Math.max(CL.subPill, ctx.measureText(pill).width + 10);
        const px = x + T - pw - 3;
        const py = y + T - badgeH - CL.subPill - 3;
        ctx.fillStyle = C.text;
        pathRoundRect(ctx, px, py, pw, CL.subPill, 4);
        ctx.fill();
        ctx.fillStyle = "#000";
        ctx.textAlign = "center";
        ctx.fillText(pill, px + pw / 2, py + 13);
    }

    ctx.restore();

    ctx.strokeStyle = ref.missing ? C.danger : C.border;
    ctx.lineWidth = 1;
    pathRoundRect(ctx, x + 0.5, y + 0.5, T - 1, T - 1, 4);
    ctx.stroke();

    if (selected) {
        // The one accent in the whole node: LTX-red rectangle around the tile.
        ctx.strokeStyle = C.danger;
        ctx.lineWidth = 3;
        pathRoundRect(ctx, x - 2.5, y - 2.5, T + 5, T + 5, 7);
        ctx.stroke();
    }
}

// Vector upload arrow — same family as the toolbar's SVG icon (shaft, chevron
// head, tray) so the two read as one set. Drawn, not text; no emoji.
function drawUploadGlyph(ctx, cx, cy, s, color) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(cx, cy + s * 0.15);
    ctx.lineTo(cx, cy - s * 0.5);
    ctx.moveTo(cx - s * 0.3, cy - s * 0.2);
    ctx.lineTo(cx, cy - s * 0.5);
    ctx.lineTo(cx + s * 0.3, cy - s * 0.2);
    ctx.moveTo(cx - s * 0.45, cy + s * 0.3);
    ctx.lineTo(cx - s * 0.45, cy + s * 0.5);
    ctx.lineTo(cx + s * 0.45, cy + s * 0.5);
    ctx.lineTo(cx + s * 0.45, cy + s * 0.3);
    ctx.stroke();
    ctx.restore();
}

// The per-row add affordance (UI review #1): an icon-only square that opens that
// row's file picker. Deliberately NOT the toolbar-button treatment — a toolbar
// clone sitting on the media surface read wrong; this is a quiet well with an
// upload glyph. Drawn dimmed (not removed) at cap: a control that vanishes
// teaches nothing, a dimmed one shows the row is full (UI review #4).
function drawAddSquare(ctx, x, y, dimmed) {
    const S = CL.addBtn;
    ctx.save();
    if (dimmed) ctx.globalAlpha = 0.4;
    ctx.fillStyle = C.wellDeep;
    pathRoundRect(ctx, x + 0.5, y + 0.5, S - 1, S - 1, 6);
    ctx.fill();
    ctx.strokeStyle = C.raised;
    ctx.lineWidth = 1;
    pathRoundRect(ctx, x + 0.5, y + 0.5, S - 1, S - 1, 6);
    ctx.stroke();
    drawUploadGlyph(ctx, x + S / 2, y + S / 2, 18, C.textFaint);
    ctx.restore();
}

// The subject picker, painted inside one tile and hit-tested from the same cell list.
// Paints the picker and RETURNS its cells rather than pushing them itself.
//
// hitTest scans regions in reverse, so the last one pushed wins - and these cells sit
// inside the tile's own rect. Pushing them where they are painted put them BEFORE the
// tile, so every click on a number resolved to the tile instead and no subject could ever
// be set: the feature was completely unreachable. Returning them lets the caller push
// them last, which is the only order that works.
function drawSubjectPicker(ctx, ref, x, y, kind, index) {
    const T = CL.tile;
    const out = [];
    ctx.save();
    pathRoundRect(ctx, x, y, T, T, 4);
    ctx.clip();
    // Dark enough that white cell text reads over any thumbnail underneath.
    ctx.fillStyle = "rgba(0, 0, 0, 0.82)";
    ctx.fillRect(x, y, T, T);

    const on = new Set(ref.subjects || []);
    for (const cell of subjectCells(T, CL.subCell, CL.subGap)) {
        const cx = x + cell.x;
        const cy = y + cell.y;
        const active = cell.n === 0 ? on.size === 0 : on.has(cell.n);
        ctx.fillStyle = active ? C.text : "rgba(255, 255, 255, 0.08)";
        pathRoundRect(ctx, cx, cy, cell.w, cell.h, 3);
        ctx.fill();
        ctx.strokeStyle = active ? "#000" : "#555";
        ctx.lineWidth = 1;
        pathRoundRect(ctx, cx + 0.5, cy + 0.5, cell.w - 1, cell.h - 1, 3);
        ctx.stroke();
        if (cell.n) {
            ctx.fillStyle = active ? "#000" : C.textMuted;
            ctx.font = "bold 12px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(String(cell.n), cx + cell.w / 2, cy + cell.h / 2 + 4);
        }
        out.push({ type: "subject", kind, index, n: cell.n,
                   x: cx, y: cy, w: cell.w, h: cell.h });
    }
    ctx.restore();
    return out;
}

function draw(node) {
    const body = node._mmrpBody;
    if (!body) return;
    const canvas = body.canvas;
    const ctx = body.ctx;
    const cssW = canvas.clientWidth;
    // Not attached / zero-sized yet (V3 mounts the DOM widget asynchronously) —
    // the ResizeObserver fires once real dimensions arrive and repaints then.
    if (!cssW) return;

    // The slab's height follows its content, and its content depends on how many tiles
    // fit ACROSS — so the layout is derived from the width, and the height applied from
    // the layout. Assigning the style only when it actually differs is what stops the
    // ResizeObserver turning this into a repaint loop: one extra frame on the paint where
    // a section gains or loses a line, then stable.
    //
    // Note this does NOT call setSize. draw() has never been allowed to, because paint
    // recursing into layout is how the old resize-driven relayout bugs happened; the node
    // is resized by relayout() from the places that change the content instead.
    const layout = computeCanvasRows(node._mmrpRefs, cssW - CL.x0 * 2);
    node._mmrpLayout = layout;
    applyCanvasHeight(node, layout);

    const cssH = canvas.clientHeight;
    if (!cssH) return;

    // HiDPI: back the canvas at devicePixelRatio and scale the context so all
    // layout math (and hit regions) stay in CSS px while text/thumbs stay sharp.
    const dpr = window.devicePixelRatio || 1;
    const bw = Math.round(cssW * dpr);
    const bh = Math.round(cssH * dpr);
    if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    // The slab itself: the CSS keeps the element black; a subtle drawn edge marks
    // it off against the grey node body without the border-vs-content-box coordinate
    // mismatch a CSS border would introduce into hit-testing.
    ctx.strokeStyle = C.raised;
    ctx.lineWidth = 1;
    pathRoundRect(ctx, 0.5, 0.5, cssW - 1, cssH - 1, 6);
    ctx.stroke();

    const refs = node._mmrpRefs;
    const tagged = assignTags(refs);
    const viewW = cssW - CL.x0 * 2;
    const regions = [];
    node._mmrpHit = { regions };
    const playing = node._mmrpPlaying;
    const drag = node._mmrpDrag;
    const picker = node._mmrpPicker;

    // `layout` was computed above, from the width, before the canvas height was applied.
    // Everything below still paints in one pass with no scroll offset, because the slab
    // grows to fit rather than clipping.
    for (const row of layout.rows) {
        const kind = row.kind;
        const arr = refs[`${kind}s`];
        const taggedArr = tagged[`${kind}s`];
        const atCap = arr.length >= CAPS[kind];
        const perRow = row.perRow;
        const tileY = row.stripY + CL.stripPad;
        // Top-left of tile i, wrapping every perRow. The single source of tile geometry
        // in the file — hit regions below are all derived from these two, so a tile and
        // its own delete chip cannot end up on different lines.
        const tileXOf = (i) => CL.x0 + (i % perRow) * (CL.tile + CL.gap);
        const tileYOf = (i) => tileY + Math.floor(i / perRow) * (CL.tile + CL.gap);

        // Divider at the midpoint of the inter-section gap — per spec: a viewer
        // should never be unsure which section a tile belongs to.
        if (row.y > CL.padTop) {
            const dy = Math.round(row.y - CL.rowGap / 2) + 0.5;
            ctx.strokeStyle = "#2a2a2a";
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(CL.x0, dy);
            ctx.lineTo(CL.x0 + viewW, dy);
            ctx.stroke();
        }

        // The labels are the only wayfinding on the slab — #aaa/12px, not the
        // quietest text in the node (UI review #7). At cap they brighten further.
        ctx.fillStyle = atCap ? C.text : C.textMuted;
        ctx.font = "12px sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(`${SECTION_LABEL[kind]} (${arr.length}/${CAPS[kind]})`, CL.x0, row.y + 12);

        arr.forEach((ref, i) => {
            const tx = tileXOf(i);
            const ty = tileYOf(i);
            const t = taggedArr[i];
            // "vid_audio <Audio 1>" not bare "<Audio 1>": the tag is what MiniMax binds
            // and has to stay visible, but unlabelled it reads as a standalone audio ref.
            const lines = kind === "video" && t.audioTag
                ? [t.tag, `vid_audio ${t.audioTag}`]
                : [t.tag];
            // An edited reference states its edit on the badge: "2.00-6.50s · cropped".
            const summary = editSummary(ref);
            if (summary) lines.push(summary);
            const badgeH = [CL.badge1, CL.badge2, CL.badge3][lines.length - 1];
            const selected =
                node._mmrpSelected && node._mmrpSelected.kind === kind && node._mmrpSelected.index === i;

            let soundState = null;
            if (kind === "video") {
                const res = probeResults.has(ref.file) ? probeResults.get(ref.file) : undefined;
                soundState =
                    res === undefined
                        ? "pending"
                        : res === true
                          ? ref.use_soundtrack
                              ? "on"
                              : "off"
                          : res === false
                            ? "none"
                            : "unknown";
            }

            let playState = null;
            if (kind !== "image" && !ref.missing) {
                playState =
                    playing && playing.kind === kind && playing.file === ref.file ? "pause" : "play";
            }

            const dragging = drag && drag.active && drag.kind === kind && drag.index === i;
            if (dragging) ctx.globalAlpha = 0.35;
            const picking = picker && picker.kind === kind && picker.index === i;
            drawTile(ctx, kind, ref, lines, tx, ty, selected, soundState, badgeH,
                     picking ? null : playState);
            ctx.globalAlpha = 1;
            const pickerCells = picking
                ? drawSubjectPicker(ctx, ref, tx, ty, kind, i)
                : null;

            // Hit regions, most specific last — hitTest scans in reverse so the
            // play/delete/soundtrack/edit affordances win over the tile containing them.
            regions.push({ type: "tile", kind, index: i, file: ref.file, x: tx, y: ty, w: CL.tile, h: CL.tile });
            if (!ref.missing && !picking) {
                regions.push({
                    type: "edit",
                    kind,
                    index: i,
                    file: ref.file,
                    x: tx + 3,
                    y: ty + 3,
                    w: CL.del,
                    h: CL.del,
                });
            }
            if ((soundState === "on" || soundState === "off") && !picking) {
                regions.push({
                    type: "sound",
                    kind,
                    index: i,
                    file: ref.file,
                    x: tx + 3,
                    y: ty + CL.tile - badgeH - CL.sound - 3,
                    w: CL.sound,
                    h: CL.sound,
                });
            }
            if (!picking) regions.push({
                type: "del",
                kind,
                index: i,
                file: ref.file,
                x: tx + CL.tile - CL.del - 3,
                y: ty + 3,
                w: CL.del,
                h: CL.del,
            });
            // ...and neither is play, for the same reason. The 2px gutters between the
            // picker's cells cross the play glyph's 28x28 region at the tile's centre, so
            // a click landing between two numbers started a video playing UNDER the open
            // picker. The glyph is already suppressed while picking; its hit region has to
            // go with it.
            if (playState && !picking) {
                const cy = kind === "audio" ? ty + 80 : ty + 58;
                regions.push({
                    type: "play",
                    kind,
                    index: i,
                    file: ref.file,
                    x: tx + CL.tile / 2 - CL.playR,
                    y: cy - CL.playR,
                    w: CL.playR * 2,
                    h: CL.playR * 2,
                    tileX: tx,
                    tileY: ty,
                });
            }
            // LAST, so the cells beat the tile they are painted inside. The chips above
            // are skipped entirely while the picker is open: the overlay hides them, and a
            // control that is invisible but still clickable is how someone deletes a
            // reference they were trying to label.
            if (pickerCells) regions.push(...pickerCells);
        });

        // FIXED placement (UI review #2): always where the next tile would go —
        // the row's left edge when empty, after the last tile (plus the 24px
        // separating gap) otherwise. It never centres itself, so it never jumps;
        // it only ever moves rightwards as tiles are added. Vertically centred
        // on the strip. Drawn dimmed at cap, and only clickable below cap.
        // The insertion caret: a bright bar in the gap the tile would land in. Placed from
        // the same wrapping arithmetic the tiles are, so on a section that spans several
        // lines it lands on the right one rather than always on the first.
        if (drag && drag.active && drag.kind === kind && typeof drag.insertAt === "number") {
            const at = Math.min(drag.insertAt, arr.length);
            // For a drop at the very end, anchor to the RIGHT edge of the last tile;
            // otherwise to the gap before the tile currently at that index.
            const slot = at >= arr.length && arr.length ? arr.length - 1 : at;
            const col = slot % perRow;
            const line = Math.floor(slot / perRow);
            const atEnd = at >= arr.length && arr.length > 0;
            const cx = Math.round(
                CL.x0 + col * (CL.tile + CL.gap) + (atEnd ? CL.tile + CL.gap / 2 : -CL.gap / 2)
            ) + 0.5;
            const cy = tileY + line * (CL.tile + CL.gap);
            ctx.strokeStyle = C.danger;
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.moveTo(cx, cy - 2);
            ctx.lineTo(cx, cy + CL.tile + 2);
            ctx.stroke();
        }

        // Still "always where the next tile would go", which now means the end of the
        // LAST LINE rather than the end of the only one. tilesPerRow reserved room for
        // this square in a full line, so it can never be pushed off the right edge.
        const onLastLine = arr.length === 0
            ? 0
            : (arr.length % perRow === 0 ? perRow : arr.length % perRow);
        const ax = onLastLine
            ? CL.x0 + onLastLine * (CL.tile + CL.gap) - CL.gap + CL.addGap
            : CL.x0;
        const ay = tileY + Math.max(0, row.lines - 1) * (CL.tile + CL.gap)
            + Math.round((CL.tile - CL.addBtn) / 2);
        drawAddSquare(ctx, ax, ay, atCap);
        if (!atCap) {
            regions.push({ type: "add", kind, x: ax, y: ay, w: CL.addBtn, h: CL.addBtn });
        }
    }

    // The tile under the cursor, drawn last so it rides above every section. Half size,
    // because a full-size ghost hides the caret telling you where it will land.
    if (drag && drag.active) {
        const ref = node._mmrpRefs[`${drag.kind}s`][drag.index];
        const entry = ref && ref.file ? getThumb(ref.file, ref) : null;
        const g = Math.round(CL.tile / 2);
        const gx = drag.x - g / 2;
        const gy = drag.y - g / 2;
        ctx.save();
        ctx.globalAlpha = 0.85;
        pathRoundRect(ctx, gx, gy, g, g, 4);
        ctx.clip();
        ctx.fillStyle = C.well;
        ctx.fillRect(gx, gy, g, g);
        if (entry && entry.state === "ok") drawCover(ctx, entry.img, gx, gy, g, g);
        ctx.restore();
        ctx.strokeStyle = C.danger;
        ctx.lineWidth = 2;
        pathRoundRect(ctx, gx + 0.5, gy + 0.5, g - 1, g - 1, 4);
        ctx.stroke();
    }
}

// ---------------------------------------------------------------------------
// Canvas hit-testing. ComfyUI zooms the whole DOM widget with a CSS transform,
// so a client-space pixel is NOT a canvas-space pixel — map through the bounding
// rect scaled back by the element's own layout size (ltx_director.js getMousePos,
// :3900). Everything comes out in the same CSS-px space draw() painted in.
// ---------------------------------------------------------------------------

function getMousePos(canvas, e) {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return { x: -1, y: -1 };
    return {
        x: ((e.clientX - rect.left) * canvas.offsetWidth) / rect.width,
        y: ((e.clientY - rect.top) * canvas.offsetHeight) / rect.height,
    };
}

export function hitTest(node, x, y) {
    const hit = node._mmrpHit;
    if (!hit) return null;
    for (let i = hit.regions.length - 1; i >= 0; i--) {
        const r = hit.regions[i];
        if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) return r;
    }
    return null;
}

// A drag has to travel before it counts as one. Below this the gesture is still a click,
// which is what keeps "select a tile" working now that dragging one means something.
const DRAG_THRESHOLD = 4;

// >>> MMRP-DROP
// Where a drop at (x, y) would insert, as an index into that kind's array.
//
// Derived from the TILE HIT REGIONS rather than from x arithmetic, deliberately. The
// regions are whatever draw() actually painted, so this reads correctly whether a section
// is one row or several - there is no second copy of the layout maths to drift out of
// step with the first, and nothing here to revisit if the tiles start wrapping.
// Was the tile let go somewhere that means "never mind"?
//
// dropIndexAt answers just as confidently 900px away as 9px away - it has no notion of
// distance, and it should not: horizontal position inside a row is meaningful at ANY
// distance, because past the end of a row means the end of that row. So the abort cannot
// be expressed as "far from the tiles" without breaking the drop that aims at the empty
// space after the last one.
//
// What it is instead is "off the slab". Dragging a tile out of the node and releasing it
// on empty canvas is the natural way to change your mind, and before this it silently
// committed the reorder AND rewrote every <Picture N> in the prompt to match, with Escape
// as the only way out. Measured: released 900px right and 200px above the node, and the
// tile moved.
//
// Coordinates are canvas-relative (getMousePos divides through by offsetWidth/Height), so
// the slab is 0..viewW by 0..viewH. One tile of slack keeps a deliberate drop just past
// the edge working.
function droppedOffStrip(pos, viewW, viewH) {
    if (!Number.isFinite(viewW) || !Number.isFinite(viewH) || viewW <= 0 || viewH <= 0) {
        return false;   // no measurable slab: fall back to the old always-drop behaviour
    }
    const slack = CL.tile;
    return pos.x < -slack || pos.x > viewW + slack
        || pos.y < -slack || pos.y > viewH + slack;
}

function dropIndexAt(node, kind, x, y) {
    const regions = (node && node._mmrpHit && node._mmrpHit.regions) || [];
    const tiles = regions.filter((r) => r.type === "tile" && r.kind === kind);
    if (!tiles.length) return 0;
    for (const r of tiles) {
        if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) {
            // Past the midpoint means "after this one".
            return x < r.x + r.w / 2 ? r.index : r.index + 1;
        }
    }
    // Outside every tile. Pick the ROW first, then the nearest tile within it - a single
    // weighted distance does not work here. Weighting rows more heavily still let a tile
    // on the row ABOVE win once the cursor was far enough to the right of a short last
    // row: dropping in the empty space at the end of "5 images in rows of 3" returned
    // index 3 (the start of the last row) instead of 5 (the end of the list).
    const rowOf = (r) => Math.round(r.y);
    const rows = [...new Set(tiles.map(rowOf))];
    let row = rows[0];
    for (const candidate of rows) {
        const mid = candidate + CL.tile / 2;
        if (Math.abs(y - mid) < Math.abs(y - (row + CL.tile / 2))) row = candidate;
    }
    const onRow = tiles.filter((r) => rowOf(r) === row);
    let best = null;
    for (const r of onRow) {
        const d = Math.abs(x - (r.x + r.w / 2));
        if (!best || d < best.d) best = { d, index: r.index, after: x > r.x + r.w / 2 };
    }
    return best ? best.index + (best.after ? 1 : 0) : 0;
}
// <<< MMRP-DROP

function endDragListeners(node) {
    window.removeEventListener("mousemove", node._mmrpDragMove, true);
    window.removeEventListener("mouseup", node._mmrpDragUp, true);
    window.removeEventListener("keydown", node._mmrpDragKey, true);
}

function finishDrag(node, e) {
    const drag = node._mmrpDrag;
    node._mmrpDrag = null;
    endDragListeners(node);
    if (!drag) return;
    // Cancelled first: Escape on a drag that has not moved yet is still a cancel, not a
    // click. Checking `active` before this made it select the tile - and, with the subject
    // picker in play, open that too.
    if (drag.cancelled) {
        scheduleDraw(node);
        return;
    }
    if (!drag.active) {
        // Never crossed the threshold, so it was a click. Selecting still happens; the
        // click additionally toggles this tile's subject picker, which is the gesture
        // that makes grouping quick. Clicking a DIFFERENT tile moves the picker there
        // rather than closing it, so working along a row is one click per tile.
        node._mmrpSelected = { kind: drag.kind, index: drag.index };
        const open = node._mmrpPicker;
        node._mmrpPicker = (open && open.kind === drag.kind && open.index === drag.index)
            ? null
            : { kind: drag.kind, index: drag.index };
        if (node._mmrpPicker) stopPreview(node);   // the picker covers the play glyph
        scheduleDraw(node);
        return;
    }
    if (!e || drag.cancelled) {
        scheduleDraw(node);
        return;
    }
    const view = node._mmrpBody.canvas;
    const pos = getMousePos(view, e);
    // Released off the slab: an abort, exactly as Escape is.
    if (droppedOffStrip(pos, view.offsetWidth, view.offsetHeight)) {
        scheduleDraw(node);
        return;
    }
    const to = dropIndexAt(node, drag.kind, pos.x, pos.y);
    if (to === drag.index || to === drag.index + 1) {
        scheduleDraw(node);   // dropped back on itself
        return;
    }
    // Reordering renumbers the tags, which is exactly what the retag pass is for.
    node._mmrpPicker = null;   // same reason as removeRef: indices moved
    withRetag(node, () => {
        const refs = cloneRefs(node._mmrpRefs);
        refs[`${drag.kind}s`] = moveRef(refs[`${drag.kind}s`], drag.index, to);
        mlog("reference_moved", { kind: drag.kind, from: drag.index, to });
        return refs;
    });
    node._mmrpSelected = null;
    // Same reason onConfigure clears it: this replaces the whole reference set under a
    // picker that is pinned to an index.
    node._mmrpPicker = null;
}

function onDragMove(node, e) {
    const drag = node._mmrpDrag;
    if (!drag) return;
    const pos = getMousePos(node._mmrpBody.canvas, e);
    drag.x = pos.x;
    drag.y = pos.y;
    if (!drag.active) {
        const far = Math.abs(pos.x - drag.startX) > DRAG_THRESHOLD
            || Math.abs(pos.y - drag.startY) > DRAG_THRESHOLD;
        if (!far) return;
        drag.active = true;
        // The shared preview overlay is positioned off tile rects that are about to move
        // underneath it.
        stopPreview(node);
        node._mmrpSelected = null;
        node._mmrpPicker = null;
    }
    // null while off the slab: draw()'s gap indicator is gated on
    // `typeof insertAt === "number"`, so it vanishes and the abort is visible in advance.
    const view = node._mmrpBody.canvas;
    drag.insertAt = droppedOffStrip(pos, view.offsetWidth, view.offsetHeight)
        ? null
        : dropIndexAt(node, drag.kind, pos.x, pos.y);
    scheduleDraw(node);
}

function onCanvasMouseDown(node, e) {
    if (e.button !== 0) return;
    const pos = getMousePos(node._mmrpBody.canvas, e);
    const hit = hitTest(node, pos.x, pos.y);
    if (!hit) {
        if (node._mmrpSelected || node._mmrpPicker) {
            node._mmrpSelected = null;
            node._mmrpPicker = null;
            scheduleDraw(node);
        }
        return;
    }
    if (hit.type === "subject") {
        // MouseEvent.detail is the click ordinal, so `>= 2` is the second click of a
        // double-click. Skipping the toggle there is what lets double-click keep meaning
        // "insert the tag" on a tile whose picker is open, without a timer and without
        // trying to undo a toggle afterwards.
        //
        // Honest limit: when the picker was ALREADY open, the pair's first click is an
        // ordinary single click and its toggle stands. Nothing can know a second click is
        // coming without delaying the first, and a laggy toggle is worse than this.
        if (e.detail >= 2) return;
        // Cells TOGGLE and the picker stays open, so several subjects can be set in one
        // visit. Closing after each pick would make the multi-subject case - the reason
        // this is a set and not a single value - four gestures instead of one.
        const next = cloneRefs(node._mmrpRefs);
        const target = next[`${hit.kind}s`][hit.index];
        if (target) {
            target.subjects = toggleSubject(target.subjects, hit.n);
            if (!target.subjects.length) delete target.subjects;
            mlog("subjects", { kind: hit.kind, file: target.file, subjects: target.subjects || [] });
        }
        applyRefs(node, next);
    } else if (hit.type === "del") {
        removeRef(node, hit.kind, hit.index);
    } else if (hit.type === "sound") {
        toggleSoundtrack(node, hit.index);
    } else if (hit.type === "play") {
        togglePreview(node, hit);
    } else if (hit.type === "edit") {
        openEditModal(node, hit.kind, hit.index);
    } else if (hit.type === "add") {
        node._mmrpBody.fileInputs[hit.kind].click();
    } else {
        const ref = node._mmrpRefs[`${hit.kind}s`][hit.index];
        // A missing file has no thumbnail to drag and nothing worth reordering.
        if (!ref || ref.missing) {
            node._mmrpSelected = { kind: hit.kind, index: hit.index };
            scheduleDraw(node);
            return;
        }
        // Pending, not active: still a click until it moves. The listeners go on the
        // WINDOW so a drag that leaves the canvas still ends - releasing outside and
        // finding the tile stuck to the cursor is the worst version of this.
        node._mmrpDrag = { kind: hit.kind, index: hit.index, startX: pos.x, startY: pos.y,
                           x: pos.x, y: pos.y, active: false, cancelled: false };
        node._mmrpDragMove = (ev) => onDragMove(node, ev);
        node._mmrpDragUp = (ev) => finishDrag(node, ev);
        node._mmrpDragKey = (ev) => {
            if (ev.key !== "Escape" || !node._mmrpDrag) return;
            node._mmrpDrag.cancelled = true;
            finishDrag(node, null);
            ev.stopPropagation();
        };
        window.addEventListener("mousemove", node._mmrpDragMove, true);
        window.addEventListener("mouseup", node._mmrpDragUp, true);
        window.addEventListener("keydown", node._mmrpDragKey, true);
        // Otherwise litegraph starts dragging the NODE under the cursor.
        e.stopPropagation();
        e.preventDefault();
    }
}

// Put text at the caret in the prompt box, spacing it so the result reads as prose.
//
// The spacing is not politeness: the tags go to a model that is told to use them exactly,
// and "<Picture 1>wears" is a different token sequence from "<Picture 1> wears". Making
// the user remember the space is how you get the first one.
// >>> MMRP-INSERT
// The pure half: where the text lands and where the caret ends up. Split out from the
// DOM so tests/test_insert.py can run the real thing under node.
export function spliceTag(value, start, end, text) {
    const before = value.slice(0, start);
    const after = value.slice(end);
    const lead = before && !/\s$/.test(before) ? " " : "";
    const trail = after && !/^\s/.test(after) ? " " : "";
    const insert = `${lead}${text}${trail}`;
    return { value: before + insert + after, caret: start + insert.length };
}
// <<< MMRP-INSERT

function insertIntoDirection(node, text) {
    const el = node._mmrpBody && node._mmrpBody.directionInput;
    if (!el) return;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? start;
    const { value, caret } = spliceTag(el.value, start, end, text);
    el.value = value;
    el.setSelectionRange(caret, caret);
    el.focus();
    // Mirror into the hidden widget exactly the way the textarea's own input listener
    // does — setting .value programmatically does not fire `input`.
    const w = widgetByName(node, "direction");
    if (w) {
        w.value = el.value;
        if (w.callback) w.callback(w.value);
    }
    mlog("tag_inserted", { text });
}

// Double-clicking a TILE writes its tag into the prompt. Double-clicking the scissors
// chip still opens the crop/trim editor, and single-clicking that chip always did.
//
// This REASSIGNS the gesture: a double-click anywhere on a tile used to open the editor.
// The chip is the editor's affordance and is drawn on every tile, so the editor did not
// lose its way in — and the tag is the thing you reach for far more often, because it is
// what every sentence in the prompt has to name.
function onCanvasDblClick(node, e) {
    const pos = getMousePos(node._mmrpBody.canvas, e);
    const hit = hitTest(node, pos.x, pos.y);
    // The picker and the tag insert overlap on the same pixels: the first click of a
    // double-click opens the picker, so the second one lands on a CELL. Left alone that
    // means double-clicking the middle of a tile toggles a subject instead of inserting
    // its tag - trading one gesture for another rather than supporting both.
    //
    // So a double-click on a cell reverts the toggle its second click just applied and
    // goes on to insert the tag. Double-click means "insert" everywhere on the tile, and
    // a single click still means "toggle this subject".
    if (hit && hit.type === "subject") {
        // No revert here any more. It assumed the pair's FIRST click had opened the
        // picker, so only the second toggled; with the picker already open both clicks
        // toggle (netting zero) and the revert applied a third, silently changing the
        // grouping sent to the model. The second click is suppressed at source instead -
        // see the `detail` check in onCanvasMouseDown - so by the time this runs the pair
        // has toggled at most once and there is nothing to undo.
        const tagged = assignTags(node._mmrpRefs)[`${hit.kind}s`][hit.index];
        if (tagged) insertIntoDirection(node, tagged.tag);
        return;
    }
    if (!hit || (hit.type !== "tile" && hit.type !== "edit")) return;
    const ref = node._mmrpRefs[`${hit.kind}s`][hit.index];
    if (!ref || ref.missing) return;
    if (hit.type === "edit") {
        openEditModal(node, hit.kind, hit.index);
        return;
    }
    const tagged = assignTags(node._mmrpRefs)[`${hit.kind}s`][hit.index];
    // The video's own <Video N>, not its soundtrack's <Audio N>. One gesture has to pick
    // one, and the video tag is what a sentence about the clip needs; the soundtrack tag
    // is printed on the badge for the rarer case of writing about the sound alone.
    if (tagged) insertIntoDirection(node, tagged.tag);
}

// >>> MMRP-SOUND
// Which video a sound-chip click means, and what flipping it produces.
//
// Pure and marker-delimited so tests/test_sound.py can run it under node, because the
// interesting case is the one a mock would never catch: TWO references to the same file.
// Nothing prevents that - addFiles does no de-duplication, and an upload with an existing
// name overwrites on the server and comes back under that same name.
function flipSoundtrackAt(videos, index) {
    if (!Array.isArray(videos)) return null;
    if (!Number.isInteger(index) || index < 0 || index >= videos.length) return null;
    return videos.map((v, i) => (i === index ? { ...v, use_soundtrack: !v.use_soundtrack } : v));
}
// <<< MMRP-SOUND

// Addressed by INDEX, like every other tile action.
//
// Looking the video up by file NAME picks the first reference carrying that name, so
// clicking the sound chip on the second copy of a clip toggled the first one: the chip the
// user clicked did not change, and a different tile did. The hit region has carried
// `index` all along (see the "sound" region in draw), and the delete, edit and subject
// handlers beside this one all use it.
//
// Still inside withRetag: flipping a soundtrack renumbers every <Audio N>, because a
// video's audio claims its tag before standalone audio does.
function toggleSoundtrack(node, index) {
    const current = node._mmrpRefs.videos[index];
    if (!current) return;
    if (probeResults.get(current.file) !== true) return; // pending/silent/unknown
    withRetag(node, () => {
        const next = cloneRefs(node._mmrpRefs);
        const videos = flipSoundtrackAt(next.videos, index);
        if (!videos) return next;
        mlog("soundtrack", { file: current.file, index, on: videos[index].use_soundtrack });
        return { ...next, videos };
    });
}

// ---------------------------------------------------------------------------
// Click-to-play previews — ONE shared <video> overlay + ONE shared <audio> per
// node, never per-cell DOM. The rule: the drawn ▶ circle is the play hit region;
// clicking anywhere else on the tile selects. Any refs change stops the preview
// (tile positions shift, the overlay would sit on the wrong tile), as do 'ended',
// a second click, clicking the playing overlay itself, and onRemoved.
// ---------------------------------------------------------------------------

function stopPreview(node) {
    const body = node._mmrpBody;
    if (!body || !node._mmrpPlaying) return;
    body.previewVideo.pause();
    body.previewVideo.removeAttribute("src");
    body.previewVideo.style.display = "none";
    body.previewAudio.pause();
    body.previewAudio.removeAttribute("src");
    node._mmrpPlaying = null;
    scheduleDraw(node);
}

function togglePreview(node, hit) {
    const body = node._mmrpBody;
    const playing = node._mmrpPlaying;
    if (playing && playing.kind === hit.kind && playing.file === hit.file) {
        stopPreview(node);
        return;
    }
    stopPreview(node);
    // A trimmed reference previews ONLY its span: seek to the in-point here, stop at
    // the out-point in the shared elements' timeupdate handlers (buildCustomBlock).
    // Setting currentTime before metadata sets the default playback start position
    // (per the HTML spec), and the timeupdate handler re-seeks if a browser drops it.
    const ref = node._mmrpRefs[`${hit.kind}s`][hit.index];
    const trim = ref && Array.isArray(ref.trim) ? ref.trim : null;
    if (hit.kind === "video") {
        const v = body.previewVideo;
        v.src = fileUrl(hit.file);
        if (trim) v.currentTime = trim[0];
        // Same coordinate space as the canvas — the block is the offsetParent for
        // both, and ComfyUI's zoom transform applies to the overlay automatically.
        v.style.left = `${body.canvas.offsetLeft + hit.tileX}px`;
        v.style.top = `${body.canvas.offsetTop + hit.tileY}px`;
        v.style.display = "block";
        v.play().catch(() => stopPreview(node));
    } else {
        const a = body.previewAudio;
        a.src = fileUrl(hit.file);
        if (trim) a.currentTime = trim[0];
        a.play().catch(() => stopPreview(node));
    }
    node._mmrpPlaying = { kind: hit.kind, file: hit.file, trim };
    scheduleDraw(node);
}

// ---------------------------------------------------------------------------
// State <-> widgets
// ---------------------------------------------------------------------------

function widgetByName(node, name) {
    return (node.widgets || []).find((w) => w.name === name);
}

// Never let a malformed references_json abort workflow loading.
//
// ComfyUI restores widget values POSITIONALLY from widgets_values. Any change to the
// widget list — ours moved the DOM widget from first to last, and the Python side later
// added `system_prompt` — shifts that mapping for workflows saved by an older build, so
// this widget can come back holding a neighbour's value (a model id, an API key, a
// prompt). Parsing that threw a SyntaxError out of onNodeCreated/onConfigure and killed
// the entire workflow load, taking every other node with it.
//
// A reference set we cannot read is recoverable — the user re-adds the files. A workflow
// that will not open is not. So: parse defensively, fall back to empty, warn once.
function parseRefsValue(widget, node) {
    if (!widget) return emptyRefs();
    const raw = widget.value;
    if (typeof raw !== "string" || !raw.trim()) return emptyRefs();
    try {
        const parsed = JSON.parse(raw);
        // Two UI preferences ride in the envelope beside the references rather than in
        // widgets of their own: widgets_values is positional, so each would be another
        // slot to migrate forever for something that is not part of the reference set.
        // refs.py reads this object's `references` key and Python never writes the widget
        // back, so extra keys survive every round trip untouched.
        if (parsed && Number.isFinite(parsed.prompt_h)) {
            node._mmrpPromptH = parsed.prompt_h;
        }
        if (node && parsed && typeof parsed.retag === "boolean") node._mmrpRetag = parsed.retag;
        return fromReferencesList((parsed && parsed.references) || []);
    } catch (e) {
        console.warn(
            "[MiniMaxRefPack] references_json held a value this build cannot read " +
                "(likely a widget-order shift from an older saved workflow). Starting with " +
                "no references; re-add them and re-save. Raw value:",
            raw
        );
        return emptyRefs();
    }
}

function syncReferencesWidget(node) {
    const w = widgetByName(node, "references_json");
    if (!w) return;
    const flat = [...node._mmrpRefs.images, ...node._mmrpRefs.videos, ...node._mmrpRefs.audios];
    const list = toReferencesList(node._mmrpRefs).map((r, i) => {
        // carry the "missing" flag through without teaching refs.py about it: it's
        // stripped by fromReferencesList's kind switch on the Python side.
        const src = flat[i];
        return src && src.missing ? { ...r, missing: true } : r;
    });
    const envelope = { references: list };
    // Each is written ONLY once the user has moved it off the default, so a workflow that
    // touched neither stays byte-identical to what earlier builds wrote.
    if (Number.isFinite(node._mmrpPromptH)) envelope.prompt_h = node._mmrpPromptH;
    if (node._mmrpRetag === false) envelope.retag = false;
    w.value = JSON.stringify(envelope);
}

// ON by default: tags going stale is the bug, so not fixing them has to be the choice
// someone makes rather than the one they get.
function retagEnabled(node) {
    return node._mmrpRetag !== false;
}

// The prompt box is the one element whose height is a preference rather than a
// consequence, so it is the one element with an inline height.
function syncPromptHeight(node) {
    const el = node._mmrpBody && node._mmrpBody.directionInput;
    if (el) el.style.height = `${promptHeightOf(node)}px`;
}

// Writing references_json on every frame of a drag would spam the undo stack, so the
// height is persisted once the drag settles.
function schedulePersistPromptH(node) {
    clearTimeout(node._mmrpPromptHTimer);
    node._mmrpPromptHTimer = setTimeout(() => syncReferencesWidget(node), 250);
}

// The slab element's own height. Applied from the layout the moment the content changes,
// NOT only on the next paint: scheduleDraw goes through requestAnimationFrame, which a
// hidden tab suspends, so leaving this to draw() alone left the block sized for a wrapped
// layout while the canvas inside it was still the old height. Confirmed on frontend
// 1.51.9 with the tab backgrounded: the block was 831 tall around a 554 canvas.
//
// Assigning only on a real change is what keeps this out of a loop with the
// ResizeObserver watching the same element.
function applyCanvasHeight(node, layout) {
    const canvas = node._mmrpBody && node._mmrpBody.canvas;
    if (!canvas) return;
    const want = `${(layout || layoutOf(node)).height}px`;
    if (canvas.style.height !== want) canvas.style.height = want;
}

// Re-derive the node's size after something changed the CONTENT rather than the window.
// Kept out of draw() on purpose: paint recursing into layout is the bug class the
// fixed-size rewrite was built to kill, and it stays dead.
function relayout(node) {
    applyCanvasHeight(node);
    setSizeInternal(node);
    scheduleDraw(node);
    app.graph?.setDirtyCanvas(true, true);
}

function applyRefs(node, refs) {
    stopPreview(node); // tile positions shift with any change — never leave the overlay stale
    node._mmrpRefs = refs;
    syncReferencesWidget(node);
    renderNodeBody(node);
    // A reference that starts or ends a line changes the slab's height, and the node has
    // to follow it. This is the "adding media is a height change" half of the layout.
    relayout(node);
}

async function addFiles(node, kind, files) {
    // The clone is taken AFTER the uploads, not before.
    //
    // Every `await` below is a window in which another gesture finishes its own applyRefs.
    // Cloning at entry and writing that clone back afterwards silently reverted whatever
    // happened in between: delete a tile while an upload is in flight and it comes back;
    // toggle a soundtrack and the toggle is undone. Two drops a second apart is enough.
    //
    // Found by semmlerino in PR #2 against upstream, where it was diagnosed on the
    // drag-and-drop path; it is the same race on the file-picker path.
    const room = CAPS[kind] - node._mmrpRefs[`${kind}s`].length;
    if (room <= 0) return;
    const all = Array.from(files);
    const take = all.slice(0, room);
    // Caps are hard, but never SILENTLY hard — say what didn't fit.
    if (take.length < all.length) {
        alert(`Only ${room} ${kind} slot${room === 1 ? "" : "s"} left — skipped ${all.length - take.length} file(s).`);
    }
    mlog("upload", { kind, files: take.length });
    const uploadStarted = performance.now();
    const uploaded = [];
    for (const file of take) {
        try {
            const name = await apiUpload(file);
            mlog("uploaded", { kind, file: name, bytes: file.size });
            uploaded.push(name);
        } catch (e) {
            mwarn("upload_failed", { kind, file: file.name, error: e.message });
            alert(`Upload failed for ${file.name}: ${e.message}`);
        }
    }
    mlog("upload_done", { kind, files: take.length, ms: performance.now() - uploadStarted });
    if (!uploaded.length) return;
    // Adding a reference is NOT always an append as far as the tags are concerned. A
    // video's soundtrack claims its <Audio N> before all standalone audio, so uploading
    // one clip shifts every standalone <Audio> up by one - and a prompt that said
    // "<Audio 1>" about the user's music now means the new video's soundtrack. Images
    // and standalone audio really do just append, and the retag pass is a no-op for them.
    withRetag(node, () => {
        const refs = cloneRefs(node._mmrpRefs);
        const arr = refs[`${kind}s`];
        for (const name of uploaded) {
            // The cap is re-checked here, not just at entry: the set may have grown while
            // these files were uploading.
            if (arr.length >= CAPS[kind]) break;
            // soundtrack ON by default; the probe turns it back off for a silent clip
            arr.push(kind === "video" ? { file: name, use_soundtrack: true } : { file: name });
        }
        return refs;
    });
}

function removeRef(node, kind, index) {
    // The picker is pinned to an index and delete shifts them, so it would end up open on
    // a different reference than the one the user had chosen. Closing is the honest move.
    node._mmrpPicker = null;
    if (node._mmrpSelected && node._mmrpSelected.kind === kind) {
        if (node._mmrpSelected.index === index) node._mmrpSelected = null;
        else if (node._mmrpSelected.index > index) node._mmrpSelected.index -= 1;
    }
    withRetag(node, () => {
        const refs = cloneRefs(node._mmrpRefs);
        const [gone] = refs[`${kind}s`].splice(index, 1);
        mlog("reference_removed", { kind, file: gone && gone.file });
        return refs;
    });
}

// The DOM-side reaction to a state change: button disabled states, probes, repaint.
// No sizing here — the geometry is fixed, only the canvas's CONTENTS change.
function renderNodeBody(node) {
    const body = node._mmrpBody;
    if (!body) return;
    const refs = node._mmrpRefs;
    for (const kind of KINDS) {
        body.uploadButtons[kind].disabled = refs[`${kind}s`].length >= CAPS[kind];
    }
    syncProbes(node);
    scheduleDraw(node);
}

// ---------------------------------------------------------------------------
// Custom block: upload row (DOM buttons), the canvas, prompt textarea
// ---------------------------------------------------------------------------

// Monochrome upload-arrow icon (stroke: currentColor, so it inherits the button's
// white) — one family across all three upload buttons, per the sketch:
// [⬆ Image] [⬆ Video] [⬆ Audio]. The icon carries "upload", the label carries the
// kind, which is what lets the buttons shrink horizontally.
const UPLOAD_ICON =
    '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M8 10.5V3M4.5 6 8 2.5 11.5 6M2.5 12.5v1a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-1"/></svg>';

const KIND_UPLOAD_META = [
    ["image", "Image", "image/*"],
    ["video", "Video", "video/*"],
    ["audio", "Audio", "audio/*"],
];

function buildCustomBlock(node) {
    const container = document.createElement("div");
    container.className = "mmrp-block";

    const uploadRow = document.createElement("div");
    uploadRow.className = "mmrp-uploads";
    const uploadButtons = {};
    const fileInputs = {};
    for (const [kind, label, accept] of KIND_UPLOAD_META) {
        const btn = document.createElement("button");
        btn.className = "mmrp-btn mmrp-upload-btn";
        btn.innerHTML = `${UPLOAD_ICON}<span>${label}</span>`;
        const input = document.createElement("input");
        input.type = "file";
        input.accept = accept;
        input.multiple = true;
        input.style.display = "none";
        input.onchange = async (e) => {
            await addFiles(node, kind, e.target.files);
            input.value = "";
        };
        btn.onclick = () => input.click();
        uploadRow.appendChild(btn);
        uploadRow.appendChild(input);
        uploadButtons[kind] = btn;
        fileInputs[kind] = input; // the per-row "+" add slots click these too
    }

    const saveConfigBtn = document.createElement("button");
    saveConfigBtn.className = "mmrp-btn mmrp-upload-btn";
    saveConfigBtn.textContent = "Save config";
    saveConfigBtn.onclick = () => saveConfig(node, saveConfigBtn);
    uploadRow.appendChild(saveConfigBtn);

    const loadConfigBtn = document.createElement("button");
    loadConfigBtn.className = "mmrp-btn mmrp-upload-btn";
    loadConfigBtn.textContent = "Load config";
    loadConfigBtn.onclick = () => openLoadConfigPanel(node, loadConfigBtn);
    uploadRow.appendChild(loadConfigBtn);

    const localBtn = document.createElement("button");
    localBtn.className = "mmrp-btn mmrp-upload-btn mmrp-local-btn";
    localBtn.textContent = "Local LLM";
    localBtn.title = "Find an OpenAI-compatible server on this machine and use it to write prompts";
    localBtn.onclick = () => openLocalServerModal(node, localBtn);
    uploadRow.appendChild(localBtn);
    // Only meaningful while prompt_provider is `local`, so it is hidden otherwise rather
    // than sitting there inert. The row is nowrap and content-sized, so removing it from
    // layout just shortens the group; the node's fixed height is unaffected.
    node._mmrpLocalBtn = localBtn;
    syncLocalBtn(node);

    const gearBtn = document.createElement("button");
    gearBtn.className = "mmrp-btn mmrp-gear-btn";
    gearBtn.textContent = "⚙";
    gearBtn.title = "System prompt settings";
    gearBtn.onclick = () => openSystemPromptModal(node);
    uploadRow.appendChild(gearBtn);

    container.appendChild(uploadRow);
    // uploadRow goes onto _mmrpBody below: minNodeWidth() measures its natural width,
    // and that is what sets the floor the node may be dragged to.

    const canvas = document.createElement("canvas");
    canvas.className = "mmrp-canvas";
    // Height is applied per paint from the computed layout (see draw). Seeded here
    // only so the element has a box before the first paint measures it.
    canvas.style.height = `${computeCanvasRows(emptyRefs(), 600).height}px`;
    canvas.addEventListener("mousedown", (e) => onCanvasMouseDown(node, e));
    canvas.addEventListener("dblclick", (e) => onCanvasDblClick(node, e));
    // Keep litegraph's node context menu off the tiles (matches the reference's
    // per-item contextmenu swallow).
    canvas.addEventListener("contextmenu", (e) => e.stopPropagation());
    container.appendChild(canvas);

    // Fires exactly once in practice — when V3 asynchronously mounts the block and
    // the canvas goes 0 -> real size. That first fire is the initial paint trigger;
    // the geometry never changes afterwards.
    const resizeObserver = new ResizeObserver(() => scheduleDraw(node));
    resizeObserver.observe(canvas);

    // The shared preview pair (see togglePreview). Children of the block so the
    // canvas and the overlay share an offsetParent and a zoom transform.
    const previewVideo = document.createElement("video");
    previewVideo.className = "mmrp-preview";
    previewVideo.playsInline = true;
    previewVideo.addEventListener("ended", () => stopPreview(node));
    previewVideo.addEventListener("click", () => stopPreview(node));
    container.appendChild(previewVideo);

    const previewAudio = document.createElement("audio");
    previewAudio.addEventListener("ended", () => stopPreview(node));
    container.appendChild(previewAudio);

    // Trim enforcement for the shared preview pair: stop at the out-point, and
    // re-seek to the in-point if the pre-metadata seek in togglePreview was dropped.
    const keepToTrim = (el) => () => {
        const p = node._mmrpPlaying;
        if (!p || !p.trim) return;
        if (el.currentTime >= p.trim[1]) stopPreview(node);
        else if (el.currentTime < p.trim[0] - 0.25) el.currentTime = p.trim[0];
    };
    previewVideo.addEventListener("timeupdate", keepToTrim(previewVideo));
    previewAudio.addEventListener("timeupdate", keepToTrim(previewAudio));

    // Per spec: "just a large text area" — one plain DOM textarea on the grey
    // node body, no wrapper, no overlay label, no unfocused dimming (the dimming
    // plus a low-contrast border is exactly what once made this box invisible).
    const directionInput = document.createElement("textarea");
    directionInput.className = "mmrp-direction";
    directionInput.placeholder = "Prompt — describe the shot…";
    directionInput.spellcheck = false;
    container.appendChild(directionInput);

    node._mmrpBody = {
        root: container,
        uploadRow,
        uploadButtons,
        fileInputs,
        canvas,
        ctx: canvas.getContext("2d"),
        directionInput,
        previewVideo,
        previewAudio,
        resizeObserver,
    };
    return container;
}

// ---------------------------------------------------------------------------
// Config (reference pack) save/load
// ---------------------------------------------------------------------------

// Configs are FILES ON THE USER'S MACHINE, not server state. Save downloads a .json through
// the browser; Load reads one back through a file picker. The point is portability
// across deployments: a pod can be rebuilt or swapped and the config still opens.
// Nothing here touches /minimax_refpack/packs — the only server call is the input-dir
// listing used to work out which referenced files are actually present on THIS pod.

function configFilename(name) {
    const safe = (name || "config").replace(/[^A-Za-z0-9 ._-]+/g, "_").trim() || "config";
    return `minimax-refpack-${safe}.json`;
}

function buildConfig(node, name) {
    const w = (n) => {
        const widget = widgetByName(node, n);
        return widget ? widget.value || "" : "";
    };
    return {
        version: 1,
        name,
        direction: w("direction"),
        // The widget is `openrouter_model`. It was renamed from `model` in 0.3.3 and this
        // was not, so every config written since has carried `model: ""` and the model
        // choice has simply not been saved. Still WRITTEN under the old key, so a config
        // made here can be loaded by an older build.
        model: w("openrouter_model"),
        reasoning_effort: w("reasoning_effort"),
        references: toReferencesList(node._mmrpRefs),
    };
}

function downloadJson(filename, obj) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    // Must be in the document for the click to count as user-initiated in Firefox.
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revoke on the next tick, not synchronously: Safari aborts a download whose
    // object URL is revoked before it has started reading the blob.
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

function saveConfig(node, anchorBtn) {
    document.querySelectorAll(".mmrp-pack-panel").forEach((p) => p.remove());

    const panel = document.createElement("div");
    panel.className = "mmrp-pack-panel mmrp-save-panel";

    const input = document.createElement("input");
    input.className = "mmrp-save-name";
    input.type = "text";
    input.placeholder = "Config name";
    input.spellcheck = false;
    panel.appendChild(input);

    const saveBtn = document.createElement("button");
    saveBtn.className = "mmrp-btn mmrp-btn-primary";
    saveBtn.textContent = "Download";
    panel.appendChild(saveBtn);

    const status = document.createElement("div");
    status.className = "mmrp-save-status";
    panel.appendChild(status);

    const doSave = () => {
        const name = input.value.trim();
        if (!name) {
            status.textContent = "Name it first.";
            return;
        }
        try {
            const filename = configFilename(name);
            downloadJson(filename, buildConfig(node, name));
            status.textContent = `Downloaded ${filename}`;
            input.disabled = true;
            saveBtn.disabled = true;
            setTimeout(() => panel.remove(), 2000);
        } catch (e) {
            status.textContent = `Download failed: ${e.message}`;
        }
    };
    saveBtn.onclick = doSave;
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") doSave();
        else if (e.key === "Escape") panel.remove();
        // Keep graph-level hotkeys (node delete etc.) away from typing.
        e.stopPropagation();
    });

    anchorBtn.parentElement.appendChild(panel);
    input.focus();

    const closeOnOutside = (e) => {
        if (panel.contains(e.target) || e.target === anchorBtn) return;
        panel.remove();
        document.removeEventListener("mousedown", closeOnOutside, true);
    };
    setTimeout(() => document.addEventListener("mousedown", closeOnOutside, true), 0);
}

// The SECOND (and last) JSON.parse in this file. The other is parseRefsValue, which
// guards a widget value; this one guards a file the user picked. Both are wrapped
// because both parse bytes we did not write — an unguarded throw here would surface
// as a dead button, which is the exact class of bug this section already had once.
function parseConfigFile(text) {
    let data;
    try {
        data = JSON.parse(text);
    } catch (e) {
        throw new Error("not valid JSON");
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
        throw new Error("not a config object");
    }
    if (!Array.isArray(data.references)) {
        throw new Error("no references list");
    }
    return data;
}

// Which of these filenames actually exist in THIS pod's input dir. Uses the listing
// route the upload pickers already use, one call per kind that appears in the config.
async function missingFiles(references) {
    const kinds = [...new Set(references.map((r) => r.kind))];
    const present = new Set();
    await Promise.all(
        kinds.map(async (kind) => {
            try {
                const res = await fetch(`/minimax_refpack/files?kind=${encodeURIComponent(kind)}`);
                if (!res.ok) return;
                const data = await res.json();
                for (const f of data.files || []) present.add(f);
            } catch (e) {
                // Unreachable listing: report nothing missing rather than flagging
                // every tile red on a transient network blip.
            }
        }),
    );
    if (!present.size) return new Set();
    return new Set(references.map((r) => r.file).filter((f) => !present.has(f)));
}

async function applyConfig(node, data, sourceLabel) {
    const missing = await missingFiles(data.references);
    const list = data.references.map((r) => ({ ...r, missing: missing.has(r.file) }));
    node._mmrpSelected = null;
    applyRefs(node, fromReferencesList(list));

    const directionWidget = widgetByName(node, "direction");
    if (directionWidget && typeof data.direction === "string") {
        directionWidget.value = data.direction;
        if (node._mmrpBody) node._mmrpBody.directionInput.value = data.direction;
    }

    // A config written before these fields existed carries "" — leave the widget alone
    // rather than blanking a valid setting. A model no longer in the dropdown is
    // skipped too: setting a value litegraph cannot offer would show a combo
    // displaying something the user can never pick again.
    const restore = (widgetName, value) => {
        if (typeof value !== "string" || !value) return null;
        const w = widgetByName(node, widgetName);
        if (!w) return null;
        const options = w.options && w.options.values;
        if (Array.isArray(options) && !options.includes(value)) return value;
        w.value = value;
        return null;
    };
    const unavailable = [
        // Reads the file's `model` key - its name in the config format, unchanged for
        // compatibility - into the widget that actually holds it. Loading pointed at a
        // widget called `model`, which has not existed since 0.3.3: restore() found
        // nothing, returned null, and the model was silently not restored AND not
        // reported as unavailable, because an empty value returns early before the
        // lookup. README promises Load restores the model.
        restore("openrouter_model", data.model),
        restore("reasoning_effort", data.reasoning_effort),
    ].filter(Boolean);

    const problems = [];
    if (missing.size) problems.push(`not on this pod: ${[...missing].join(", ")}`);
    if (unavailable.length) problems.push(`unavailable setting(s): ${unavailable.join(", ")}`);
    if (problems.length) alert(`${sourceLabel} — ${problems.join("; ")}`);
}

function openLoadConfigPanel(node, anchorBtn) {
    document.querySelectorAll(".mmrp-pack-panel").forEach((p) => p.remove());

    const picker = document.createElement("input");
    picker.type = "file";
    picker.accept = ".json,application/json";
    picker.style.display = "none";
    picker.onchange = async () => {
        const file = picker.files && picker.files[0];
        picker.remove();
        if (!file) return;
        try {
            const data = parseConfigFile(await file.text());
            await applyConfig(node, data, file.name);
        } catch (e) {
            alert(`Couldn't load ${file.name}: ${e.message}`);
        }
    };
    document.body.appendChild(picker);
    picker.click();
}

// ---------------------------------------------------------------------------
// Crop/trim editor — ONE modal for both edits, same overlay pattern as the system
// prompt modal. Crop lives on image and video, trim on video and audio; the modal
// shows whichever applies. Everything is edited in fraction/second space and only
// committed on Save; Cancel (or clicking the backdrop) discards.
// ---------------------------------------------------------------------------

const ASPECT_PRESETS = [
    ["1:1", 1],
    ["16:9", 16 / 9],
    ["9:16", 9 / 16],
];

function openEditModal(node, kind, index) {
    const ref = node._mmrpRefs[`${kind}s`][index];
    if (!ref || ref.missing) return;
    mlog("edit_open", { kind, file: ref.file, crop: ref.crop, trim: ref.trim });
    const wantsCrop = kind !== "audio";
    const wantsTrim = kind !== "image";

    let crop = Array.isArray(ref.crop) ? ref.crop.slice() : [0, 0, 1, 1];
    let rotate = Number.isFinite(ref.rotate) ? ref.rotate : 0;
    let flip = ref.flip || null;
    let expand = ref.rotate_expand !== false;
    let clearRotateBtn = null;
    let syncAngleRef = () => {};
    let trim = Array.isArray(ref.trim) ? ref.trim.slice() : null; // null until duration known
    let duration = null;
    let ratio = null; // aspect lock; null = free
    let mediaW = 0;
    let mediaH = 0;
    const r2 = (v) => Math.round(v * 100) / 100;

    // The two Clear buttons dim when there is nothing to clear, so the modal shows
    // at a glance whether this reference currently carries an edit. Live state, the
    // same predicates Save uses — dragging a rect out enables Clear crop instantly.
    let clearCropBtn = null;
    let clearTrimBtn = null;
    const syncClears = () => {
        if (clearCropBtn) clearCropBtn.disabled = normalizeCrop(crop) === null;
        if (clearTrimBtn) clearTrimBtn.disabled = normalizeTrim(trim, duration) === null;
        if (clearRotateBtn) clearRotateBtn.disabled = !rotate && !flip;
    };

    // Show the orientation by transforming the MEDIA inside its wrapper. The crop layer
    // is a sibling of the wrapper, not a child, so the rect stays axis-aligned in the
    // rotated frame's coordinates - which is exactly the space refs.py stores it in and
    // media.py applies it in.
    const applyOrientation = () => {
        if (!mediaWrap) return;
        const parts = [];
        if (rotate) parts.push(`rotate(${rotate}deg)`);
        if (flip) parts.push(`scale(${flip.includes("h") ? -1 : 1}, ${flip.includes("v") ? -1 : 1})`);
        mediaWrap.style.transform = parts.join(" ");
        // A quarter turn swaps the media's aspect inside a wrapper sized for the other
        // one; scaling it to fit keeps the whole frame visible rather than letting the
        // long edge overflow the stage.
        const quarter = Math.abs((rotate % 180) - 90) < 1e-6;
        if (quarter && mediaW && mediaH) {
            const fit = Math.min(1, mediaH / mediaW, mediaW / mediaH) || 1;
            mediaWrap.style.transform += ` scale(${fit})`;
        }
        // "Fit inside" clips the overhang to the source's extent, which is what the
        // server does with rotate_expand: false. Showing the overhang while the node
        // would drop it is the same class of lie as cropping before rotating.
        mediaWrap.style.clipPath = (!expand && rotate) ? "inset(0)" : "";
    };

    const overlay = document.createElement("div");
    overlay.className = "mmrp-overlay";
    dismissOnBackdrop(overlay);

    const modal = document.createElement("div");
    modal.className = "mmrp-modal mmrp-edit-modal";
    overlay.appendChild(modal);

    const header = document.createElement("div");
    header.className = "mmrp-modal-header";
    header.textContent = `Edit — ${ref.file}`;
    modal.appendChild(header);

    // ---- the media, real pixels via the stock /view route ----
    const stage = document.createElement("div");
    stage.className = "mmrp-edit-stage";
    let media;
    if (kind === "image") {
        media = document.createElement("img");
    } else if (kind === "video") {
        media = document.createElement("video");
        media.muted = true;
        media.playsInline = true;
        media.preload = "auto";
    } else {
        media = document.createElement("audio");
        media.controls = true;
        media.preload = "metadata";
    }
    media.src = fileUrl(ref.file);
    // The media lives in a wrapper so "Play edit" can reframe it (the wrapper becomes
    // the crop's window; the media is scaled and shifted inside it). Untouched, the
    // wrapper shrink-wraps the media and nothing about the layout changes.
    const mediaWrap = document.createElement("div");
    mediaWrap.className = "mmrp-edit-media-wrap";
    // A reference that already carries an orientation must open showing it, or the first
    // thing the modal does is misrepresent the reference.
    setTimeout(() => { applyOrientation(); syncAngleRef(); }, 0);
    mediaWrap.appendChild(media);
    stage.appendChild(mediaWrap);
    modal.appendChild(stage);

    let cropLayer = null;   // set below when the media can be cropped

    // ---- crop rect + corner handles (image/video) ----
    let syncCropRect = () => {};
    if (wantsCrop) {
        const layer = document.createElement("div");
        layer.className = "mmrp-crop-layer";
        const rectEl = document.createElement("div");
        rectEl.className = "mmrp-crop-rect";
        layer.appendChild(rectEl);
        const handles = {};
        for (const corner of ["nw", "ne", "sw", "se"]) {
            const h = document.createElement("div");
            h.className = `mmrp-crop-handle mmrp-crop-${corner}`;
            rectEl.appendChild(h);
            handles[corner] = h;
        }
        stage.appendChild(layer);
        cropLayer = layer;

        syncCropRect = () => {
            rectEl.style.left = `${crop[0] * 100}%`;
            rectEl.style.top = `${crop[1] * 100}%`;
            rectEl.style.width = `${crop[2] * 100}%`;
            rectEl.style.height = `${crop[3] * 100}%`;
            syncClears();
        };
        syncCropRect();

        // Pointer deltas in fractions of the layer box; dragCrop does the math.
        const startDrag = (e, mode) => {
            stopPlayback();
            e.preventDefault();
            e.stopPropagation();
            const box = layer.getBoundingClientRect();
            if (!box.width || !box.height) return;
            const from = { x: e.clientX, y: e.clientY, crop: crop.slice() };
            const move = (ev) => {
                const dx = (ev.clientX - from.x) / box.width;
                const dy = (ev.clientY - from.y) / box.height;
                crop = dragCrop(from.crop, mode, dx, dy, ratio, mediaW, mediaH);
                syncCropRect();
            };
            const up = () => {
                window.removeEventListener("mousemove", move);
                window.removeEventListener("mouseup", up);
            };
            window.addEventListener("mousemove", move);
            window.addEventListener("mouseup", up);
        };
        rectEl.addEventListener("mousedown", (e) => startDrag(e, "move"));
        for (const corner of ["nw", "ne", "sw", "se"]) {
            handles[corner].addEventListener("mousedown", (e) => startDrag(e, corner));
        }
    }

    // ---- aspect presets (crop only): free / the node's width:height / fixed ratios ----
    if (wantsCrop) {
        const row = document.createElement("div");
        row.className = "mmrp-aspect-row";
        const label = document.createElement("span");
        label.className = "mmrp-edit-label";
        label.textContent = "Crop";
        row.appendChild(label);

        const presets = [["Free", null]];
        const wWidget = widgetByName(node, "width");
        const hWidget = widgetByName(node, "height");
        const nodeW = wWidget ? Number(wWidget.value) : 0;
        const nodeH = hWidget ? Number(hWidget.value) : 0;
        if (nodeW > 0 && nodeH > 0) presets.push([`${nodeW}:${nodeH}`, nodeW / nodeH]);
        presets.push(...ASPECT_PRESETS);

        const buttons = [];
        for (const [text, r] of presets) {
            const btn = document.createElement("button");
            btn.className = "mmrp-btn";
            btn.textContent = text;
            btn.onclick = () => {
                stopPlayback();
                ratio = r;
                for (const b of buttons) b.classList.toggle("mmrp-active", b === btn);
                if (r) {
                    crop = setRectAspect(crop, r, mediaW, mediaH);
                    syncCropRect();
                }
            };
            row.appendChild(btn);
            buttons.push(btn);
        }
        // Nothing is highlighted on open. Free IS the starting behaviour (ratio = null),
        // but showing it selected reads as a choice the user made, and then "Clear crop"
        // has nothing left to visibly clear. The highlight means "you picked a lock".

        // Right-aligned, paired with the trim row's Clear trim: back to the whole
        // frame, aspect lock released and every preset unhighlighted. Save then deletes
        // the saved crop, exactly like a hand-dragged full-frame rect would
        // (normalizeCrop -> null).
        clearCropBtn = document.createElement("button");
        clearCropBtn.className = "mmrp-btn mmrp-clear-btn";
        clearCropBtn.textContent = "Clear crop";
        clearCropBtn.onclick = () => {
            stopPlayback();
            mlog("edit_cleared", { file: ref.file, what: "crop" });
            crop = [0, 0, 1, 1];
            ratio = null;
            for (const b of buttons) b.classList.remove("mmrp-active");
            syncCropRect();
        };
        row.appendChild(clearCropBtn);
        modal.appendChild(row);

        // ---- orientation: quarter turns and mirrors ----
        //
        // Above the crop rect in the stage, and applied to the PREVIEW by the same CSS
        // transform, so the rect the user drags is drawn on the oriented frame - which is
        // the frame media.py crops. Getting that pairing wrong is the documented failure:
        // the preview shows one region and the node emits another.
        const orow = document.createElement("div");
        orow.className = "mmrp-edit-row";
        const olabel = document.createElement("span");
        olabel.className = "mmrp-edit-label";
        olabel.textContent = "Rotate";
        orow.appendChild(olabel);

        const turn = (delta) => {
            stopPlayback();
            rotate = (((rotate + delta) % 360) + 360) % 360;
            // A crop drawn on the old orientation does not survive a quarter turn: its
            // fractions mean something different once the axes swap. Rotating the RECT
            // with the frame keeps the same pixels selected, which is what the user means
            // by "turn it", and it is exact for a quarter turn.
            crop = rotateCropRect(crop, delta);
            applyOrientation();
            syncAngleRef();
            syncCropRect();
            syncClears();
        };
        for (const [glyph, delta, title] of [["↺", -90, "Rotate 90° anticlockwise"],
                                             ["↻", 90, "Rotate 90° clockwise"]]) {
            const b = document.createElement("button");
            b.className = "mmrp-btn mmrp-aspect-btn";
            b.textContent = glyph;
            b.title = title;
            b.onclick = () => turn(delta);
            orow.appendChild(b);
        }
        for (const [glyph, axis, title] of [["↔", "h", "Mirror left-right"],
                                            ["↕", "v", "Mirror top-bottom"]]) {
            const b = document.createElement("button");
            b.className = "mmrp-btn mmrp-aspect-btn";
            b.textContent = glyph;
            b.title = title;
            b.onclick = () => {
                stopPlayback();
                // `axis` is what the user SEES; flip is stored in the source frame,
                // and those differ by a transpose at 90 and 270. The rect stays on the
                // clicked axis because the crop is applied after the rotation.
                flip = toggleFlipAxis(flip, sourceFlipAxis(axis, rotate));
                crop = mirrorCropRect(crop, axis);
                applyOrientation();
                syncCropRect();
                syncClears();
            };
            orow.appendChild(b);
        }

        // Free angle. The quarter-turn buttons stay: they are two clicks for the case
        // that is both common and lossless, and dragging to exactly 90 is fiddly.
        const angle = document.createElement("input");
        angle.type = "range";
        angle.className = "mmrp-angle";
        angle.min = "-180";
        angle.max = "180";
        angle.step = "0.5";
        angle.title = "Free rotation. Snaps to the quarter turns, which are lossless.";
        const angleOut = document.createElement("span");
        angleOut.className = "mmrp-angle-out";
        const syncAngle = () => {
            angleOut.textContent = `${rotate ? (rotate > 180 ? rotate - 360 : rotate).toFixed(1) : "0.0"}°`;
            const shown = rotate > 180 ? rotate - 360 : rotate;
            if (Number(angle.value) !== shown) angle.value = String(shown);
            // Only a free angle can spill outside the source, so the fit toggle is dead
            // weight on a quarter turn and says so rather than sitting there inert.
            const free = !!rotate && Math.abs((rotate % 90)) > 1e-6;
            fitBox.disabled = !free;
            fitRow.classList.toggle("mmrp-dim", !free);
        };
        angle.oninput = () => {
            stopPlayback();
            // The crop rect is NOT rotated with a free angle. A quarter turn maps the
            // rect exactly; an arbitrary angle does not - the rotated rect is no longer
            // axis-aligned, and silently substituting its bounding box would quietly
            // select pixels the user never chose. The rect stays where it is, in the
            // rotated frame, which is where the editor draws it.
            rotate = snapAngle(angle.value);
            applyOrientation();
            syncAngle();
            syncClears();
        };
        syncAngleRef = syncAngle;
        orow.appendChild(angle);
        orow.appendChild(angleOut);

        const fitRow = document.createElement("label");
        fitRow.className = "mmrp-fit";
        const fitBox = document.createElement("input");
        fitBox.type = "checkbox";
        fitBox.checked = ref.rotate_expand === false;
        fitBox.onchange = () => {
            stopPlayback();
            expand = !fitBox.checked;
            applyOrientation();
        };
        fitRow.appendChild(fitBox);
        fitRow.appendChild(document.createTextNode(" Fit inside"));
        fitRow.title =
            "Off: keep the whole rotated frame and fill the corners black. " +
            "On: bind the result to the source's extent, cropping the overhang.";
        orow.appendChild(fitRow);

        clearRotateBtn = document.createElement("button");
        clearRotateBtn.className = "mmrp-btn mmrp-clear-btn";
        clearRotateBtn.textContent = "Clear rotation";
        clearRotateBtn.onclick = () => {
            stopPlayback();
            mlog("edit_cleared", { file: ref.file, what: "rotation" });
            rotate = 0;
            flip = null;
            expand = true;
            applyOrientation();
            syncAngleRef();
            syncCropRect();
            syncClears();
        };
        orow.appendChild(clearRotateBtn);
        modal.appendChild(orow);
    }

    // ---- trim bar + 2dp second fields (video/audio) ----
    let syncTrim = () => {};
    if (wantsTrim) {
        const row = document.createElement("div");
        row.className = "mmrp-trim-row";
        const label = document.createElement("span");
        label.className = "mmrp-edit-label";
        label.textContent = "Trim";
        row.appendChild(label);

        const inNum = document.createElement("input");
        const outNum = document.createElement("input");
        for (const el of [inNum, outNum]) {
            el.className = "mmrp-trim-num";
            el.type = "number";
            el.step = "0.01";
            el.min = "0";
            el.disabled = true;
            // Keep graph hotkeys away from typing, same as the save-name box.
            el.addEventListener("keydown", (e) => e.stopPropagation());
        }

        const bar = document.createElement("div");
        bar.className = "mmrp-trim-bar";
        const span = document.createElement("div");
        span.className = "mmrp-trim-span";
        const inHandle = document.createElement("div");
        inHandle.className = "mmrp-trim-handle";
        const outHandle = document.createElement("div");
        outHandle.className = "mmrp-trim-handle";
        bar.appendChild(span);
        bar.appendChild(inHandle);
        bar.appendChild(outHandle);

        const durLabel = document.createElement("span");
        durLabel.className = "mmrp-edit-label";
        durLabel.textContent = "…";

        // "Clear crop"'s pair: back to the whole clip, so Save deletes the saved
        // trim (normalizeTrim collapses a full-span window to null).
        clearTrimBtn = document.createElement("button");
        clearTrimBtn.className = "mmrp-btn mmrp-clear-btn";
        clearTrimBtn.textContent = "Clear trim";

        row.appendChild(inNum);
        row.appendChild(bar);
        row.appendChild(outNum);
        row.appendChild(durLabel);
        row.appendChild(clearTrimBtn);
        modal.appendChild(row);

        syncTrim = () => {
            if (!duration || !trim) return;
            inNum.value = trim[0].toFixed(2);
            outNum.value = trim[1].toFixed(2);
            span.style.left = `${(trim[0] / duration) * 100}%`;
            span.style.width = `${((trim[1] - trim[0]) / duration) * 100}%`;
            inHandle.style.left = `calc(${(trim[0] / duration) * 100}% - 5px)`;
            outHandle.style.left = `calc(${(trim[1] / duration) * 100}% - 5px)`;
            syncClears();
        };

        const setTrim = (start, end, seekTo) => {
            if (!duration) return;
            // in stays >= 0.05s before out; both stay inside the clip
            start = clamp01(r2(start), 0, Math.max(0, r2(duration) - 0.05));
            end = clamp01(r2(end), start + 0.05, r2(duration));
            trim = [start, end];
            syncTrim();
            // scrub the modal's own video so the user sees the frame they chose
            if (kind === "video" && seekTo !== undefined) media.currentTime = seekTo;
        };

        const dragHandle = (which) => (e) => {
            stopPlayback();
            e.preventDefault();
            const box = bar.getBoundingClientRect();
            if (!box.width || !duration) return;
            const move = (ev) => {
                const t = clamp01((ev.clientX - box.left) / box.width, 0, 1) * duration;
                if (which === "in") setTrim(t, trim[1], t);
                else setTrim(trim[0], t, t);
            };
            const up = () => {
                window.removeEventListener("mousemove", move);
                window.removeEventListener("mouseup", up);
            };
            window.addEventListener("mousemove", move);
            window.addEventListener("mouseup", up);
        };
        inHandle.addEventListener("mousedown", dragHandle("in"));
        outHandle.addEventListener("mousedown", dragHandle("out"));

        inNum.addEventListener("change", () => stopPlayback() || setTrim(parseFloat(inNum.value) || 0, trim ? trim[1] : 0, parseFloat(inNum.value) || 0));
        outNum.addEventListener("change", () => stopPlayback() || setTrim(trim ? trim[0] : 0, parseFloat(outNum.value) || 0));
        clearTrimBtn.onclick = () => {
            mlog("edit_cleared", { file: ref.file, what: "trim" });
            stopPlayback();
            setTrim(0, duration || 0);
        };

        media.addEventListener("loadedmetadata", () => {
            duration = media.duration;
            durLabel.textContent = `/ ${r2(duration).toFixed(2)}s`;
            inNum.disabled = false;
            outNum.disabled = false;
            if (!trim) trim = [0, r2(duration)];
            // a saved trim on a since-replaced, shorter file still clamps sanely
            setTrim(trim[0], trim[1], kind === "video" ? trim[0] : undefined);
        });
    }

    // ---- play the original / play the edit (video, audio) -------------------------
    // The tile's ▶ plays the reference; this plays what the SOCKET will carry. "Play
    // edit" seeks to the in-point, stops at the out-point, and (for video) reframes the
    // media so the crop fills the window - the rect overlay is hidden while it runs,
    // because the media underneath it has moved.
    let stopPlayback = () => {};
    if (kind !== "image") {
        const row = document.createElement("div");
        row.className = "mmrp-play-row";
        const label = document.createElement("span");
        label.className = "mmrp-edit-label";
        label.textContent = "Preview";
        row.appendChild(label);

        const origBtn = document.createElement("button");
        origBtn.className = "mmrp-btn";
        const editBtn = document.createElement("button");
        editBtn.className = "mmrp-btn";
        const ORIG = "Play original";
        const EDIT = wantsCrop ? "Play edit" : "Play trim";
        origBtn.textContent = ORIG;
        editBtn.textContent = EDIT;
        row.appendChild(origBtn);
        row.appendChild(editBtn);
        modal.appendChild(row);

        let mode = null;      // null | "original" | "edit"
        let stopAt = null;
        let raf = null;       // out-point watchdog; timeupdate alone fires every ~250ms,
                              // which overshoots the out-point by a visible quarter second.
                              // This is NOT the canvas draw loop the header rules out - it
                              // exists only while the modal's own media is playing.

        const frame = (on) => {
            if (!wantsCrop) return;
            if (on) {
                const box = media.getBoundingClientRect();
                if (!box.width || !box.height) return;
                const f = cropPreviewBox(crop, box.width, box.height);
                mediaWrap.style.width = `${f.wrapW}px`;
                mediaWrap.style.height = `${f.wrapH}px`;
                Object.assign(media.style, {
                    position: "absolute",
                    maxWidth: "none",
                    maxHeight: "none",
                    width: `${f.mediaW}px`,
                    height: `${f.mediaH}px`,
                    left: `${f.left}px`,
                    top: `${f.top}px`,
                });
                if (cropLayer) cropLayer.style.display = "none";
            } else {
                mediaWrap.style.width = "";
                mediaWrap.style.height = "";
                for (const k of ["position", "maxWidth", "maxHeight", "width", "height", "left", "top"]) {
                    media.style[k] = "";
                }
                if (cropLayer) cropLayer.style.display = "";
            }
        };

        const sync = () => {
            origBtn.textContent = mode === "original" ? "Stop" : ORIG;
            editBtn.textContent = mode === "edit" ? "Stop" : EDIT;
        };

        stopPlayback = () => {
            if (!mode) return;
            if (raf !== null) {
                cancelAnimationFrame(raf);
                raf = null;
            }
            media.pause();
            mode = null;
            stopAt = null;
            if (kind === "video") media.muted = true;
            frame(false);
            sync();
        };

        const start = (next) => {
            if (mode === next) {
                stopPlayback();
                return;
            }
            frame(false);
            mode = next;
            if (next === "edit") {
                frame(true);
                media.currentTime = trim ? trim[0] : 0;
                stopAt = trim ? trim[1] : null;
            } else {
                media.currentTime = 0;
                stopAt = null;
            }
            media.muted = false;   // the point of pressing play is to hear it too
            mlog("preview", { file: ref.file, mode: next,
                              from: next === "edit" && trim ? trim[0] : 0,
                              to: next === "edit" && trim ? trim[1] : null });
            media.play().catch(() => stopPlayback());
            const tick = () => {
                if (!mode) return;
                if (stopAt !== null && media.currentTime >= stopAt) {
                    stopPlayback();
                    return;
                }
                raf = requestAnimationFrame(tick);
            };
            raf = requestAnimationFrame(tick);
            sync();
        };

        origBtn.onclick = () => start("original");
        editBtn.onclick = () => start("edit");
        media.addEventListener("timeupdate", () => {
            if (stopAt !== null && media.currentTime >= stopAt) stopPlayback();
        });
        media.addEventListener("ended", () => stopPlayback());
    }

    // For an image, size math only needs the natural dimensions.
    if (kind === "image") {
        media.addEventListener("load", () => {
            mediaW = media.naturalWidth;
            mediaH = media.naturalHeight;
        });
    } else if (kind === "video") {
        media.addEventListener("loadedmetadata", () => {
            mediaW = media.videoWidth;
            mediaH = media.videoHeight;
        });
    }

    // Initial disabled state for the Clear pair — the sync calls above ran before
    // the buttons existed. (Clear trim re-syncs again on loadedmetadata.)
    syncClears();

    // ---- footer ----
    const footer = document.createElement("div");
    footer.className = "mmrp-modal-footer";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "mmrp-btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = () => overlay.remove();

    const saveBtn = document.createElement("button");
    saveBtn.className = "mmrp-btn mmrp-btn-primary";
    saveBtn.textContent = "Save";
    saveBtn.onclick = () => {
        const next = cloneRefs(node._mmrpRefs);
        const target = next[`${kind}s`][index];

        const nc = wantsCrop ? normalizeCrop(crop) : null;
        if (nc) target.crop = nc;
        else delete target.crop;

        // the full clip is "no trim" — serialise nothing, like refs.py omits it
        const nt = wantsTrim ? normalizeTrim(trim, duration) : null;
        if (nt) target.trim = nt;
        else delete target.trim;

        // Orientation, serialised the same way: written when set, DELETED when not, so an
        // untouched reference keeps producing the byte-identical references_json older
        // builds wrote.
        const nr = wantsCrop && rotate ? ((rotate % 360) + 360) % 360 : 0;
        if (nr) target.rotate = nr;
        else delete target.rotate;
        if (wantsCrop && flip) target.flip = flip;
        else delete target.flip;
        // Only meaningful alongside a rotation, and only when it differs from the default.
        if (nr && !expand) target.rotate_expand = false;
        else delete target.rotate_expand;

        mlog("edit_saved", { kind, file: ref.file, crop: nt === null && nc === null ? null : nc,
                             trim: nt, rotate: nr || null, flip: flip || null,
                             cleared: nc === null && nt === null && !nr && !flip });
        dropThumb(ref.file); // the tile re-fetches through the route with the edit
        overlay.remove();
        applyRefs(node, next);
    };

    footer.appendChild(cancelBtn);
    footer.appendChild(saveBtn);
    modal.appendChild(footer);

    document.body.appendChild(overlay);
}

// ---------------------------------------------------------------------------
// System prompt modal — the ONLY place `system_prompt` is ever shown. The widget
// itself is hidden on the node body like direction/references_json (onNodeCreated).
// "Load default" only fills the textarea from the server's packaged default; nothing
// commits to the widget until Save & close, so the user can edit from that starting
// point without losing their in-progress edits by opening/closing the modal.
// ---------------------------------------------------------------------------

// Show the Local LLM button only while prompt_provider is `local`. Called from three
// places because a combo can change three ways: the user clicks it, a saved graph
// restores it, or applyPick() sets it from the picker itself.
// REVERSIBLE, unlike hideWidget(): that one installs getter-only accessors and is a
// one-way door, which is right for the three permanently-hidden widgets and wrong here.
// These come back when the provider changes, so the real type is stashed and restored.
// >>> MMRP-VISIBILITY
function setWidgetVisible(w, visible) {
    if (!w) return;
    if (w._mmrpType === undefined) {
        w._mmrpType = w.type;
        w._mmrpDraw = w.draw;      // usually undefined: litegraph draws by type
    }
    try {
        w.type = visible ? w._mmrpType : "hidden";
        w.hidden = !visible;
        if (!w.options) w.options = {};
        w.options.hidden = !visible;
        // [0,0] removes the row from litegraph's layout, which is what lets nodeSize()
        // shrink the node by exactly the hidden rows on the next pass.
        w.computeSize = visible ? undefined : () => [0, 0];
        // Load-bearing, and its absence was a real bug (2026-08-17): zero height keeps a
        // widget out of the LAYOUT but not out of the PAINT. Litegraph kept drawing the
        // hidden ones at their last_y from when they were visible, so `api_base` painted
        // its URL straight over the `reasoning_effort` row. Suppress the paint, and drop
        // the stale coordinate so nothing can be drawn or hit-tested at it either.
        w.draw = visible ? w._mmrpDraw : () => {};
        if (!visible) w.last_y = undefined;
    } catch (_) {
        // Some frontend builds make these reactive accessors. Degrade to "always shown"
        // rather than throwing mid-configure and aborting the workflow load.
    }
}

// reasoning_effort is OpenRouter-only (endpoint.sends_reasoning), so it hides with the
// rest of that group. A dropdown that silently does nothing on `local` is the same trap
// that let a local model id sit in a field OpenRouter then read.
const PROVIDER_FIELDS = {
    openrouter: ["openrouter_api_key", "openrouter_model", "reasoning_effort"],
    local: [
        "api_base", "local_model_slug",
        // Declared at the END of INPUT_TYPES (widgets_values is positional — see
        // ORDER_CURRENT) but grouped HERE, because grouping is by name and the canvas
        // should read by decision flow even though the wire format cannot.
        "local_ttl", "local_server", "local_send_reasoning", "local_extra_body",
    ],
};
// <<< MMRP-VISIBILITY

function syncLocalBtn(node) {
    const w = widgetByName(node, "prompt_provider");
    const provider = migrateProviderValue(w ? w.value : undefined);

    const btn = node?._mmrpLocalBtn;
    if (btn) btn.style.display = provider === "local" ? "" : "none";

    // A field the run will ignore is worse than absent: it invites you to fill it in and
    // then silently does nothing with it, which is exactly how a local model id ended up
    // being posted to OpenRouter. `none` hides both groups - it calls nobody.
    for (const [name, fields] of Object.entries(PROVIDER_FIELDS)) {
        for (const field of fields) {
            setWidgetVisible(widgetByName(node, field), provider === name);
        }
    }
    // reasoning_effort is the one field with two owners. It is OpenRouter's by default,
    // but turning on local_send_reasoning makes it live on `local` too — and a setting
    // that is being sent while its own control stays hidden is the same trap this whole
    // function exists to prevent, just inverted.
    if (provider === "local") {
        const sendsReasoning = !!widgetByName(node, "local_send_reasoning")?.value;
        setWidgetVisible(widgetByName(node, "reasoning_effort"), sendsReasoning);
    }
    // last_y only settles after litegraph's next layout pass, so re-assert on the frame
    // after rather than reading a stale height now.
    if (node.setSize) {
        setTimeout(() => {
            try {
                setSizeInternal(node);
                app.graph?.setDirtyCanvas(true, true);
            } catch (_) {}
        }, 0);
    }
}

// litegraph gives a combo widget its own callback; wrapping it is how we hear the user
// changing the value. Guarded against double-wrapping because onNodeCreated can run more
// than once for a node across a paste or an undo, and a chain of wrappers would fire the
// original callback once per wrap.
function watchProviderWidget(node) {
    // Two widgets decide what is visible, not one. prompt_provider picks the group;
    // local_send_reasoning decides whether reasoning_effort joins the local group. Both
    // are watched the same way, and both are guarded against double-wrapping because
    // onNodeCreated can run more than once across a paste or an undo and a chain of
    // wrappers would fire the original callback once per wrap.
    for (const name of ["prompt_provider", "local_send_reasoning"]) {
        const w = widgetByName(node, name);
        if (!w || w._mmrpWatched) continue;
        const orig = w.callback;
        w.callback = function (...args) {
            const out = orig ? orig.apply(this, args) : undefined;
            syncLocalBtn(node);
            return out;
        };
        w._mmrpWatched = true;
    }
}


// Writes a native widget the way litegraph expects: value first, then its callback, so
// anything watching that widget (serialization, other extensions) sees the change rather
// than a value that appeared behind its back.
function setWidget(node, name, value) {
    const w = widgetByName(node, name);
    if (!w) return false;
    w.value = value;
    if (w.callback) w.callback(value);
    return true;
}


// Answers "how do I know the api_base?" by not making the user answer it. Sweeps the
// known local ports server-side, lists what actually replied, and writes all three
// widgets from one click - provider, URL and model id together, because getting two of
// the three right still fails at queue time with a 404 nobody can read.
function openLocalServerModal(node, anchorBtn) {
    const overlay = document.createElement("div");
    overlay.className = "mmrp-overlay";
    dismissOnBackdrop(overlay);

    const modal = document.createElement("div");
    modal.className = "mmrp-modal";
    overlay.appendChild(modal);

    const header = document.createElement("div");
    header.className = "mmrp-modal-header";
    header.textContent = "Local LLM";
    modal.appendChild(header);

    const hint = document.createElement("div");
    hint.className = "mmrp-modal-hint";
    hint.textContent = "Looking for a server on this machine...";
    modal.appendChild(hint);

    const list = document.createElement("div");
    list.className = "mmrp-server-list";
    modal.appendChild(list);

    const applyPick = (base, model) => {
        setWidget(node, "prompt_provider", "local");
        setWidget(node, "api_base", base);
        setWidget(node, "local_model_slug", model);
        syncLocalBtn(node);
        overlay.remove();
        if (anchorBtn) {
            const original = anchorBtn.textContent;
            anchorBtn.textContent = "Using local ✓";
            setTimeout(() => { anchorBtn.textContent = original; }, 2000);
        }
        app.graph?.setDirtyCanvas(true, true);
    };

    // `unprobed` is a non-loopback api_base: the reason the user's own URL is missing
    // from this list has to be stated, or the modal looks like it simply failed to find
    // a server the user knows is running.
    const render = (servers, unprobed) => {
        list.replaceChildren();
        const remoteNote = unprobed
            ? ` Your api_base (${unprobed}) is not on this machine, so it was not probed — ` +
              "this button only ever looks at localhost, so that a ComfyUI anyone can reach " +
              "cannot be turned into a port scanner. Type the model id into local_model_slug " +
              "by hand and it will work fine."
            : "";
        if (!servers.length) {
            hint.textContent =
                "No OpenAI-compatible server answered on this machine. Start LM Studio " +
                "(Developer tab), or `ollama serve`, then Rescan. If your server runs " +
                "somewhere else, type its URL into api_base by hand." + remoteNote;
            return;
        }
        hint.textContent =
            "Pick the model that should write your prompts. Videos will be sent as " +
            "still frames and audio will not be sent at all." + remoteNote;
        for (const s of servers) {
            const group = document.createElement("div");
            group.className = "mmrp-server-group";

            const title = document.createElement("div");
            title.className = "mmrp-server-title";
            title.textContent = `${s.label} — ${s.base}`;
            group.appendChild(title);

            if (!s.models.length) {
                const empty = document.createElement("div");
                empty.className = "mmrp-server-empty";
                empty.textContent = "answering, but no model is loaded";
                group.appendChild(empty);
            }
            for (const m of s.models) {
                const row = document.createElement("button");
                row.className = "mmrp-btn mmrp-server-model";
                row.textContent = m;
                row.onclick = () => applyPick(s.base, m);
                group.appendChild(row);
            }
            list.appendChild(group);
        }
    };

    // Sweeps WITH whatever is already in api_base, which is the whole point of #3: a
    // server on a port that isn't one of the six known ones was previously invisible to
    // this button even when its URL was sitting in the widget the button writes to.
    //
    // A non-loopback api_base is deliberately not sent. The route would refuse it with a
    // 400 and the modal would show "Could not scan", which reads as a broken button
    // rather than a policy; asking only for what can be answered lets render() explain
    // instead. The refusal itself still lives on the server — see MMRP-LOOPBACK.
    const sweep = () => {
        const typed = String(widgetByName(node, "api_base")?.value || "").trim();
        const probeable = typed !== "" && isLoopbackUrl(typed);
        hint.textContent = "Looking for a server on this machine...";
        list.replaceChildren();
        let unprobed = probeable ? "" : typed;
        apiDetectServers(probeable ? typed : "")
            .catch(() => {
                // The server refused the base this side thought was fine. The two
                // predicates CAN disagree — WHATWG `new URL` normalises hosts (IPv4
                // shorthand, trailing dots, IDNA, percent-decoding) and Python's urlparse
                // does not — and when they do, the sweep must still happen.
                //
                // Without this, one odd character in api_base turned the whole button
                // into "Could not scan" with nothing listed, while LM Studio sat there
                // answering on 127.0.0.1:1234. On main a bad api_base was simply ignored,
                // so that would be a worse regression than the bug being fixed. Fall back
                // to the plain sweep and explain, exactly as for a LAN address.
                unprobed = typed;
                return apiDetectServers("");
            })
            .then((servers) => render(servers, unprobed))
            .catch((e) => { hint.textContent = `Could not scan: ${e.message}`; });
    };

    const footer = document.createElement("div");
    footer.className = "mmrp-modal-footer";

    const rescanBtn = document.createElement("button");
    rescanBtn.className = "mmrp-btn";
    rescanBtn.textContent = "Rescan";
    rescanBtn.onclick = sweep;

    const closeBtn = document.createElement("button");
    closeBtn.className = "mmrp-btn mmrp-btn-primary";
    closeBtn.textContent = "Close";
    closeBtn.onclick = () => overlay.remove();

    footer.appendChild(rescanBtn);
    footer.appendChild(closeBtn);
    modal.appendChild(footer);

    document.body.appendChild(overlay);
    sweep();
}


function openSystemPromptModal(node) {
    // One widget per register. `system_prompt` is the STANDARD one - it keeps its name
    // and its meaning, so an existing workflow's override is untouched.
    const WIDGET_FOR = { standard: "system_prompt", replacement: "system_prompt_replacement" };
    // The tab that opens is the register this workflow will actually use, so the modal
    // starts on the prompt the next queue will read rather than on an arbitrary one.
    let tab = systemPromptModeOf(node);
    const widgetFor = (which) => widgetByName(node, WIDGET_FOR[which]);
    const jobType = String(widgetByName(node, "job_type")?.value ?? "").trim().toLowerCase();
    // Only `auto` is ambiguous, and only `auto` gets the caveat — saying "assuming
    // standard" under an explicit job_type: standard would be noise about a certainty.
    const noteFor = (which) =>
        jobType === "auto"
            ? " job_type is `auto`, so the register is picked at queue time by a " +
              `classifier: this is the \`${which}\` prompt, and a job routed to the other ` +
              "register uses a different one."
            : ` This is the \`${which}\` prompt, matching job_type.`;

    const overlay = document.createElement("div");
    overlay.className = "mmrp-overlay";
    dismissOnBackdrop(overlay);

    const modal = document.createElement("div");
    modal.className = "mmrp-modal";
    overlay.appendChild(modal);

    const header = document.createElement("div");
    header.className = "mmrp-modal-header";
    header.textContent = "System prompt";
    modal.appendChild(header);

    const hint = document.createElement("div");
    hint.className = "mmrp-modal-hint";
    // Filled by render(), which knows which tab is showing.
    modal.appendChild(hint);

    // Two tabs, because the two registers are deliberately separate 20KB+ files and
    // editing one must not touch the other.
    const tabs = document.createElement("div");
    tabs.className = "mmrp-tabs";
    const tabButtons = {};
    for (const which of ["standard", "replacement"]) {
        const b = document.createElement("button");
        b.className = "mmrp-btn mmrp-tab";
        b.textContent = which;
        b.onclick = () => selectTab(which);
        tabs.appendChild(b);
        tabButtons[which] = b;
    }
    modal.appendChild(tabs);

    const textarea = document.createElement("textarea");
    textarea.className = "mmrp-modal-textarea";
    textarea.spellcheck = false;
    modal.appendChild(textarea);

    // A blank widget means "use the packaged default", but an empty box hides WHICH
    // prompt is running. Show it, so the modal is the node's prompt rather than a
    // guess about it. Saving an untouched prefill is a no-op: Save writes the
    // textarea back only if it differs from the default (see saveBtn).
    // Per-tab, because switching tabs mid-edit must not compare this tab's text against
    // the OTHER tab's prefill and conclude the user typed it.
    const prefilled = { standard: "", replacement: "" };

    // Everything the user typed stays in the modal until Save, on both tabs, so switching
    // to check the other register does not throw away an edit.
    const draft = {};
    const stash = () => { draft[tab] = textarea.value; };

    function selectTab(which) {
        if (which === tab) return;
        stash();
        tab = which;
        render();
    }

    function render() {
        for (const [which, b] of Object.entries(tabButtons)) {
            b.classList.toggle("mmrp-active", which === tab);
        }
        const w = widgetFor(tab);
        textarea.value = draft[tab] !== undefined ? draft[tab] : (w ? w.value || "" : "");
        loadDefaultBtn.textContent = `Load ${tab} default`;
        hint.textContent = "Empty = use the built-in default." + noteFor(tab);
        if (!textarea.value.trim()) {
            const asked = tab;
            hint.textContent =
                "Showing the built-in default. Edit to override it for this workflow."
                + noteFor(asked);
            apiSystemPromptDefault(asked)
                .then(({ text, mode: served }) => {
                    // Two guards: the user may have typed while this was in flight, and
                    // they may have switched tabs - landing another register's 26KB
                    // prompt in this box would be worse than showing nothing.
                    if (tab !== asked || textarea.value.trim()) return;
                    prefilled[asked] = text;
                    textarea.value = text;
                    if (served !== asked) {
                        hint.textContent =
                            "Showing the built-in default. Edit to override it for this "
                            + "workflow." + noteFor(served);
                    }
                })
                .catch(() => {
                    hint.textContent = "Empty = use the built-in default." + noteFor(asked);
                });
        }
    }

    const footer = document.createElement("div");
    footer.className = "mmrp-modal-footer";

    const loadDefaultBtn = document.createElement("button");
    loadDefaultBtn.className = "mmrp-btn";
    loadDefaultBtn.textContent = "Load default";
    loadDefaultBtn.onclick = async () => {
        try {
            const asked = tab;
            const { text, mode: served } = await apiSystemPromptDefault(asked);
            if (tab !== asked) return;
            textarea.value = text;
            // 7: this used to leave prefilledDefault at "" - so Save & close saw an
            // edited-looking box and froze the whole packaged prompt into the workflow,
            // which is exactly what its own comment says it is trying to avoid. It
            // matters more now that the text is register-specific: a frozen replacement
            // prompt would then be used for a standard run.
            prefilled[asked] = text;
            if (served !== asked) {
                hint.textContent = "Showing the built-in default. Edit to override it "
                    + "for this workflow." + noteFor(served);
            }
        } catch (e) {
            alert(`Couldn't load the default: ${e.message}`);
        }
    };

    // The settings modal is where the node's per-workflow preferences live, and this is
    // one. It sits above the footer rather than in it: the footer's buttons all act on the
    // textarea, and a checkbox that does not is confusing among them.
    const retagRow = document.createElement("label");
    retagRow.className = "mmrp-modal-check";
    const retagBox = document.createElement("input");
    retagBox.type = "checkbox";
    retagBox.checked = retagEnabled(node);
    retagBox.onchange = () => {
        node._mmrpRetag = retagBox.checked;
        syncReferencesWidget(node);
    };
    retagRow.appendChild(retagBox);
    retagRow.appendChild(document.createTextNode(
        " Keep reference tags in the prompt up to date. Deleting a reference or toggling a "
        + "video's soundtrack renumbers the tags after it; with this on, the prompt text is "
        + "rewritten to match so <Picture 2> keeps meaning the image you wrote it about."
    ));
    modal.appendChild(retagRow);

    const resetBtn = document.createElement("button");
    resetBtn.className = "mmrp-btn";
    resetBtn.textContent = "Reset";
    resetBtn.title = "Discard edits, revert to the last-saved value";
    resetBtn.onclick = () => {
        const w = widgetFor(tab);
        delete draft[tab];
        textarea.value = w ? w.value || "" : "";
    };

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "mmrp-btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = () => overlay.remove();

    const saveCloseBtn = document.createElement("button");
    saveCloseBtn.className = "mmrp-btn mmrp-btn-primary";
    saveCloseBtn.textContent = "Save & close";
    saveCloseBtn.onclick = () => {
        // BOTH registers are written, not just the visible one: the other tab may hold
        // an edit made before switching, and silently dropping it would be the worst
        // possible reading of "Save".
        stash();
        for (const [which, name] of Object.entries(WIDGET_FOR)) {
            const w = widgetByName(node, name);
            if (!w || draft[which] === undefined) continue;
            const v = draft[which];
            // An untouched prefill saves as blank, so the workflow keeps tracking the
            // packaged prompt instead of freezing today's copy into the graph.
            w.value = prefilled[which] && v === prefilled[which] ? "" : v;
            if (w.callback) w.callback(w.value);
        }
        overlay.remove();
    };

    render();

    footer.appendChild(loadDefaultBtn);
    footer.appendChild(resetBtn);
    footer.appendChild(cancelBtn);
    footer.appendChild(saveCloseBtn);
    modal.appendChild(footer);

    document.body.appendChild(overlay);
    textarea.focus();
}

// ---------------------------------------------------------------------------
// Selection + delete-key handling. The key handler is CAPTURE-phase and swallows
// the event completely — otherwise ComfyUI's own keydown handler deletes the whole
// NODE on Delete/Backspace. It no-ops while an INPUT/TEXTAREA has focus so
// Backspace in the prompt edits text. Torn down in onRemoved.
// ---------------------------------------------------------------------------

function installSelectionHandlers(node) {
    const body = node._mmrpBody;

    const clearOnOutsideClick = (e) => {
        if (!node._mmrpSelected) return;
        // The canvas's own mousedown decides what a click there means (tile vs
        // empty area); everywhere else, any click drops the selection.
        if (e.target === body.canvas) return;
        node._mmrpSelected = null;
        scheduleDraw(node);
    };

    const keyHandler = (e) => {
        if (!node._mmrpSelected) return;
        const active = document.activeElement;
        if (active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT")) return;
        if (e.key === "Delete" || e.key === "Backspace") {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            const { kind, index } = node._mmrpSelected;
            removeRef(node, kind, index);
        } else if (e.key === "Escape") {
            node._mmrpSelected = null;
            scheduleDraw(node);
        }
    };

    document.addEventListener("mousedown", clearOnOutsideClick, true);
    document.addEventListener("keydown", keyHandler, true);

    const origOnRemoved = node.onRemoved;
    node.onRemoved = function () {
        document.removeEventListener("mousedown", clearOnOutsideClick, true);
        document.removeEventListener("keydown", keyHandler, true);
        (node._mmrpHideIntervals || []).forEach((id) => clearInterval(id));
        stopPreview(node);
        if (node._mmrpBody) node._mmrpBody.resizeObserver.disconnect();
        // A drag in flight holds three capture-phase WINDOW listeners. Without this they
        // outlive the node until the next mouseup anywhere, whose finishDrag then applies
        // a reorder to a node that is no longer in the graph.
        node._mmrpDrag = null;
        endDragListeners(node);
        liveNodes.delete(node);
        if (origOnRemoved) origOnRemoved.apply(this, arguments);
    };
}

// ---------------------------------------------------------------------------
// Size enforcement. The node is resizable now, so these three overrides no longer pin it
// to one size - they keep WIDTH the user's (floored at what the button row needs) and
// HEIGHT derived from the content, with a height drag re-read as a prompt-box resize.
//
// They are still belt and braces about everything else: a restored workflow, a paste or a
// frontend quirk cannot put the node at a size its content does not support.
// ---------------------------------------------------------------------------

// A height drag, turned into a prompt-box height and remembered.
//
// The node's height is derived from its content, so there is nothing to store it in
// directly — but the drag has to mean SOMETHING or the handle would fight the user. It
// means "make the prompt this much taller", which is the only part of the block whose
// height is a preference rather than a consequence.
//
// It applies the DELTA, not the requested height. Deriving the prompt from the absolute
// (requested minus everything else) looked equivalent and was not: dragging the node
// NARROWER wraps a tile onto a new line, which grows "everything else" — so a pure
// horizontal drag, whose height never changed, collapsed the prompt to its floor. Then
// widening again freed that space and handed it back to the prompt, which had forgotten
// how tall it used to be. Measured: 110 -> 44 -> 181 over one narrow-and-widen round
// trip, with the node left 71px taller than it started. A delta is zero when the user
// did not drag vertically, which is the whole of the fix.
//
// The unclamped total is what is stored, so dragging down to the floor and back up
// returns to the height you started from instead of over-shooting by whatever the clamp
// swallowed. The frontend clamps the drag to computeSize() anyway, so it cannot run far
// below the floor.
// Is the user actually dragging THIS node's resize handle right now?
//
// The only reliable answer, and it has to be asked. The frontend calls setSize for its own
// reasons constantly - most damagingly from `_arrangeWidgets`, which runs on every draw
// and pushes the node taller to make room for the widget stack. Treating those as height
// drags was catastrophic: each one grew the prompt, which grew the node, which made
// _arrangeWidgets push again. Measured on a fresh, untouched node: the prompt box settled
// at 1736px and the node at 2978, and every save/reload cycle added another 1626px - which
// was then persisted into references_json, so the corruption was written into the workflow.
//
// `resizing_node` is set on the resize drag's onDragStart and cleared in its finally
// (verified in the 1.51.9 bundle), so it is exactly "a human is dragging this node's
// handle". A frontend that does not have it gets no height-drag support rather than the
// runaway - the feature degrades, which is the right way round.
function isUserResizing(node) {
    const canvas = app.canvas;
    if (!canvas || !("resizing_node" in canvas)) return false;
    return canvas.resizing_node === node;
}

// The frontend hands setSize a Float64Array-backed view, not an Array.
//
// 1.51.9's resize drag builds a `Rectangle extends Float64Array` and passes `rect.size`,
// a subarray of it. `Array.isArray` is FALSE for that, so guards written against it
// skipped every real drag while passing every hand-written `setSize([w, h])` from a
// console - which is exactly how this shipped "verified" and did nothing. Duck-typing on
// length accepts both.
function isSizePair(size) {
    return !!size && typeof size.length === "number" && size.length >= 2;
}

// How much taller the user has dragged, read from the POINTER rather than from the height
// the frontend passes.
//
// The passed height is a DERIVED quantity, not a signal. 1.51.9's drag computes it as
// "size at mousedown plus total pointer delta" and then clamps it up to computeSize() on
// both axes - so dragging the node NARROWER wraps a tile onto a new line, raises the
// minimum height, and passes that new minimum, with the pointer never having moved
// vertically at all. A clamped frame and a deliberate drag to the floor are byte
// identical, and no amount of reasoning about the number separates them. Four successive
// fixes tried to; each one leaked somewhere new.
//
// The pointer cannot be confused this way. eDown is the pointerdown event and the drag is
// ABSOLUTE from it - the frontend rebuilds its rectangle from a snapshot taken at
// mousedown on every frame and never re-anchors - so dy is the user's whole vertical
// intent, immune to the clamp, to tiles wrapping, and to content growing at any point
// during the drag. It also makes this idempotent: every setSize caller during a drag
// derives the SAME prompt height from the same pointer, so "which caller is this?" stops
// being a question the code has to answer.
//
// Verified against a port of the 1.51.9 drag algorithm driving the real functions in this
// file, over fifteen scenarios including both N corners, cancellation, two nodes dragged
// independently, and content growing both between mousedown and the first move and in the
// middle of a drag.
function absorbPointerIntoPrompt(node, size) {
    // Nothing before the node has settled: at creation node.size[1] is still litegraph's
    // default, and promptHeightOf would anchor against a layout that does not exist yet.
    if (!node._mmrpSizeReady) return;

    const canvas = app.canvas;
    const pointer = canvas && canvas.pointer;
    const y0 = pointer && pointer.eDown && pointer.eDown.canvasY;
    // eMove rather than graph_mouse: CanvasPointer.move() is gated on the PRIMARY pointer
    // and sets eMove immediately before dispatching onDrag, while graph_mouse is written
    // by every pointermove including non-primary ones - a stray touch would corrupt it
    // between two frames of a drag. Before the first move there is no eMove and dy is 0.
    const yNow = pointer && (pointer.eMove ? pointer.eMove.canvasY : y0);
    // A frontend without these gets no height drag rather than a wrong one: the width
    // still resizes and the prompt simply stops being draggable. Degrades, never corrupts.
    if (!Number.isFinite(y0) || !Number.isFinite(yNow)) return;

    let anchor = node._mmrpDragAnchor;
    if (!anchor) {
        // WHICH CORNER, derived from where the pointer went down rather than read off the
        // canvas pointer's own resize-direction field.
        //
        // That field cannot be used here, and the reason is worth recording: it is set at
        // pointerdown and wiped again on the first mousemove, by processMouseMove's
        // group-hover branch (`resizeDirection &&= void 0`, with no eDown guard), while
        // `resizing_node` is not set until the drag starts. So the two are NEVER valid
        // together - at the first setSize the direction is readable but resizing_node is
        // null, and from the second onwards resizing_node is set and the direction is
        // gone. Reading it during the drag made this absorb dead code in the browser
        // while every simulated scenario passed.
        //
        // eDown survives the whole drag. Only corners resize (resizeEdgeSize exists in
        // the source but is used nowhere), so the pointer went down within a handful of
        // pixels of the top or the bottom edge, and which one it was is not close.
        const top = node.pos[1];
        const north = Math.abs(y0 - top) < Math.abs(y0 - (top + (node.size[1] || 0)));
        anchor = node._mmrpDragAnchor = {
            north,
            prompt: promptHeightOf(node),
            // N-corner drags keep the node's BOTTOM edge still and move its top. The
            // frontend does that itself by rewriting the rectangle's y - including on the
            // clamped frames, where it uses the drag-start bottom explicitly - so y plus
            // height equals the drag-start bottom on EVERY frame. That invariant is what
            // makes the bottom recoverable from any frame at all, which is why this can be
            // captured lazily. But since this wrapper replaces the height with the derived
            // one, it has to re-pin the bottom itself further down, or the node slides.
            bottom: north && isSizePair(size) && Number.isFinite(size[1])
                ? node.pos[1] + size[1]
                : null,
        };
    }

    const dy = yNow - y0;
    // SE/SW grow downward with the pointer; NE/NW grow upward, so the sign flips.
    const signedDy = anchor.north ? -dy : dy;
    const next = Math.max(CONTENT.minPromptH, Math.round(anchor.prompt + signedDy));
    // Compared against the EFFECTIVE height, not the stored field. Merely grabbing the
    // handle produces a frame with dy 0, whose `next` is the height the box already has -
    // but on a node that has never been dragged the field is undefined, so comparing
    // against it wrote the default back and persisted a prompt_h into references_json for
    // a node nobody resized. Harmless in value, but it stopped an untouched envelope from
    // being byte-identical after a stray click on the corner.
    if (next === promptHeightOf(node)) return;
    node._mmrpPromptH = next;
    syncPromptHeight(node);
    schedulePersistPromptH(node);
}

// The smallest this node may be: the content with the prompt box at its floor.
//
// This is what computeSize() has to report, because the frontend clamps a resize drag UP
// to it on BOTH axes — 1.51.9: `c.width < l[0] && (c.width = l[0])`, and the same for
// height. Reporting the CURRENT size there (which is what this did) makes the node
// un-shrinkable: every drag inwards is clamped straight back out, so it can only ever
// grow. `min_size`, which the old code set alongside it, does not appear anywhere in the
// 1.51.9 bundle at all.
function minNodeHeight(node) {
    const widgetY = (node._mmrpDomWidget && node._mmrpDomWidget.last_y) || 80;
    const outputsMin = ((node.outputs && node.outputs.length) || 1) * 20 + 40;
    const content = CONTENT.uploadsH + CONTENT.gap + layoutOf(node).height
        + CONTENT.gap + CONTENT.minPromptH;
    return Math.max(widgetY + content + domWidgetMargin(node) * 2, outputsMin);
}

function installSizeGuards(node) {
    // Resizable now, where it used to be pinned. Width is genuinely the user's; height
    // is still derived, and a height drag is re-read as a prompt resize just above.
    node.resizable = true;

    // The block's own size rides along with all three: the node size and the DOM widget
    // size are one decision, and letting them drift is what produced a 254px slab in a
    // 1340px node. syncDomWidgetSize is idempotent and does no layout, so calling it
    // from computeSize — the hottest of the three — costs two property writes.
    const origOnResize = node.onResize;
    node.onResize = function (size) {
        // Deliberately does NOT absorb. onResize runs downstream of setSize on this
        // frontend (`setSize(e){this.size=e,this.onResize?.(this.size)}`), so the height
        // it is handed is the DERIVED one this wrapper just wrote - not anything the user
        // asked for. Absorbing it fed the node's own output back in as a request and drove
        // the prompt to its floor on a drag that should have grown it. setSize is the one
        // place a user's height is real, and it is the only place that reads it.
        // A width drag re-wraps the tiles, so the slab's height can change with no change
        // to the content at all.
        applyCanvasHeight(this);
        const f = nodeSize(this);
        size[0] = f[0];
        size[1] = f[1];
        syncDomWidgetSize(this);
        if (origOnResize) origOnResize.call(this, size);
    };

    const origComputeSize = node.computeSize;
    node.computeSize = function () {
        // origComputeSize still runs for its side effects on some frontends, but its
        // answer is discarded — ours is derived from the content.
        if (origComputeSize) origComputeSize.apply(this, arguments);
        syncDomWidgetSize(this);
        // The MINIMUM, not the current size. See minNodeHeight: the frontend clamps a
        // resize drag up to whatever this returns, so reporting the current size is what
        // made the node grow-only.
        return [minNodeWidth(this), minNodeHeight(this)];
    };

    const origSetSize = node.setSize;
    node.setSize = function (size) {
        // setSize is the resize drag's entry point on this frontend, so the user's height
        // has to be read HERE — before it is replaced by the derived one two lines down.
        // Reading it in onResize instead was an identity: onResize only ever sees what
        // this function already wrote.
        // The anchor lives only for the duration of one drag.
        const resizing = isUserResizing(this);
        if (!resizing) this._mmrpDragAnchor = null;
        else if (!this._mmrpInternalResize) absorbPointerIntoPrompt(this, size);
        if (isSizePair(size) && Number.isFinite(size[0])) {
            this.size[0] = Math.max(size[0], minNodeWidth(this));
        }
        const f = nodeSize(this);
        // Re-pin the bottom edge on an N-corner drag. The frontend anchors it by rewriting
        // the rectangle's y from the height IT chose; this wrapper then replaces that
        // height with the derived one, which would leave the node drifting downward by the
        // difference on every frame. Measured before this line: 137px on a narrow-only NW
        // drag that wrapped a row, and 734px dragged well below the floor.
        if (resizing && this._mmrpDragAnchor && this._mmrpDragAnchor.bottom != null) {
            this.pos[1] = this._mmrpDragAnchor.bottom - f[1];
        }
        // Written back into the caller's own object so the drag sees the clamp...
        if (isSizePair(size)) {
            size[0] = f[0];
            size[1] = f[1];
        }
        // ...but litegraph is handed a plain array. The drag passes a Float64Array view of
        // its own rectangle, and storing that as node.size would leave the node aliasing a
        // buffer the next mousemove overwrites.
        const plain = [f[0], f[1]];
        if (origSetSize) origSetSize.call(this, plain);
        else this.size = plain;
        syncDomWidgetSize(this);
    };
}

// Re-assert the node's own size without the prompt box treating it as a drag. Every
// internal caller goes through this: relayout after a content change, the visibility
// sync, the post-mount settle. Without the flag, adding a reference would read its own
// derived height back as a user request.
function setSizeInternal(node) {
    node._mmrpInternalResize = true;
    try {
        if (node.setSize) node.setSize(nodeSize(node));
    } catch (e) {
        /* pre-mount, before last_y exists */
    } finally {
        node._mmrpInternalResize = false;
    }
}

// ---------------------------------------------------------------------------
// Extension registration
// ---------------------------------------------------------------------------

app.registerExtension({
    name: "MiniMaxRefPack.RefManager",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated?.apply(this, arguments);
            injectStyles();
            const node = this;

            const refsWidget = widgetByName(node, "references_json");
            node._mmrpRefs = parseRefsValue(refsWidget, node);
            const ivRefs = hideWidget(refsWidget);

            const directionWidget = widgetByName(node, "direction");
            const ivDirection = hideWidget(directionWidget);

            const systemPromptWidget = widgetByName(node, "system_prompt");
            const ivSystemPrompt = hideWidget(systemPromptWidget);
            // The replacement register's prompt is hidden the same way: 20KB+ of text has
            // no business on the node body, and it is edited behind the gear like its
            // pair.
            const ivSystemPromptRepl = hideWidget(widgetByName(node, "system_prompt_replacement"));

            // Cleared early in installSelectionHandlers' onRemoved wrapper so a deleted
            // node doesn't leave a hide-poll timer running (they also self-clear after
            // 1s regardless, this just avoids the wait on an early delete).
            node._mmrpHideIntervals = [ivRefs, ivDirection, ivSystemPrompt, ivSystemPromptRepl].filter((id) => id !== undefined);

            const bodyEl = buildCustomBlock(node);
            // hideOnZoom defaults to TRUE. Below the frontend's LOD threshold the whole
            // block is then hidden outright (DomWidgets.vue gates visibility on
            // `!(widget.options.hideOnZoom && lowQuality)`), so zooming out leaves this
            // node as a tall slab of bare grey body with no references and no prompt on
            // it. Independently found by semmlerino (PR #2), verified there against
            // frontend 1.48.6 and here against 1.51.9.
            const domWidget = node.addDOMWidget("mmrp_block", "custom", bodyEl, {
                serialize: false,
                hideOnZoom: false,
            });
            node._mmrpDomWidget = domWidget;
            // Constant, by construction: the CSS pins the DOM heights this arithmetic
            // assumes (uploadsH/gap), the canvas height comes from the live layout.
            // Read by litegraph for the node's layout — NOT by the frontend for the
            // block's size, which comes from width/computedHeight. See syncDomWidgetSize.
            // The size litegraph turns into computedHeight and thence into the
            // element's height, pre-compensated for its pad and the frontend's margin so
            // the element lands on exactly contentHeight.
            domWidget.computeSize = () => {
                const margin = domWidgetMargin(node);
                const reported = reportedHeightFor(node, contentHeight(node));
                domWidget._mmrpReported = reported;
                return [nodeSize(node)[0] - margin * 2, reported];
            };
            syncDomWidgetSize(node);
            liveNodes.add(node);

            node._mmrpBody.directionInput.value = directionWidget ? directionWidget.value || "" : "";
            node._mmrpBody.directionInput.addEventListener("input", () => {
                if (!directionWidget) return;
                directionWidget.value = node._mmrpBody.directionInput.value;
                if (directionWidget.callback) directionWidget.callback(directionWidget.value);
            });

            syncPromptHeight(node);
            installSizeGuards(node);
            installSelectionHandlers(node);
            watchProviderWidget(node);
            syncLocalBtn(node);
            renderNodeBody(node);
            syncReferencesWidget(node);

            // The widgets' own defaults, captured while they still hold them. nodeCreated
            // runs BEFORE configure() applies widgets_values, so this is the last moment
            // they are pristine - and the migration below needs somewhere to put back a
            // widget that the old layout had nothing to say about.
            node._mmrpWidgetDefaults = {};
            for (const w of node.widgets || []) {
                if (w && w.name && typeof w.value !== "function") {
                    node._mmrpWidgetDefaults[w.name] = w.value;
                }
            }

            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (info) {
                const out = origOnConfigure ? origOnConfigure.apply(this, arguments) : undefined;
                // configure() has just applied widgets_values POSITIONALLY, which for any
                // graph saved before 0.3.3 means the values landed in the wrong widgets.
                // The original array is still on `info`, so re-place it by name from the
                // layout it was actually written in.
                const saved = info && info.widgets_values;
                const byName = remapWidgetValues(saved);
                // Was `!== ORDER_0_3_3`. A 0.3.3 array is now a strict PREFIX of the
                // current layout, so positional restore has already put every one of its
                // values in the right widget - remapping it would be a no-op that still
                // logged "migrated_layout" and made a workflow needing nothing look like
                // one that had been repaired. A shorter array also leaves the widgets
                // appended since at their defaults, which is where they belong.
                if (byName && !isPrefixOfCurrent(detectLayout(saved))) {
                    const names = (this.widgets || [])
                        .map((w) => w && w.name).filter(Boolean);
                    const plan = migrationPlan(saved, names, this._mmrpWidgetDefaults);
                    for (const [name, value] of Object.entries(plan || byName)) {
                        setWidget(this, name, value);
                    }
                    console.log(
                        `[MiniMaxRefPack] event=migrated_layout slots=${saved.length} ` +
                        `provider=${byName.prompt_provider}`
                    );
                } else if (migrateProviderWidget(widgetByName(this, "prompt_provider"))) {
                    console.log(
                        "[MiniMaxRefPack] event=migrated_provider from=use_openrouter " +
                        `to=${widgetByName(this, "prompt_provider").value}`
                    );
                }
                // After the migration above, so a 0.3.1 graph's `true` has already become
                // "openrouter" and the button is hidden rather than flickering on.
                watchProviderWidget(this);
                syncLocalBtn(this);
                const rw = widgetByName(this, "references_json");
                stopPreview(this);
                this._mmrpRefs = parseRefsValue(rw, this);
                const dw = widgetByName(this, "direction");
                if (this._mmrpBody) this._mmrpBody.directionInput.value = dw ? dw.value || "" : "";
                this._mmrpSelected = null;
                // parseRefsValue may have restored a stored prompt height off the
                // envelope; the box has to be told before the size is re-derived.
                syncPromptHeight(this);
                // The picker is pinned to an index, and this replaces every reference
                // under it - an open one would reopen on whatever now occupies that slot.
                this._mmrpPicker = null;
                // configure() writes the serialized size straight onto node.size,
                // bypassing our setSize wrapper — re-assert the fixed size.
                setSizeInternal(this);
                renderNodeBody(this);
                return out;
            };

            // last_y isn't assigned until litegraph's first layout pass — re-assert
            // the fixed size once it exists so the height lands on the real widget top.
            setTimeout(() => {
                setSizeInternal(node);
                // From here the node's height is its real one, so a further change is a
                // user gesture rather than the frontend still laying it out.
                node._mmrpSizeReady = true;
                app.graph?.setDirtyCanvas(true, true);
                scheduleDraw(node);
            }, 50);
        };
    },
});
