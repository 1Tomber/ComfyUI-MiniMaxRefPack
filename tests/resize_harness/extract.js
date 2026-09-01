"use strict";
// Extracts the REAL resize-relevant functions from web/refpack.js (branch integration)
// and evaluates them in a sandbox, so the harness runs the shipped code, not a copy.
// Nothing in the repo is modified; the file is only read.

const fs = require("fs");
const path = require("path");

// Default target: refpack.snapshot.js — a byte-identical copy of
// C:\Users\Tom\mmrp-wt\web\refpack.js at feat/responsive-node commit 78a3b79 (the
// pointer-model code that passes 16/16), taken because that worktree is being edited
// live. Point REFPACK_PATH at the worktree to run against the editing head instead.
const path0 = require("path");
// The file under test, found relative to this harness so the suite is checkout- and
// machine-independent. REFPACK_PATH overrides it for comparing two checkouts.
const REFPACK = process.env.REFPACK_PATH ||
    path0.join(__dirname, "..", "..", "web", "refpack.js");

const src = fs.readFileSync(REFPACK, "utf8");

function braceMatch(text, openIdx) {
    let depth = 0;
    for (let i = openIdx; i < text.length; i++) {
        const ch = text[i];
        if (ch === "{") depth++;
        else if (ch === "}") { depth--; if (depth === 0) return i; }
        else if (ch === '"' || ch === "'" || ch === "`") {
            const q = ch;
            i++;
            while (i < text.length && text[i] !== q) { if (text[i] === "\\") i++; i++; }
        } else if (ch === "/" && text[i + 1] === "/") { while (i < text.length && text[i] !== "\n") i++; }
        else if (ch === "/" && text[i + 1] === "*") { i = text.indexOf("*/", i) + 1; }
    }
    throw new Error("unbalanced braces from " + openIdx);
}

function grabFn(name) {
    const sig = new RegExp("(?:^|\\n)(?:export )?function " + name + "\\s*\\(");
    const m = sig.exec(src);
    if (!m) throw new Error("function not found: " + name);
    const start = m.index + (m[0].startsWith("\n") ? 1 : 0);
    const open = src.indexOf("{", m.index + m[0].length - 1);
    const close = braceMatch(src, open);
    return src.slice(start, close + 1).replace(/^export /, "");
}

function grabConst(name) {
    const sig = new RegExp("(?:^|\\n)const " + name + " = ");
    const m = sig.exec(src);
    if (!m) throw new Error("const not found: " + name);
    const start = m.index + (m[0].startsWith("\n") ? 1 : 0);
    const eq = src.indexOf("=", start);
    let end;
    const firstNonWs = src.slice(eq + 1).match(/\S/);
    if (firstNonWs[0] === "{" || firstNonWs[0] === "[") {
        const open = eq + 1 + src.slice(eq + 1).indexOf(firstNonWs[0]);
        end = braceMatch(src.replace(/\[/g, "{").replace(/\]/g, "}"), open);
    } else {
        end = src.indexOf(";", eq);
    }
    return src.slice(start, end + 1) + (src[end] === ";" ? "" : ";");
}

const FN_NAMES = [
    "tilesPerRow", "linesFor", "computeCanvasRows",
    "uploadsNaturalWidth", "minNodeWidth", "promptHeightOf", "slabViewW",
    "layoutOf", "contentHeight", "nodeSize", "domWidgetMargin",
    "domWidgetHeightPad", "reportedHeightFor", "syncDomWidgetSize",
    "syncPromptHeight", "applyCanvasHeight",
    "isUserResizing", "isSizePair", "absorbPointerIntoPrompt", "minNodeHeight",
    "installSizeGuards", "setSizeInternal",
    // contentHeight/minNodeHeight now add the subject bar's height; both helpers have to
    // be in the sandbox or those two throw. With no subjects the extra is 0, so the resize
    // contract is unchanged - which is exactly what the scenarios re-verify.
    "subjectBarExtra", "assignedSubjectNumbers",
    // subjectBarExtra also shows the bar for stray tags now, so it reaches the whole tag
    // scanner. Pulled in whole (not stubbed) so the harness keeps running the real height
    // math; with no direction text and no refs the scan is empty and the extra is still 0.
    // scanCounts derives the audio range from assignTags, so that comes along too.
    "hasStrayTags", "scanCounts", "directionText", "scanPromptTags", "widgetByName",
    "assignTags",
];
const CONST_NAMES = ["KINDS", "CL", "CONTENT"];

function makeSandbox(ctx) {
    const pieces = [];
    for (const c of CONST_NAMES) pieces.push(grabConst(c));
    for (const f of FN_NAMES) pieces.push(grabFn(f));
    const code = `
        "use strict";
        const { app, getComputedStyle, schedulePersistPromptH } = ctx;
        ${pieces.join("\n")}
        return { ${[...CONST_NAMES, ...FN_NAMES].join(", ")} };
    `;
    // eslint-disable-next-line no-new-func
    return new Function("ctx", code)(ctx);
}

module.exports = { makeSandbox, grabFn, REFPACK };
