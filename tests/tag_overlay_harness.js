"use strict";
// Runs the REAL syncTagOverlay from web/refpack.js against a minimal DOM shim, so the
// backdrop-building code - the one intricate new piece with no live ComfyUI to catch a
// typo - is exercised here rather than only on the pod. Reuses the resize harness's
// grabFn so the extraction itself is the tested one.
//
// It checks the thing that would silently break: that the spans line up with the tags the
// scanner found, carry the right class (live vs stray vs armed), and that the text between
// them is preserved verbatim, so the overlay reads as the exact same string as the
// textarea.

const { grabFn } = require("./resize_harness/extract.js");

// ---- a DOM just big enough for syncTagOverlay -------------------------------------
function makeEl(tag) {
    const el = {
        tagName: tag,
        _className: "",
        childNodes: [],
        style: {},
        scrollTop: 0,
        scrollLeft: 0,
        title: "",
        listenerCount: 0,
        get className() { return this._className; },
        set className(v) { this._className = v; },
        classList: {
            add(c) {
                const set = new Set(el._className.split(/\s+/).filter(Boolean));
                set.add(c);
                el._className = [...set].join(" ");
            },
            contains(c) { return el._className.split(/\s+/).includes(c); },
        },
        setAttribute() {},
        addEventListener() { el.listenerCount += 1; },
        appendChild(child) { el.childNodes.push(child); return child; },
        replaceChildren() { el.childNodes.length = 0; },
        set textContent(v) { el._text = v; },
        get textContent() { return el._text; },
    };
    return el;
}
const document = {
    createElement: makeEl,
    createTextNode(text) { return { nodeType: 3, textContent: text }; },
};

// visible text the overlay would render = concatenation of every node's text
function overlayText(overlay) {
    return overlay.childNodes.map((n) => n.textContent || "").join("");
}
function spans(overlay) {
    return overlay.childNodes.filter((n) => n.tagName === "span");
}

// ---- assemble the real functions in a sandbox with the shim -----------------------
const NAMES = ["assignTags", "assignedSubjectNumbers", "scanPromptTags", "scanCounts",
               "syncTagOverlay"];
const bodies = NAMES.map(grabFn).join("\n");
const factory = new Function("document", "scheduleDraw", "KINDS", `
    "use strict";
    ${bodies}
    return { syncTagOverlay };
`);
const { syncTagOverlay } = factory(document, () => {}, ["image", "video", "audio"]);

// ---- scenarios --------------------------------------------------------------------
let failures = 0;
function check(label, cond) {
    if (cond) { console.log("  PASS  " + label); }
    else { console.log("  FAIL  " + label); failures++; }
}

function nodeWith(text, refs) {
    const overlay = makeEl("div");
    return {
        _mmrpRefs: refs,
        _mmrpBody: { tagOverlay: overlay, directionInput: { value: text, scrollTop: 0, scrollLeft: 0 } },
    };
}
const R = (images = [], videos = [], audios = []) => ({
    images: images.map((f) => ({ file: f })),
    videos: videos.map((f) => (typeof f === "string" ? { file: f, use_soundtrack: true } : f)),
    audios: audios.map((f) => ({ file: f })),
});

// 1. text preserved verbatim, one live + one stray span, right classes
(() => {
    const text = "the <Picture 1> wears <Picture 4>";
    const n = nodeWith(text, R(["a.png"]));   // only 1 picture, so <Picture 4> is stray
    syncTagOverlay(n);
    const ov = n._mmrpBody.tagOverlay;
    check("verbatim text round-trips", overlayText(ov) === text);
    const s = spans(ov);
    check("two spans", s.length === 2);
    check("live tag classed mmrp-tag, not stray",
          s[0].classList.contains("mmrp-tag") && !s[0].classList.contains("mmrp-tag-stray"));
    check("out-of-range tag classed stray", s[1].classList.contains("mmrp-tag-stray"));
    check("span text is the tag itself", s[0].textContent === "<Picture 1>" && s[1].textContent === "<Picture 4>");
})();

// 2. the "#" marker is stray
(() => {
    const n = nodeWith("gone <Picture #> here", R([]));
    syncTagOverlay(n);
    const s = spans(n._mmrpBody.tagOverlay);
    check("hash marker is one stray span", s.length === 1 && s[0].classList.contains("mmrp-tag-stray"));
})();

// 3. a video's soundtrack <Audio N> is a live tag (maps to the video tile)
(() => {
    const n = nodeWith("score <Audio 1>", R([], ["clip.mp4"]));   // video soundtrack = <Audio 1>
    syncTagOverlay(n);
    const s = spans(n._mmrpBody.tagOverlay);
    check("soundtrack audio tag is live", s.length === 1 && !s[0].classList.contains("mmrp-tag-stray"));
})();

// 4. spans are decorative only - no listeners are bound (so clicks reach the textarea)
(() => {
    const n = nodeWith("a <Picture 1> b", R(["a.png"]));
    syncTagOverlay(n);
    const s = spans(n._mmrpBody.tagOverlay);
    check("tag span binds no event listeners", s.length === 1 && s[0].listenerCount === 0);
})();

// 5. empty prompt -> no spans, empty overlay
(() => {
    const n = nodeWith("", R(["a.png"]));
    syncTagOverlay(n);
    check("empty prompt builds nothing", n._mmrpBody.tagOverlay.childNodes.length === 0);
})();

console.log(failures ? `\n${failures} overlay check(s) FAILED` : "\nall overlay checks pass");
process.exit(failures ? 1 : 0);
