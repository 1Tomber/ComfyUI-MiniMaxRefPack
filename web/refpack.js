/**
 * MiniMax H3 Reference Pack — native widgets (openrouter_api_key, model) stay on top,
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
 * FIXED SIZE, per spec: "I would like this node to have a fixed size that cannot be
 * moved" / "I don't want the canvas to change at all — always the same row. It just
 * adds or removes content." So: node.resizable = false, and onResize / setSize /
 * computeSize all clamp to fixedSize() regardless of input — a restored workflow, a
 * paste, or a frontend quirk cannot put the node at another size. The three rows are
 * always drawn at full tile height whether they hold 0 or 9 items; only their
 * CONTENTS change. Every piece of machinery that existed to cope with a changing
 * size (content measurement, resize-driven relayout, width floors, the
 * restored-size protection) is deleted — the geometry below is arithmetic over
 * constants, not measurement.
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
 */

import { app } from "../../scripts/app.js";

const NODE_NAME = "MiniMaxH3ReferencePack";
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
    // The per-row add affordance: an icon-only square, NOT a toolbar-button clone
    // (UI review #1). Fixed placement — always where the next tile would go, with
    // a 24px gap separating it from the reference group so it doesn't read as
    // another tile; it never centres itself and only ever moves rightwards.
    addBtn: 44,
    addGap: 24,
};
CL.stripH = CL.stripPad * 2 + CL.tile;

// The rows never move: every section is always drawn at full tile height, 0 items or
// 9. Static layout, computed once — this is the whole point of the fixed-size node.
const CANVAS_ROWS = (() => {
    const rows = [];
    let y = CL.padTop;
    for (const kind of KINDS) {
        rows.push({ kind, y, stripY: y + CL.headerH + CL.headGap });
        y += CL.headerH + CL.headGap + CL.stripH + CL.rowGap;
    }
    // The trailing rowGap doubles as the slab's bottom padding.
    return { rows, height: y };
})();

// Fixed block geometry. The DOM heights (uploadsH, promptH, gap) are pinned in
// refpack.css to the same values — the two must agree, there is no measurement.
// THE WIDTH PRESERVES THE NO-SCROLL INVARIANT: the longest row is 9 image tiles
// (9×131 + 8×6 = 1227px) plus the 24px add-gap and the 44px add square, which is
// drawn dimmed even at cap = 1295px of row viewport; +2×10 slab padding = 1315
// canvas; +2×10 block inset = 1335 node — fixed at 1340 for slack. That invariant
// is why no scroll machinery exists; change tile/gap/x0/pad/addBtn/addGap only
// together with this width.
const CONTENT = {
    width: 1340,
    pad: 10, // side inset of the DOM block within the node
    uploadsH: 30, // .mmrp-uploads fixed height
    promptH: 110, // .mmrp-direction fixed height — clearly smaller than a canvas row
    gap: 8, // .mmrp-block flex gap
};
CONTENT.height =
    CONTENT.uploadsH + CONTENT.gap + CANVAS_ROWS.height + CONTENT.gap + CONTENT.promptH;

// The ONE node size. Height floors against the 19 output sockets; widgetY is where
// litegraph placed the DOM block below the native widgets (fallback for the beat
// before the first layout assigns last_y).
function fixedSize(node) {
    const widgetY = (node._mmrpDomWidget && node._mmrpDomWidget.last_y) || 80;
    const outputsMin = ((node.outputs && node.outputs.length) || 1) * 20 + 40;
    return [CONTENT.width, Math.max(widgetY + CONTENT.height + CONTENT.pad, outputsMin)];
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

// ---- references_json <-> working-state conversion --------------------
// refs.py's Reference shape: {"kind": "image"|"video"|"audio", "file": str, [use_soundtrack]}

function fromReferencesList(list) {
    const refs = { images: [], videos: [], audios: [] };
    for (const r of list || []) {
        if (!r || typeof r.file !== "string") continue;
        if (r.kind === "image") refs.images.push({ file: r.file, missing: !!r.missing });
        else if (r.kind === "video")
            refs.videos.push({ file: r.file, use_soundtrack: !!r.use_soundtrack, missing: !!r.missing });
        else if (r.kind === "audio") refs.audios.push({ file: r.file, missing: !!r.missing });
    }
    return refs;
}

function toReferencesList(refs) {
    const out = [];
    for (const r of refs.images) out.push({ kind: "image", file: r.file });
    for (const r of refs.videos) out.push({ kind: "video", file: r.file, use_soundtrack: !!r.use_soundtrack });
    for (const r of refs.audios) out.push({ kind: "audio", file: r.file });
    return out;
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

function thumbUrl(file) {
    return `/minimax_refpack/thumb?file=${encodeURIComponent(file)}`;
}

// Raw file, for the click-to-play previews (thumbUrl is a server-generated still).
// Stock ComfyUI route — confirmed at server.py:511 (`@routes.get("/view")`, no /api prefix).
function fileUrl(file) {
    return `/view?filename=${encodeURIComponent(file)}&type=input`;
}

// NOTE: there is no apiSavePack/apiLoadPack/apiListPacks any more. Configs are files
// on the user's own machine (download + file picker, see "Config save/load" below),
// so the /minimax_refpack/packs routes are no longer called from the UI at all.

async function apiSystemPromptDefault() {
    const res = await fetch("/minimax_refpack/system_prompt");
    if (!res.ok) throw new Error(`fetch failed: ${res.status}`);
    const data = await res.json();
    return data.default || "";
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
                    const next = cloneRefs(node._mmrpRefs);
                    next.videos.find((v) => v.file === file).use_soundtrack = false;
                    applyRefs(node, next);
                    return; // applyRefs already repaints
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

const thumbCache = new Map(); // file -> { img, state: "loading"|"ok"|"error" }
const liveNodes = new Set();

function getThumb(file) {
    let entry = thumbCache.get(file);
    if (!entry) {
        const img = new Image();
        entry = { img, state: "loading" };
        img.onload = () => {
            entry.state = "ok";
            for (const n of liveNodes) scheduleDraw(n);
        };
        img.onerror = () => {
            entry.state = "error";
            for (const n of liveNodes) scheduleDraw(n);
        };
        img.src = thumbUrl(file);
        thumbCache.set(file, entry);
    }
    return entry;
}

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

// One tile: well, thumbnail/glyph, tag badge, delete, soundtrack state, play glyph,
// missing treatment, selection stroke. `lines` is the pre-computed badge text (two
// lines for a video that also owns an <Audio N>); `playState` is null | "play" |
// "pause" (null = no preview affordance: images, missing refs).
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
        const entry = getThumb(ref.file);
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

function draw(node) {
    const body = node._mmrpBody;
    if (!body) return;
    const canvas = body.canvas;
    const ctx = body.ctx;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    // Not attached / zero-sized yet (V3 mounts the DOM widget asynchronously) —
    // the ResizeObserver fires once real dimensions arrive and repaints then.
    if (!cssW || !cssH) return;

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

    for (const row of CANVAS_ROWS.rows) {
        const kind = row.kind;
        const arr = refs[`${kind}s`];
        const taggedArr = tagged[`${kind}s`];
        const atCap = arr.length >= CAPS[kind];
        const tileY = row.stripY + CL.stripPad;

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
            const tx = CL.x0 + i * (CL.tile + CL.gap);
            const t = taggedArr[i];
            const lines = kind === "video" && t.audioTag ? [t.tag, t.audioTag] : [t.tag];
            const badgeH = lines.length > 1 ? CL.badge2 : CL.badge1;
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

            drawTile(ctx, kind, ref, lines, tx, tileY, selected, soundState, badgeH, playState);

            // Hit regions, most specific last — hitTest scans in reverse so the
            // play/delete/soundtrack affordances win over the tile containing them.
            regions.push({ type: "tile", kind, index: i, file: ref.file, x: tx, y: tileY, w: CL.tile, h: CL.tile });
            if (soundState === "on" || soundState === "off") {
                regions.push({
                    type: "sound",
                    kind,
                    index: i,
                    file: ref.file,
                    x: tx + 3,
                    y: tileY + CL.tile - badgeH - CL.sound - 3,
                    w: CL.sound,
                    h: CL.sound,
                });
            }
            regions.push({
                type: "del",
                kind,
                index: i,
                file: ref.file,
                x: tx + CL.tile - CL.del - 3,
                y: tileY + 3,
                w: CL.del,
                h: CL.del,
            });
            if (playState) {
                const cy = kind === "audio" ? tileY + 80 : tileY + 58;
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
                    tileY,
                });
            }
        });

        // FIXED placement (UI review #2): always where the next tile would go —
        // the row's left edge when empty, after the last tile (plus the 24px
        // separating gap) otherwise. It never centres itself, so it never jumps;
        // it only ever moves rightwards as tiles are added. Vertically centred
        // on the strip. Drawn dimmed at cap, and only clickable below cap.
        const ax = arr.length
            ? CL.x0 + arr.length * CL.tile + (arr.length - 1) * CL.gap + CL.addGap
            : CL.x0;
        const ay = tileY + Math.round((CL.tile - CL.addBtn) / 2);
        drawAddSquare(ctx, ax, ay, atCap);
        if (!atCap) {
            regions.push({ type: "add", kind, x: ax, y: ay, w: CL.addBtn, h: CL.addBtn });
        }
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

function hitTest(node, x, y) {
    const hit = node._mmrpHit;
    if (!hit) return null;
    for (let i = hit.regions.length - 1; i >= 0; i--) {
        const r = hit.regions[i];
        if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) return r;
    }
    return null;
}

function onCanvasMouseDown(node, e) {
    if (e.button !== 0) return;
    const pos = getMousePos(node._mmrpBody.canvas, e);
    const hit = hitTest(node, pos.x, pos.y);
    if (!hit) {
        if (node._mmrpSelected) {
            node._mmrpSelected = null;
            scheduleDraw(node);
        }
        return;
    }
    if (hit.type === "del") {
        removeRef(node, hit.kind, hit.index);
    } else if (hit.type === "sound") {
        toggleSoundtrack(node, hit.file);
    } else if (hit.type === "play") {
        togglePreview(node, hit);
    } else if (hit.type === "add") {
        node._mmrpBody.fileInputs[hit.kind].click();
    } else {
        node._mmrpSelected = { kind: hit.kind, index: hit.index };
        scheduleDraw(node);
    }
}

function toggleSoundtrack(node, file) {
    if (probeResults.get(file) !== true) return; // pending/silent/unknown — not toggleable
    const next = cloneRefs(node._mmrpRefs);
    const target = next.videos.find((v) => v.file === file);
    if (target) target.use_soundtrack = !target.use_soundtrack;
    applyRefs(node, next);
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
    if (hit.kind === "video") {
        const v = body.previewVideo;
        v.src = fileUrl(hit.file);
        // Same coordinate space as the canvas — the block is the offsetParent for
        // both, and ComfyUI's zoom transform applies to the overlay automatically.
        v.style.left = `${body.canvas.offsetLeft + hit.tileX}px`;
        v.style.top = `${body.canvas.offsetTop + hit.tileY}px`;
        v.style.display = "block";
        v.play().catch(() => stopPreview(node));
    } else {
        const a = body.previewAudio;
        a.src = fileUrl(hit.file);
        a.play().catch(() => stopPreview(node));
    }
    node._mmrpPlaying = { kind: hit.kind, file: hit.file };
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
function parseRefsValue(widget) {
    if (!widget) return emptyRefs();
    const raw = widget.value;
    if (typeof raw !== "string" || !raw.trim()) return emptyRefs();
    try {
        const parsed = JSON.parse(raw);
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
    w.value = JSON.stringify({ references: list });
}

function applyRefs(node, refs) {
    stopPreview(node); // tile positions shift with any change — never leave the overlay stale
    node._mmrpRefs = refs;
    syncReferencesWidget(node);
    renderNodeBody(node);
}

async function addFiles(node, kind, files) {
    const refs = cloneRefs(node._mmrpRefs);
    const arr = refs[`${kind}s`];
    const room = CAPS[kind] - arr.length;
    if (room <= 0) return;
    const all = Array.from(files);
    const take = all.slice(0, room);
    // Caps are hard, but never SILENTLY hard — say what didn't fit.
    if (take.length < all.length) {
        alert(`Only ${room} ${kind} slot${room === 1 ? "" : "s"} left — skipped ${all.length - take.length} file(s).`);
    }
    for (const file of take) {
        try {
            const name = await apiUpload(file);
            arr.push(kind === "video" ? { file: name, use_soundtrack: false } : { file: name });
        } catch (e) {
            alert(`Upload failed for ${file.name}: ${e.message}`);
        }
    }
    applyRefs(node, refs);
}

function removeRef(node, kind, index) {
    const refs = cloneRefs(node._mmrpRefs);
    refs[`${kind}s`].splice(index, 1);
    if (node._mmrpSelected && node._mmrpSelected.kind === kind) {
        if (node._mmrpSelected.index === index) node._mmrpSelected = null;
        else if (node._mmrpSelected.index > index) node._mmrpSelected.index -= 1;
    }
    applyRefs(node, refs);
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

    const gearBtn = document.createElement("button");
    gearBtn.className = "mmrp-btn mmrp-gear-btn";
    gearBtn.textContent = "⚙";
    gearBtn.title = "System prompt settings";
    gearBtn.onclick = () => openSystemPromptModal(node);
    uploadRow.appendChild(gearBtn);

    container.appendChild(uploadRow);

    const canvas = document.createElement("canvas");
    canvas.className = "mmrp-canvas";
    canvas.style.height = `${CANVAS_ROWS.height}px`; // fixed, set once
    canvas.addEventListener("mousedown", (e) => onCanvasMouseDown(node, e));
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
        model: w("model"),
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
        restore("model", data.model),
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
// System prompt modal — the ONLY place `system_prompt` is ever shown. The widget
// itself is hidden on the node body like direction/references_json (onNodeCreated).
// "Load default" only fills the textarea from the server's packaged default; nothing
// commits to the widget until Save & close, so the user can edit from that starting
// point without losing their in-progress edits by opening/closing the modal.
// ---------------------------------------------------------------------------

function openSystemPromptModal(node) {
    const systemPromptWidget = widgetByName(node, "system_prompt");

    const overlay = document.createElement("div");
    overlay.className = "mmrp-overlay";
    overlay.onclick = (e) => {
        if (e.target === overlay) overlay.remove();
    };

    const modal = document.createElement("div");
    modal.className = "mmrp-modal";
    overlay.appendChild(modal);

    const header = document.createElement("div");
    header.className = "mmrp-modal-header";
    header.textContent = "System prompt";
    modal.appendChild(header);

    const hint = document.createElement("div");
    hint.className = "mmrp-modal-hint";
    hint.textContent = "Empty = use the built-in default.";
    modal.appendChild(hint);

    const textarea = document.createElement("textarea");
    textarea.className = "mmrp-modal-textarea";
    textarea.spellcheck = false;
    textarea.value = systemPromptWidget ? systemPromptWidget.value || "" : "";
    modal.appendChild(textarea);

    // A blank widget means "use the packaged default", but an empty box hides WHICH
    // prompt is running. Show it, so the modal is the node's prompt rather than a
    // guess about it. Saving an untouched prefill is a no-op: Save writes the
    // textarea back only if it differs from the default (see saveBtn).
    let prefilledDefault = "";
    if (!textarea.value.trim()) {
        hint.textContent = "Showing the built-in default. Edit to override it for this workflow.";
        apiSystemPromptDefault()
            .then((text) => {
                // Don't clobber anything the user typed while the fetch was in flight.
                if (!textarea.value.trim()) {
                    prefilledDefault = text;
                    textarea.value = text;
                }
            })
            .catch(() => {
                hint.textContent = "Empty = use the built-in default.";
            });
    }

    const footer = document.createElement("div");
    footer.className = "mmrp-modal-footer";

    const loadDefaultBtn = document.createElement("button");
    loadDefaultBtn.className = "mmrp-btn";
    loadDefaultBtn.textContent = "Load default";
    loadDefaultBtn.onclick = async () => {
        try {
            textarea.value = await apiSystemPromptDefault();
        } catch (e) {
            alert(`Couldn't load the default: ${e.message}`);
        }
    };

    const resetBtn = document.createElement("button");
    resetBtn.className = "mmrp-btn";
    resetBtn.textContent = "Reset";
    resetBtn.title = "Discard edits, revert to the last-saved value";
    resetBtn.onclick = () => {
        textarea.value = systemPromptWidget ? systemPromptWidget.value || "" : "";
    };

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "mmrp-btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = () => overlay.remove();

    const saveCloseBtn = document.createElement("button");
    saveCloseBtn.className = "mmrp-btn mmrp-btn-primary";
    saveCloseBtn.textContent = "Save & close";
    saveCloseBtn.onclick = () => {
        if (systemPromptWidget) {
            // An untouched prefill saves as blank, so the workflow keeps tracking the
            // packaged prompt instead of freezing today's copy into the graph.
            const v = textarea.value;
            systemPromptWidget.value = prefilledDefault && v === prefilledDefault ? "" : v;
        }
        overlay.remove();
    };

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
        liveNodes.delete(node);
        if (origOnRemoved) origOnRemoved.apply(this, arguments);
    };
}

// ---------------------------------------------------------------------------
// Fixed-size enforcement. resizable = false removes litegraph's resize handle;
// the three overrides are belt and braces so NOTHING — a restored workflow, a
// paste, a frontend quirk — can put the node at any other size. This supersedes
// the old "never stomp a restored size" rule: there is no user-chosen size.
// ---------------------------------------------------------------------------

function installSizeGuards(node) {
    node.resizable = false;

    const origOnResize = node.onResize;
    node.onResize = function (size) {
        const f = fixedSize(this);
        size[0] = f[0];
        size[1] = f[1];
        if (origOnResize) origOnResize.call(this, size);
    };

    const origComputeSize = node.computeSize;
    node.computeSize = function () {
        // origComputeSize still runs for its side effects on some frontends, but its
        // answer is discarded — the size is a constant.
        if (origComputeSize) origComputeSize.apply(this, arguments);
        const f = fixedSize(this);
        this.min_size = [f[0], f[1]];
        return [f[0], f[1]];
    };

    const origSetSize = node.setSize;
    node.setSize = function (size) {
        const f = fixedSize(this);
        size[0] = f[0];
        size[1] = f[1];
        if (origSetSize) origSetSize.call(this, size);
        else this.size = size;
    };
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
            node._mmrpRefs = parseRefsValue(refsWidget);
            const ivRefs = hideWidget(refsWidget);

            const directionWidget = widgetByName(node, "direction");
            const ivDirection = hideWidget(directionWidget);

            const systemPromptWidget = widgetByName(node, "system_prompt");
            const ivSystemPrompt = hideWidget(systemPromptWidget);

            // Cleared early in installSelectionHandlers' onRemoved wrapper so a deleted
            // node doesn't leave a hide-poll timer running (they also self-clear after
            // 1s regardless, this just avoids the wait on an early delete).
            node._mmrpHideIntervals = [ivRefs, ivDirection, ivSystemPrompt].filter((id) => id !== undefined);

            const bodyEl = buildCustomBlock(node);
            const domWidget = node.addDOMWidget("mmrp_block", "custom", bodyEl, { serialize: false });
            node._mmrpDomWidget = domWidget;
            // Constant, by construction: the CSS pins the DOM heights this arithmetic
            // assumes (uploadsH/promptH/gap), the canvas height is CANVAS_ROWS.height.
            domWidget.computeSize = () => [CONTENT.width - CONTENT.pad * 2, CONTENT.height];
            liveNodes.add(node);

            node._mmrpBody.directionInput.value = directionWidget ? directionWidget.value || "" : "";
            node._mmrpBody.directionInput.addEventListener("input", () => {
                if (!directionWidget) return;
                directionWidget.value = node._mmrpBody.directionInput.value;
                if (directionWidget.callback) directionWidget.callback(directionWidget.value);
            });

            installSizeGuards(node);
            installSelectionHandlers(node);
            renderNodeBody(node);
            syncReferencesWidget(node);

            const origOnConfigure = node.onConfigure;
            node.onConfigure = function (info) {
                const out = origOnConfigure ? origOnConfigure.apply(this, arguments) : undefined;
                const rw = widgetByName(this, "references_json");
                stopPreview(this);
                this._mmrpRefs = parseRefsValue(rw);
                const dw = widgetByName(this, "direction");
                if (this._mmrpBody) this._mmrpBody.directionInput.value = dw ? dw.value || "" : "";
                this._mmrpSelected = null;
                // configure() writes the serialized size straight onto node.size,
                // bypassing our setSize wrapper — re-assert the fixed size.
                this.setSize(fixedSize(this));
                renderNodeBody(this);
                return out;
            };

            // last_y isn't assigned until litegraph's first layout pass — re-assert
            // the fixed size once it exists so the height lands on the real widget top.
            setTimeout(() => {
                node.setSize(fixedSize(node));
                app.graph?.setDirtyCanvas(true, true);
                scheduleDraw(node);
            }, 50);
        };
    },
});
