"use strict";
// Replays ComfyUI frontend 1.51.9's resize drag AND its native widget-layout path
// (frontend.js, ported verbatim from the bundle with offsets cited) against the REAL
// functions lifted out of web/refpack.js (extract.js), over the scenarios below.
//
// This exists because the node's height drag was got wrong five times in a row, and every
// wrong version looked right until a live drag disproved it. The passed height turns out
// not to carry the user's intent - the frontend clamps it up to computeSize() on both
// axes, so a width drag that wraps a tile is byte-identical to a deliberate drag to the
// floor - and each fix that reasoned about that number leaked somewhere new.
//
// It is also the answer to a problem we cannot otherwise solve: the drag is ComfyUI's
// machinery, and the code under test reads fields (`resizing_node`, `pointer.eDown`,
// `pointer.resizeDirection`) that are theirs to change. We cannot own their internals, so
// we own a test that fails loudly when they move, instead of a user discovering it.
//
//   node tests/resize_harness/run.js          # exits non-zero if any scenario fails
//   REFPACK_PATH=... node .../run.js          # compare another checkout
const { makeSandbox } = require("./extract.js");
const FE = require("./frontend.js");
const M = require("./models.js");

// ---------------------------------------------------------------------------
// World construction
// ---------------------------------------------------------------------------
function makeWorld(model) {
    const app = { canvas: null, graph: { setDirtyCanvas() {} } };
    const ctx = {
        app,
        getComputedStyle: () => ({ columnGap: "8px", gap: "8px" }),
        schedulePersistPromptH: (node) => { node._persistCalls = (node._persistCalls || 0) + 1; },
    };
    const S = makeSandbox(ctx);
    const canvas = new FE.LGraphCanvas();
    app.canvas = canvas;
    const world = { app, S, canvas, model };
    world.draw = (node) => FE.drawFrame(node, { vueNodesMode: false });
    world.promptOf = (node) => (model === "D" ? M.promptOfD(S, node) : S.promptHeightOf(node));
    // Content change entry point. A/B/C: relayout() -> setSizeInternal (the shipped
    // path). D: the design's media-delta compensation.
    world.applyContentChange = (node, mutate) => {
        if (model === "D") M.applyContentChangeD(S, node, mutate);
        else { mutate(); S.setSizeInternal(node); }
    };
    // What onConfigure does after configure() wrote the serialized size straight onto
    // node.size. A/B/C: re-assert the derived size (refpack onConfigure). D: nothing —
    // the serialized size IS the state.
    world.reassert = (node) => { if (model !== "D") S.setSizeInternal(node); };
    return world;
}

// A node shaped like the live one: 20 outputs (refs.py output_names(): image_1..9,
// video_1..3, video_audio_1..3, audio_1..3, prompt, debug), all inputs widget-backed
// (skipped by _measureSlots and by computeSize's rows count), 2 visible native
// widgets, 4 hideWidget()-hidden widgets (hidden locked true, computeSize [0,0] —
// refpack :824-869; getLayoutWidgets and isWidgetVisible both exclude them, cost 0),
// and the dom widget. Uploads row measures 560 -> minNodeWidth 600.
const OUTPUT_NAMES = [
    ...Array.from({ length: 9 }, (_, i) => `image_${i + 1}`),
    ...Array.from({ length: 3 }, (_, i) => `video_${i + 1}`),
    ...Array.from({ length: 3 }, (_, i) => `video_audio_${i + 1}`),
    ...Array.from({ length: 3 }, (_, i) => `audio_${i + 1}`),
    "prompt", "debug",
];

function makeNode(world, model, { images = 7, width = 800, promptH = null } = {}) {
    const { S, app } = world;
    const node = new FE.LGraphNode("MiniMax H3 Reference Pack");
    node._mmrpRefs = {
        images: Array.from({ length: images }, (_, i) => ({ file: `img${i}` })),
        videos: [], audios: [],
    };
    node._mmrpBody = {
        uploadRow: { children: [{ offsetWidth: 560 }] },
        directionInput: { style: {}, value: "" },
        canvas: { style: {} },
    };
    node.outputs = OUTPUT_NAMES.map((name) => ({ name }));
    node.inputs = []; // every input is widget-backed -> invisible to slot measuring

    const domWidget = { name: "mmrp_block", margin: 10, options: {} };
    node._mmrpDomWidget = domWidget;
    const native = (name) => ({ name, computeSize: () => [0, 20] });
    const hidden = (name) => ({ name, hidden: true, computeSize: () => [0, 0] });
    node.widgets = [
        native("prompt_provider"), native("job_type"),
        hidden("references_json"), hidden("direction"),
        hidden("system_prompt"), hidden("system_prompt_replacement"),
        domWidget,
    ];

    node.size = [width, 400]; // litegraph default-ish; settled below

    if (model === "D") {
        M.installD(S, node, FE);
        M.settleD(S, node, FE, width, world.draw);
    } else {
        // Verbatim mirror of the shipped onNodeCreated (refpack.snapshot.js): the dom
        // widget's computeSize reports the pre-compensated height.
        domWidget.computeSize = () => {
            const margin = S.domWidgetMargin(node);
            const reported = S.reportedHeightFor(node, S.contentHeight(node));
            domWidget._mmrpReported = reported;
            return [S.nodeSize(node)[0] - margin * 2, reported];
        };
        if (promptH != null) { node._mmrpPromptH = promptH; node._mmrpPromptRaw = promptH; }
        if (model === "A") M.installA(S, node);
        else if (model === "B") M.installB(S, node);
        else M.installC(S, node, app);
        // onNodeCreated settle: setSizeInternal + sizeReady
        S.setSizeInternal(node);
        node._mmrpSizeReady = true;
    }
    world.draw(node);
    world.draw(node);
    return node;
}

// ---------------------------------------------------------------------------
// Drag driver — absolute frames, exactly what the browser produces.
// ---------------------------------------------------------------------------
let EVT_T = 1000;
function evt(x, y, { buttons = 1, button = 0 } = {}) {
    EVT_T += 50; // > CanvasPointer.bufferTime so dragStarted triggers on the 1st move
    return {
        canvasX: x, canvasY: y, clientX: x, clientY: y,
        buttons, button, timeStamp: EVT_T, isPrimary: true, pointerId: 1,
    };
}

function cornerPoint(node, corner) {
    node.updateArea();
    const br = node.boundingRect;
    const inset = 5; // inside the 15px handle
    const x = corner.includes("W") ? br.x + inset : br.right - inset;
    const y = corner.includes("N") ? br.y + inset : br.bottom - inset;
    return [x, y];
}

// path: list of [dx, dy] TOTAL displacements from the grab point (absolute frames).
// Interleaves a full draw (arrange + last_y mirror + DomWidgets.vue sizing) between
// mousemoves.
function drag(world, node, corner, path, { cancel = false } = {}) {
    const { canvas } = world;
    const [gx, gy] = cornerPoint(node, corner);
    const down = evt(gx, gy);
    down.__node = node;
    canvas.processMouseDown(down);
    let last = [gx, gy];
    for (const [dx, dy] of path) {
        last = [gx + dx, gy + dy];
        canvas.processMouseMove(evt(last[0], last[1]));
        world.draw(node);
    }
    if (cancel) canvas.processMouseCancel();
    else canvas.processMouseUp(evt(last[0], last[1], { buttons: 0 }));
    world.draw(node);
    world.draw(node);
}

const snap = (world, node) => ({
    promptH: world.promptOf(node),
    rawH: node._mmrpPromptRaw,
    w: node.size[0], h: node.size[1],
    x: node.pos[0], y: node.pos[1],
    bottom: node.pos[1] + node.size[1],
    perRow: node._mmrpLayout ? node._mmrpLayout.perRow : null,
});

const pushImages = (node, prefix, n) => {
    for (let i = 0; i < n; i++) node._mmrpRefs.images.push({ file: `${prefix}${i}` });
};

// ---------------------------------------------------------------------------
// Scenarios. Each returns { pass, detail }.
// ---------------------------------------------------------------------------
const scenarios = {
    "S1 height drag down then back to exact start": (world, model) => {
        const n = makeNode(world, model);
        const before = snap(world, n);
        drag(world, n, "SE", [[0, 60], [0, 140], [0, 200], [0, 90], [0, 0]]);
        const after = snap(world, n);
        const pass = after.promptH === before.promptH && after.h === before.h && after.w === before.w;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, node h ${before.h}->${after.h}` };
    },

    "S2 height drag to the floor and back": (world, model) => {
        const { S } = world;
        const n = makeNode(world, model);
        const before = snap(world, n);
        drag(world, n, "SE", [[0, -150], [0, -400], [0, -600], [0, -300], [0, 0]]);
        const after = snap(world, n);
        const pass = after.promptH === before.promptH && after.h === before.h;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH} (floor is ${S.CONTENT.minPromptH})` };
    },

    "S2b release at floor, then a NEW drag up must grow immediately": (world, model) => {
        const n = makeNode(world, model);
        drag(world, n, "SE", [[0, -200], [0, -600]]); // released at the floor
        const atFloor = snap(world, n);
        drag(world, n, "SE", [[0, 25], [0, 50]]);      // fresh drag, +50
        const after = snap(world, n);
        const expected = atFloor.promptH + 50;
        const pass = after.promptH === expected;
        return { pass, detail: `at floor ${atFloor.promptH} (raw ${atFloor.rawH}), +50 drag -> ${after.promptH}, expected ${expected}` };
    },

    "S3 width-only drag: narrow until tiles wrap, widen back — prompt must not move": (world, model) => {
        const n = makeNode(world, model);
        const before = snap(world, n);
        const seen = [];
        const origDraw = world.draw;
        world.draw = (nd) => { origDraw(nd); seen.push(world.promptOf(nd)); };
        drag(world, n, "SE", [[-60, 0], [-120, 0], [-180, 0], [-120, 0], [-60, 0], [0, 0]]);
        world.draw = origDraw;
        const after = snap(world, n);
        const drift = seen.filter((p) => p !== before.promptH);
        const pass = after.promptH === before.promptH && drift.length === 0
            && after.w === before.w && after.h === before.h;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, mid-drag deviations: ${drift.length ? drift.join(",") : "none"}, perRow ${before.perRow}->${after.perRow}` };
    },

    "S4 diagonal (corner) drag changing both axes": (world, model) => {
        const n = makeNode(world, model);
        const before = snap(world, n);
        drag(world, n, "SE", [[60, 40], [150, 140]]);
        const after = snap(world, n);
        const pass = after.promptH === before.promptH + 140 && after.w === before.w + 150;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH} (want ${before.promptH + 140}), w ${before.w}->${after.w} (want ${before.w + 150})` };
    },

    "S4b diagonal narrow+shrink while the wrap raises the floor": (world, model) => {
        const { S } = world;
        const n = makeNode(world, model);
        const before = snap(world, n);
        // narrow enough to wrap 5/row -> 3/row (+137px media) while dragging up 30
        drag(world, n, "SE", [[-90, -10], [-180, -30]]);
        const after = snap(world, n);
        const expected = Math.max(S.CONTENT.minPromptH, before.promptH - 30);
        const pass = after.promptH === expected;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, expected ${expected} (user dragged dy=-30)` };
    },

    "S5 drag that starts, moves, and is cancelled": (world, model) => {
        const n = makeNode(world, model);
        const before = snap(world, n);
        drag(world, n, "SE", [[0, 50], [0, 100]], { cancel: true });
        const afterCancel = snap(world, n);
        for (let i = 0; i < 5; i++) world.draw(n);
        const settled = snap(world, n);
        // contract: the resize applied so far sticks (litegraph does not roll back);
        // nothing may keep moving after the cancel, and the canvas must be released.
        const pass = afterCancel.promptH === before.promptH + 100
            && settled.promptH === afterCancel.promptH
            && settled.h === afterCancel.h
            && world.canvas.resizing_node === null;
        return { pass, detail: `prompt ${before.promptH}->${afterCancel.promptH} at cancel, ${settled.promptH} after 5 draws; resizing_node=${world.canvas.resizing_node}` };
    },

    "S6 create, serialize, reload, draw repeatedly — nothing drifts": (world, model) => {
        const n = makeNode(world, model);
        for (let i = 0; i < 4; i++) world.draw(n);
        const saved = {
            w: n.size[0], h: n.size[1],
            prompt: world.promptOf(n),
            field: n._mmrpPromptH ?? null, // what the envelope would carry (A/B/C)
        };
        // reload into a FRESH world (fresh canvas, same model)
        const world2 = makeWorld(model);
        const n2 = makeNode(world2, model, { width: saved.w, promptH: saved.field ?? undefined });
        // configure() writes the serialized size straight onto node.size (bypasses setSize)
        n2._size[0] = saved.w; n2._size[1] = saved.h;
        world2.reassert(n2);       // refpack onConfigure re-assert (A/B/C); no-op for D
        FE.afterLoadPass(n2);      // bundle @1619152 post-load pass
        for (let i = 0; i < 6; i++) world2.draw(n2);
        const after = snap(world2, n2);
        const pass = after.w === saved.w && after.h === saved.h
            && after.promptH === saved.prompt;
        return { pass, detail: `saved ${saved.w}x${saved.h} prompt ${saved.prompt}; reloaded ${after.w}x${after.h} prompt ${after.promptH}` };
    },

    "S7 two managers on one canvas, dragged independently": (world, model) => {
        const a = makeNode(world, model);
        const b = makeNode(world, model);
        b.pos = [1200, 0];
        const a0 = snap(world, a), b0 = snap(world, b);
        drag(world, a, "SE", [[0, 50], [0, 100]]);
        drag(world, b, "SE", [[0, -20], [0, -40]]);
        drag(world, a, "SE", [[-90, 0], [-180, 0], [0, 0]]); // width-only on A
        const a1 = snap(world, a), b1 = snap(world, b);
        const pass = a1.promptH === a0.promptH + 100 && b1.promptH === b0.promptH - 40
            && a1.w === a0.w;
        return { pass, detail: `A prompt ${a0.promptH}->${a1.promptH} (want ${a0.promptH + 100}), B ${b0.promptH}->${b1.promptH} (want ${b0.promptH - 40})` };
    },

    "S8 NW drag, narrow-only (wrap raises floor) — prompt still, bottom anchored": (world, model) => {
        const n = makeNode(world, model);
        n.pos = [500, 300];
        const before = snap(world, n);
        // NW: width = start - dx, so dx +180 narrows to 620 (5/row -> 3/row)
        drag(world, n, "NW", [[90, 0], [180, 0]]);
        const after = snap(world, n);
        const pass = after.promptH === before.promptH && Math.abs(after.bottom - before.bottom) < 1;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, bottom ${before.bottom}->${after.bottom}` };
    },

    "S9 NW drag far below the floor — bottom must stay anchored": (world, model) => {
        const { S } = world;
        const n = makeNode(world, model);
        n.pos = [500, 300];
        const before = snap(world, n);
        // dragging the TOP edge DOWN (dy +800) shrinks an NW resize far below the floor
        drag(world, n, "NW", [[0, 300], [0, 800]]);
        const after = snap(world, n);
        const bottomDrift = after.bottom - before.bottom;
        const pass = after.promptH === S.CONTENT.minPromptH && Math.abs(bottomDrift) < 1;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH} (want ${S.CONTENT.minPromptH}), bottom drift ${bottomDrift.toFixed(1)}px` };
    },

    "S10 content grows between mousedown and first mousemove": (world, model) => {
        const { canvas } = world;
        const n = makeNode(world, model);
        const before = snap(world, n);
        const [gx, gy] = cornerPoint(n, "SE");
        const down = evt(gx, gy); down.__node = n;
        canvas.processMouseDown(down);
        // async thumb/probe answer lands: four more images wrap a new line (+137px)
        world.applyContentChange(n, () => pushImages(n, "late", 4));
        world.draw(n);
        // now the user drags down 50
        canvas.processMouseMove(evt(gx, gy + 25)); world.draw(n);
        canvas.processMouseMove(evt(gx, gy + 50)); world.draw(n);
        canvas.processMouseUp(evt(gx, gy + 50, { buttons: 0 }));
        world.draw(n);
        const after = snap(world, n);
        const expected = before.promptH + 50;
        const pass = after.promptH === expected;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, expected ${expected} (media grew +1 line mid-gap)` };
    },

    "S11 second drag right after a width-wrap drag re-anchors cleanly": (world, model) => {
        const n = makeNode(world, model);
        const before = snap(world, n);
        drag(world, n, "SE", [[-90, 0], [-180, 0]]);   // narrow, wraps, released narrow
        drag(world, n, "SE", [[0, 35], [0, 70]]);       // fresh vertical drag
        const after = snap(world, n);
        const expected = before.promptH + 70;
        const pass = after.promptH === expected;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, expected ${expected}` };
    },

    "S12 one-move drag (contract quirk: onDrag precedes onDragStart) stays consistent": (world, model) => {
        // A single mousemove drag delivers its only setSize BEFORE resizing_node is set.
        // Contract as shipped (A/C): no model may absorb it, and the wrapper re-derives,
        // discarding the frontend's one-frame resize — node ends exactly where it started.
        // D has no wrapper: the frontend's own one-frame resize APPLIES (+80 to node and
        // prompt) — a semantic change, flagged by this scenario failing for D.
        const n = makeNode(world, model);
        const before = snap(world, n);
        drag(world, n, "SE", [[0, 80]]); // one move, then up
        const after = snap(world, n);
        const pass = after.promptH === before.promptH && after.h === before.h
            && world.canvas.resizing_node === null;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, h ${before.h}->${after.h} (shipped contract: the 80px is discarded; native: it applies)` };
    },

    "S13 content grows MID-drag, drag CONTINUES (probe answers during a height drag)": (world, model) => {
        const { canvas } = world;
        const n = makeNode(world, model);
        const before = snap(world, n);
        const [gx, gy] = cornerPoint(n, "SE");
        const down = evt(gx, gy); down.__node = n;
        canvas.processMouseDown(down);
        canvas.processMouseMove(evt(gx, gy + 30)); world.draw(n);
        canvas.processMouseMove(evt(gx, gy + 60)); world.draw(n);
        // async relayout lands mid-drag: +4 images wrap a new line (+137px media)
        world.applyContentChange(n, () => pushImages(n, "mid", 4));
        world.draw(n);
        canvas.processMouseMove(evt(gx, gy + 90)); world.draw(n);
        canvas.processMouseUp(evt(gx, gy + 90, { buttons: 0 }));
        world.draw(n);
        const after = snap(world, n);
        const expected = before.promptH + 90;
        const pass = after.promptH === expected;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, expected ${expected} (media +1 line mid-drag, then a further mousemove)` };
    },

    "S14 grabbing the handle without moving persists nothing": (world, model) => {
        const { canvas } = world;
        const n = makeNode(world, model);
        const before = snap(world, n);
        const persistsBefore = n._persistCalls || 0;
        const [gx, gy] = cornerPoint(n, "SE");
        const down = evt(gx, gy); down.__node = n;
        canvas.processMouseDown(down);
        canvas.processMouseMove(evt(gx, gy)); world.draw(n);   // no movement at all
        canvas.processMouseMove(evt(gx, gy)); world.draw(n);
        canvas.processMouseUp(evt(gx, gy, { buttons: 0 }));
        world.draw(n);
        const after = snap(world, n);
        const wrote = n._mmrpPromptH !== undefined;
        const persisted = (n._persistCalls || 0) > persistsBefore;
        const pass = after.promptH === before.promptH && after.h === before.h && !wrote && !persisted;
        return {
            pass,
            detail: `prompt ${before.promptH}->${after.promptH}, h ${before.h}->${after.h}, ` +
                `_mmrpPromptH ${wrote ? "WRITTEN" : "still unset"}, persist calls ${persisted ? "FIRED" : "none"}`,
        };
    },

    // ---- new scenarios stressing the native design -------------------------

    "S15 media grows one wrapped row while the node has slack — prompt must not move": (world, model) => {
        const n = makeNode(world, model);
        drag(world, n, "SE", [[0, 50], [0, 100]]);   // give the prompt slack (210)
        const before = snap(world, n);
        world.applyContentChange(n, () => pushImages(n, "grow", 4)); // 7->11: 5/row 2->3 lines, +137
        world.draw(n); world.draw(n);
        const after = snap(world, n);
        const pass = after.promptH === before.promptH && after.h === before.h + 137;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, h ${before.h}->${after.h} (want +137)` };
    },

    "S16 media shrinks by a row — prompt must not move": (world, model) => {
        const n = makeNode(world, model);
        const before = snap(world, n);
        world.applyContentChange(n, () => { n._mmrpRefs.images.length = 3; }); // 7->3: 2->1 lines, -137
        world.draw(n); world.draw(n);
        const after = snap(world, n);
        const pass = after.promptH === before.promptH && after.h === before.h - 137;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, h ${before.h}->${after.h} (want -137)` };
    },

    "S17 content lands mid-drag, drag ends with NO further move": (world, model) => {
        // The counterpart of S13: same mid-drag content change, but the user releases
        // without another mousemove. Isolates WHERE the native design loses the row:
        // the next absolute drag frame, not the change itself.
        const { canvas } = world;
        const n = makeNode(world, model);
        const before = snap(world, n);
        const [gx, gy] = cornerPoint(n, "SE");
        const down = evt(gx, gy); down.__node = n;
        canvas.processMouseDown(down);
        canvas.processMouseMove(evt(gx, gy + 30)); world.draw(n);
        canvas.processMouseMove(evt(gx, gy + 60)); world.draw(n);
        world.applyContentChange(n, () => pushImages(n, "mid", 4)); // +137 media
        world.draw(n);
        canvas.processMouseUp(evt(gx, gy + 60, { buttons: 0 }));    // release, no more moves
        world.draw(n); world.draw(n);
        const after = snap(world, n);
        const expected = before.promptH + 60;
        const pass = after.promptH === expected;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH}, expected ${expected}` };
    },

    "S18 node at its floor, then media grows — the node must grow, prompt must hold": (world, model) => {
        const { S } = world;
        const n = makeNode(world, model);
        drag(world, n, "SE", [[0, -400], [0, -2000]]); // released at the floor
        const atFloor = snap(world, n);
        world.applyContentChange(n, () => pushImages(n, "grow", 4)); // +137 media
        world.draw(n); world.draw(n);
        const grown = snap(world, n);
        const passA = grown.promptH === atFloor.promptH && grown.h >= atFloor.h + 137 - 1;
        // Auto-grow safety net: force the node 137 below where it settled (a content
        // path the compensation missed) — the next draws must recover it. Tolerance 13:
        // the native computeSize floor sits K=+4/+8/+6-vs-startY px above what
        // _arrangeWidgets actually stacks, so auto-grow's equilibrium is K px shy.
        n._size[1] = n._size[1] - 137;
        world.draw(n); world.draw(n);
        const restored = snap(world, n);
        const passB = restored.h >= grown.h - 13
            && restored.promptH >= S.CONTENT.minPromptH - 0.5
            && restored.promptH <= atFloor.promptH + 0.5;
        return {
            pass: passA && passB,
            detail: `at floor prompt ${atFloor.promptH} h ${atFloor.h}; after +137 media prompt ${grown.promptH} h ${grown.h}; ` +
                `after forced -137 recovered h ${restored.h} prompt ${restored.promptH}`,
        };
    },

    "S19 reload from serialized node.size with NO prompt_h — prompt must come back": (world, model) => {
        const n = makeNode(world, model);
        drag(world, n, "SE", [[0, 70], [0, 150]]); // prompt 110 -> 260
        const saved = { w: n.size[0], h: n.size[1], prompt: world.promptOf(n) };
        const world2 = makeWorld(model);
        const n2 = makeNode(world2, model, { width: saved.w }); // NO promptH handed over
        n2._size[0] = saved.w; n2._size[1] = saved.h;
        world2.reassert(n2);
        FE.afterLoadPass(n2);
        for (let i = 0; i < 6; i++) world2.draw(n2);
        const after = snap(world2, n2);
        const pass = after.promptH === saved.prompt && after.h === saved.h;
        return { pass, detail: `saved h ${saved.h} prompt ${saved.prompt}; reloaded h ${after.h} prompt ${after.promptH}` };
    },

    "S20 free space large enough that the slab exceeds any sane height": (world, model) => {
        const n = makeNode(world, model);
        const before = snap(world, n);
        drag(world, n, "SE", [[0, 800], [0, 2000]]);
        const after = snap(world, n);
        for (let i = 0; i < 4; i++) world.draw(n);
        const settled = snap(world, n);
        // Contract: the drag maps 1:1 into the prompt with no cap, and NOTHING fights
        // it afterwards (no shrink-back, no oscillation).
        const pass = after.promptH === before.promptH + 2000
            && settled.promptH === after.promptH && settled.h === after.h;
        return { pass, detail: `prompt ${before.promptH}->${after.promptH} (want ${before.promptH + 2000}), settled ${settled.promptH}, h ${settled.h}` };
    },

    "S21 workflow saved by the SHIPPED code, reloaded here without prompt_h (migration)": (world, model) => {
        // A real user's workflow: saved by model A (derived height, prompt dragged to
        // 210, prompt_h in the envelope). The envelope key is deleted by the new
        // design, so the reload sees ONLY node.size. D must reconstruct 210 from the
        // height A serialized; A/B/C without their envelope key fall back to the 110
        // default (that is WHY they need prompt_h — expected FAIL for them).
        const worldA = makeWorld("A");
        const nA = makeNode(worldA, "A");
        drag(worldA, nA, "SE", [[0, 50], [0, 100]]); // prompt 110 -> 210
        const saved = { w: nA.size[0], h: nA.size[1], prompt: worldA.promptOf(nA) };
        const world2 = makeWorld(model);
        const n2 = makeNode(world2, model, { width: saved.w });
        n2._size[0] = saved.w; n2._size[1] = saved.h;
        world2.reassert(n2);
        FE.afterLoadPass(n2);
        for (let i = 0; i < 6; i++) world2.draw(n2);
        const after = snap(world2, n2);
        const pass = Math.abs(after.promptH - saved.prompt) <= 1;
        return { pass, detail: `A saved ${saved.w}x${saved.h} prompt ${saved.prompt}; ${model} reloaded prompt ${after.promptH} h ${after.h}` };
    },
};

// ---------------------------------------------------------------------------
// Run and tabulate
// ---------------------------------------------------------------------------
const MODELS = ["A"];
const NAMES = { A: "shipped" };
// S19/S21 exist to evaluate a REPLACEMENT that stored the height in node.size instead of
// in the references_json envelope. They assert a prompt height survives a reload with no
// prompt_h present, which the shipped code is designed not to do - it reads that key. They
// are kept out rather than deleted so the comparison stays reproducible via REFPACK_PATH.
const NOT_FOR_SHIPPED = /^S(19|21) /;
const results = {};
for (const name of Object.keys(scenarios).filter((n) => !NOT_FOR_SHIPPED.test(n))) {
    results[name] = {};
    for (const m of MODELS) {
        let r;
        try { r = scenarios[name](makeWorld(m), m); }
        catch (e) { r = { pass: false, detail: "THREW: " + e.message }; }
        results[name][m] = r;
    }
}

const w1 = Math.max(...Object.keys(results).map((s) => s.length)) + 2;
console.log("".padEnd(w1) + MODELS.map((m) => NAMES[m].padEnd(14)).join(""));
for (const [name, row] of Object.entries(results)) {
    console.log(name.padEnd(w1) + MODELS.map((m) => (row[m].pass ? "PASS" : "FAIL").padEnd(14)).join(""));
}
console.log("\n--- details ---");
for (const [name, row] of Object.entries(results)) {
    console.log("\n" + name);
    for (const m of MODELS) console.log(`  ${NAMES[m].padEnd(12)} ${row[m].pass ? "PASS" : "FAIL"}  ${row[m].detail}`);
}
const total = Object.keys(results).length;
const passed = Object.values(results).filter((r) => r.A.pass).length;
console.log(`\n${passed}/${total} scenarios pass`);
if (passed !== total) {
    console.error("\nRESIZE REGRESSION: the scenarios above are the contract this node's "
        + "height drag depends on.\nIf a ComfyUI frontend upgrade caused this, the drag "
        + "contract in frontend.js has moved and\nboth it and web/refpack.js need "
        + "re-deriving against the new bundle - do not just adjust\nthe expectations.");
    process.exit(1);
}
