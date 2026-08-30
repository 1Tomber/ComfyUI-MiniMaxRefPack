"use strict";
// Faithful port of ComfyUI frontend 1.51.9's node-resize AND widget-layout machinery,
// deminified from comfyui_frontend_package/static/assets/settingStore-KkBYyEnh.js.
// Every load-bearing line is a direct transliteration; byte offsets into the bundle are
// cited, and where the bundle ships sourcesContent the original TS/Vue file is named.
//
// NEW since the pointer-model round: the NATIVE widget-layout path —
//   distributeSpace            (@148155, src/lib/litegraph/src/utils/spaceDistribution.ts)
//   getLayoutWidgets           (@209013, LGraphNode.ts:4042 — filters !w.hidden ONLY)
//   _arrangeWidgets            (@211494, LGraphNode.ts:4246 — incl. the auto-grow branch)
//   arrange()                  (@213050, LGraphNode.ts:4378 — startY from measured slots)
//   native LGraphNode.computeSize (@188497, LGraphNode.ts:1849 — the drag clamp's floor)
//   DOMWidgetImpl.computeLayoutSize (@451134, src/scripts/domWidget.ts)
//   DomWidgets.vue::updateWidgets element-size derivation (GraphView chunk,
//     src/components/graph/DomWidgets.vue)
// drawFrame() now runs the real arrange() instead of the old simplified stack.

// ---------------------------------------------------------------------------
// LiteGraph constants used by the ported code (LiteGraphGlobal.ts; bundle values
// verified: NODE_WIDGET_HEIGHT=20, NODE_SLOT_HEIGHT=20, NODE_WIDTH=140,
// NODE_TITLE_HEIGHT=30, NODE_TEXT_SIZE=14; vueNodesMode defaults to false and is
// only set true by useVueFeatureFlagsIndividual mirroring the experimental
// `Comfy.VueNodes.Enabled` setting, defaultValue:!1).
// ---------------------------------------------------------------------------
const NODE_TITLE_HEIGHT = 30;
const NODE_WIDGET_HEIGHT = 20;
const NODE_SLOT_HEIGHT = 20;
const NODE_WIDTH = 140;
const NODE_TEXT_SIZE = 14;

// ---------------------------------------------------------------------------
// Rectangle (bundle @103105: `Dc=class Rectangle extends Float64Array`)
//
// NOTE the overridden subarray(): the real class RETURNS A PLAIN Float64Array view
// over its own buffer, NOT a species-constructed Rectangle. This is what makes
// `rect.size` a length-2 Float64Array (Array.isArray === false) that aliases
// indices 2..3. Porting the override verbatim is what avoids the species trap.
//   bundle: subarray(e=0,t){let n=e<<3,r=t===void 0?t:t-e;return new Float64Array(this.buffer,n,r)}
// ---------------------------------------------------------------------------
class Rectangle extends Float64Array {
    constructor(x = 0, y = 0, w = 0, h = 0) {
        super(4);
        this[0] = x; this[1] = y; this[2] = w; this[3] = h;
        this._pos = undefined;
        this._size = undefined;
    }
    subarray(begin = 0, end) {
        const byteOffset = begin << 3;
        const length = end === undefined ? end : end - begin;
        return new Float64Array(this.buffer, byteOffset, length);
    }
    get pos() { return this._pos ??= this.subarray(0, 2); }
    set pos(v) { this[0] = v[0]; this[1] = v[1]; }
    get size() { return this._size ??= this.subarray(2, 4); }
    set size(v) { this[2] = v[0]; this[3] = v[1]; }
    get x() { return this[0]; } set x(v) { this[0] = v; }
    get y() { return this[1]; } set y(v) { this[1] = v; }
    get width() { return this[2]; } set width(v) { this[2] = v; }
    get height() { return this[3]; } set height(v) { this[3] = v; }
    get right() { return this[0] + this[2]; }
    get bottom() { return this[1] + this[3]; }
    containsXy(x, y) {
        const [rx, ry, rw, rh] = this;
        return x >= rx && x < rx + rw && y >= ry && y < ry + rh;
    }
    findContainingCorner(x, y, n) {
        if (isInRectangle(x, y, this.x, this.y, n, n)) return "NW";
        if (isInRectangle(x, y, this.right - n, this.y, n, n)) return "NE";
        if (isInRectangle(x, y, this.x, this.bottom - n, n, n)) return "SW";
        if (isInRectangle(x, y, this.right - n, this.bottom - n, n, n)) return "SE";
    }
}

function isInRectangle(x, y, rx, ry, rw, rh) {
    return x >= rx && x < rx + rw && y >= ry && y < ry + rh;
}
function dist2(ax, ay, bx, by) { const dx = ax - bx, dy = ay - by; return dx * dx + dy * dy; }

// ---------------------------------------------------------------------------
// CanvasPointer (bundle @93719). Verbatim lifecycle:
//   down(e): reset, store eDown, capture pointer
//   move(e): if (!e.buttons) reset;                       <- CANCEL path (buttons lost)
//            else { eMove=e; onDrag?.(e);                 <- onDrag fires EVERY move,
//                   if (!dragStarted && (dt>32ms || moved>6px)) _setDragStarted }
//               NOTE: onDrag runs BEFORE dragStarted is set on the first move, so the
//               drag's first setSize arrives while resizing_node is still null.
//   up(e): _completeClick -> onDragEnd if dragStarted; then reset()
//   reset(): assigning `finally` INVOKES the stored one (bundle: set finally(e){try{this._finally?.()}finally{this._finally=e}})
// ---------------------------------------------------------------------------
class CanvasPointer {
    static bufferTime = 32;
    static _maxClickDrift = 6;
    static _maxClickDrift2 = 36;
    constructor(element) {
        this.element = element;
        this.pointerId = undefined;
        this.dragStarted = false;
        this.isDown = false;
        this.resizeDirection = undefined;
        this.clearEventsOnReset = true;
        this.eDown = undefined;
        this.eMove = undefined;
        this.eUp = undefined;
        this._finally = undefined;
    }
    get finally() { return this._finally; }
    set finally(fn) {
        try { this._finally?.(); } finally { this._finally = fn; }
    }
    down(e) {
        this.reset();
        this.eDown = e;
        this.pointerId = e.pointerId;
        this.element.setPointerCapture(e.pointerId);
    }
    move(e) {
        const { eDown } = this;
        if (!eDown) return;
        if (!e.buttons) { this.reset(); return; }
        if (!(e.buttons & eDown.buttons)) { this._completeClick(e); this.reset(); return; }
        this.eMove = e;
        this.onDrag?.(e);
        if (!this.dragStarted &&
            (e.timeStamp - eDown.timeStamp > CanvasPointer.bufferTime || !this._hasSamePosition(e, eDown))) {
            this._setDragStarted(e);
        }
    }
    up(e) {
        if (e.button !== this.eDown?.button) return false;
        this._completeClick(e);
        const { dragStarted } = this;
        this.reset();
        return !dragStarted;
    }
    _completeClick(e) {
        const { eDown } = this;
        if (!eDown) return;
        this.eUp = e;
        if (this.dragStarted) this.onDragEnd?.(e);
        else if (this._hasSamePosition(e, eDown)) this.onClick?.(e);
        else { this._setDragStarted(); this.onDragEnd?.(e); }
    }
    _hasSamePosition(a, b, n = CanvasPointer._maxClickDrift2) {
        return dist2(a.clientX, a.clientY, b.clientX, b.clientY) <= n;
    }
    _setDragStarted(e) {
        this.dragStarted = true;
        this.onDragStart?.(this, e);
        delete this.onDragStart;
        // MEASURED IN A REAL BROWSER: processMouseMove's group-hover branch runs
        // `pointer.resizeDirection &&= void 0` with no eDown guard, so the field is gone
        // by the first frame of the drag - while `resizing_node` is not set until here.
        // The two are never valid together.
        this.resizeDirection = undefined;
    }
    reset() {
        this.finally = undefined; // invokes the previous finally
        delete this.onClick;
        delete this.onDoubleClick;
        delete this.onDragStart;
        delete this.onDrag;
        delete this.onDragEnd;
        this.isDown = false;
        this.dragStarted = false;
        this.resizeDirection = undefined;
        if (this.clearEventsOnReset) { this.eDown = undefined; this.eMove = undefined; this.eUp = undefined; }
        const { element, pointerId } = this;
        this.pointerId = undefined;
        if (typeof pointerId === "number" && element.hasPointerCapture(pointerId)) {
            element.releasePointerCapture(pointerId);
        }
    }
}

// ---------------------------------------------------------------------------
// distributeSpace — VERBATIM port. Bundle @148155; original
// src/lib/litegraph/src/utils/spaceDistribution.ts (sourcesContent).
//   maxSize ?? Infinity: a layout widget with maxHeight undefined absorbs ALL
//   remaining free space (single-widget case: allocation = max(minSize, totalSpace)).
//   totalSpace < totalMinSize  ->  every widget gets exactly its minSize.
// ---------------------------------------------------------------------------
function distributeSpace(totalSpace, requests) {
    if (requests.length === 0) return [];
    const totalMinSize = requests.reduce((sum, req) => sum + req.minSize, 0);
    if (totalSpace < totalMinSize) {
        return requests.map((req) => req.minSize);
    }
    let allocations = requests.map((req) => ({
        computedSize: req.minSize,
        maxSize: req.maxSize ?? Infinity,
        remaining: (req.maxSize ?? Infinity) - req.minSize,
    }));
    let remainingSpace = totalSpace - totalMinSize;
    while (remainingSpace > 0 && allocations.some((a) => a.remaining > 0)) {
        const growableItems = allocations.filter((a) => a.remaining > 0).length;
        if (growableItems === 0) break;
        const sharePerItem = remainingSpace / growableItems;
        let spaceUsedThisRound = 0;
        allocations = allocations.map((alloc) => {
            if (alloc.remaining <= 0) return alloc;
            const growth = Math.min(sharePerItem, alloc.remaining);
            spaceUsedThisRound += growth;
            return {
                ...alloc,
                computedSize: alloc.computedSize + growth,
                remaining: alloc.remaining - growth,
            };
        });
        remainingSpace -= spaceUsedThisRound;
        if (spaceUsedThisRound === 0) break;
    }
    return allocations.map(({ computedSize }) => computedSize);
}

// ---------------------------------------------------------------------------
// DOMWidgetImpl.computeLayoutSize — VERBATIM port. Bundle @451134; original
// src/scripts/domWidget.ts. This is the layout entry point ComfyUI's own multiline
// textarea rides (useStringWidget.ts::addMultilineWidget assigns NO getters at all
// and relies on the CSS-var/50px fallbacks; it sets only options.minNodeSize=[400,200],
// which litegraphService feeds into node._initialMinSize — INITIAL size only, it is
// NOT part of the resize clamp).
//   - getMinHeight() wins over the --comfy-widget-min-height CSS var
//   - no getMaxHeight and no CSS var  ->  maxHeight undefined  ->  Infinity in
//     distributeSpace: the widget absorbs all free body height
//   - type === 'hidden'  ->  {0, 0, 0}
//   - minWidth is HARDCODED 0 (why the node needs its own width floor elsewhere)
// The harness passes a stub getComputedStyle whose getPropertyValue returns "" —
// parseInt("") is NaN, exactly what an element with no such CSS vars produces live.
// ---------------------------------------------------------------------------
function domComputeLayoutSize(widget, node, getComputedStyleFn) {
    if (widget.type === "hidden") {
        return { minHeight: 0, maxHeight: 0, minWidth: 0 };
    }
    const styles = getComputedStyleFn(widget.element);
    let minHeight =
        widget.options.getMinHeight?.() ??
        parseInt(styles.getPropertyValue("--comfy-widget-min-height"));
    let maxHeight =
        widget.options.getMaxHeight?.() ??
        parseInt(styles.getPropertyValue("--comfy-widget-max-height"));
    let prefHeight =
        widget.options.getHeight?.() ??
        styles.getPropertyValue("--comfy-widget-height");
    if (typeof prefHeight === "string" && prefHeight.endsWith?.("%")) {
        prefHeight =
            node.size[1] *
            (parseFloat(prefHeight.substring(0, prefHeight.length - 1)) / 100);
    } else {
        prefHeight = typeof prefHeight === "number" ? prefHeight : parseInt(prefHeight);
        if (isNaN(minHeight)) minHeight = prefHeight;
    }
    return {
        minHeight: isNaN(minHeight) ? 50 : minHeight,
        maxHeight: isNaN(maxHeight) ? undefined : maxHeight,
        minWidth: 0,
    };
}

// ---------------------------------------------------------------------------
// LGraphNode essentials (bundle @172518 / @174370) plus the native layout path.
//   static resizeHandleSize=15
//   set size(e){!e||e.length<2||(this._size[0]=e[0],this._size[1]=e[1],...)}
//   setSize(e){this.size=e,this.onResize?.(this.size)}                       (@186411)
//   measure(): boundingRect = [pos.x, pos.y - titleH, w, h + titleH]         (@191562)
//   get bodyHeight(){return this.collapsed?0:this.size[1]}                   (@205898)
//   findResizeDirection(e,t){...findContainingCorner(e,t,resizeHandleSize)}  (@189485)
// ---------------------------------------------------------------------------
class LGraphNode {
    static resizeHandleSize = 15;
    constructor(title) {
        this.title = title || "node";
        this._posSize = new Rectangle();
        this._pos = this._posSize.pos;
        this._size = this._posSize.size;
        this._boundingRect = new Rectangle();
        this.flags = {};
        this.resizable = true;
        this.widgets = [];
        this.inputs = [];
        this.outputs = [];
        this.onResize = undefined;
        this.widgets_start_y = undefined;
        this.widgets_up = undefined;
        this.freeWidgetSpace = undefined;
    }
    get pos() { return this._pos; }
    set pos(v) { if (v && v.length >= 2) { this._pos[0] = v[0]; this._pos[1] = v[1]; } }
    get size() { return this._size; }
    set size(v) { if (v && v.length >= 2) { this._size[0] = v[0]; this._size[1] = v[1]; } }
    get collapsed() { return !!this.flags.collapsed; }
    get bodyHeight() { return this.collapsed ? 0 : this.size[1]; }
    get boundingRect() { return this._boundingRect; }
    setSize(e) { this.size = e; this.onResize?.(this.size); }

    // isWidgetVisible — bundle @208931 / LGraphNode.ts:4030:
    //   `return!(this.collapsed||e.hidden||e.advanced&&!this.showAdvanced)`
    // Used by the NATIVE computeSize: a hidden widget is skipped BEFORE its +4 pad,
    // so refpack's hideWidget()-locked widgets (hidden getter pinned to true,
    // refpack :824-869) cost exactly 0 there.
    isWidgetVisible(w) {
        return !(this.collapsed || w.hidden || (w.advanced && !this.showAdvanced));
    }

    // getLayoutWidgets — bundle @209013 / LGraphNode.ts:4042, VERBATIM:
    //   `return this.widgets?.filter(e=>!e.hidden)??[]`
    // Filters on w.hidden ONLY (not advanced, not collapsed). DOM widgets are in
    // this.widgets like any other, so they ARE included. refpack's hidden widgets
    // are excluded here outright — their computeSize=()=>[0,0] never even runs in
    // _arrangeWidgets on 1.51.9.
    getLayoutWidgets() {
        return this.widgets?.filter((w) => !w.hidden) ?? [];
    }

    // NATIVE computeSize — bundle @188497 / LGraphNode.ts:1849 ("computes the minimum
    // size of a node according to its inputs and output slots"). Height algebra is
    // verbatim; text widths use the bundle's own fallback formula
    // (`font_size*(text?.length??0)*.6`, the no-_measureText path).
    // The load-bearing lines for the resize clamp:
    //   rows = max(#inputs that are NOT widget slots, #outputs, 1)
    //   size[1] = (slot_start_y||0) + rows*NODE_SLOT_HEIGHT
    //   per VISIBLE widget:  computeSize -> h += computeSize(size[0])[1]
    //                        computeLayoutSize -> h += minHeight       (and the widget's
    //                            minWidth+104 may raise size[0] — DOMWidgetImpl returns
    //                            minWidth 0, so for our widget this branch never wins)
    //                        neither -> h += NODE_WIDGET_HEIGHT
    //     each widget then +4; after the loop +8; at the end size[1] += 6
    //   width floor: max(slotsWidth, widgetWidth, title_width, NODE_WIDTH*1.5)
    computeSize(out) {
        const ctorSize = this.constructor.size;
        if (ctorSize) return [ctorSize[0], ctorSize[1]];
        const { inputs, outputs, widgets } = this;
        let rows = Math.max(
            inputs ? inputs.filter((i) => !i.widget).length : 1,
            outputs ? outputs.length : 1);
        const size = out ?? [0, 0];
        rows = Math.max(rows, 1);
        const font_size = NODE_TEXT_SIZE;
        const padLeft = NODE_TITLE_HEIGHT;
        const padRight = padLeft * 0.33;
        const measure = (text) => font_size * ((text && text.length) || 0) * 0.6;
        const title_width = padLeft + measure(this.title) + padRight;
        let input_width = 0;
        let widgetWidth = 0;
        let output_width = 0;
        if (outputs) {
            for (const output of outputs) {
                const text = output.label || output.localized_name || output.name || "";
                const tw = measure(text);
                if (output_width < tw) output_width = tw;
            }
        }
        const minWidth = NODE_WIDTH * (widgets?.length ? 1.5 : 1);
        const centrePadding = input_width && output_width ? 5 : 0;
        const slotsWidth = input_width + output_width + 2 * NODE_SLOT_HEIGHT + centrePadding;
        const widgetMargin = 15 + 6 + 10;            // BaseWidget margin+arrowMargin+arrowWidth
        const widgetPadding = 42 + 2 * widgetMargin; // BaseWidget.minValueWidth + 2*widgetMargin
        if (widgetWidth) widgetWidth += widgetPadding;
        size[0] = Math.max(slotsWidth, widgetWidth, title_width, minWidth);
        size[1] = (this.constructor.slot_start_y || 0) + rows * NODE_SLOT_HEIGHT;
        let widgets_height = 0;
        if (widgets?.length) {
            for (const widget of widgets) {
                if (!this.isWidgetVisible(widget)) continue;
                let widget_height = 0;
                if (widget.computeSize) {
                    widget_height += widget.computeSize(size[0])[1];
                } else if (widget.computeLayoutSize) {
                    const { minHeight, minWidth: wMinW } = widget.computeLayoutSize(this);
                    const ww = wMinW + widgetPadding;
                    if (ww > size[0]) size[0] = ww;
                    widget_height += minHeight;
                } else {
                    widget_height += NODE_WIDGET_HEIGHT;
                }
                widgets_height += widget_height + 4;
            }
            widgets_height += 8;
        }
        if (this.widgets_up) size[1] = Math.max(size[1], widgets_height);
        else if (this.widgets_start_y != null) size[1] = Math.max(size[1], widgets_height + this.widgets_start_y);
        else size[1] += widgets_height;
        if (this.constructor.min_height && size[1] < this.constructor.min_height) {
            size[1] = this.constructor.min_height;
        }
        size[1] += 6;
        return size;
    }

    // _measureSlots — MODEL, not verbatim (the real one unions per-slot bounding
    // rects). The per-slot numbers it unions ARE verbatim:
    //   getOutputPos default-vertical branch (bundle @204xxx / LGraphNode.ts:3492):
    //     out[1] = nodeY + (slotIndex + 0.7) * NODE_SLOT_HEIGHT + (slot_start_y || 0)
    //   _measureSlot (LGraphNode.ts:4126): rect.y = pos.y - NODE_SLOT_HEIGHT*0.5, h = 20
    //   _measureSlots (LGraphNode.ts:4143) SKIPS widget-backed input slots when the
    //     node has widgets — the refpack node's inputs are ALL widget-backed, so only
    //     the 20 outputs contribute. Union bottom (relative to node y):
    //     (N-1+0.7)*20 + 10 = 20N + 4.
    _measureSlots() {
        const n = this.outputs ? this.outputs.length : 0;
        if (!n) return null;
        const startY = this.constructor.slot_start_y || 0;
        const top = this.pos[1] + startY + 0.7 * NODE_SLOT_HEIGHT - NODE_SLOT_HEIGHT * 0.5;
        const bottom = this.pos[1] + startY + (n - 1 + 0.7) * NODE_SLOT_HEIGHT + NODE_SLOT_HEIGHT * 0.5;
        return [this.pos[0], top, this.size[0], bottom - top];
    }

    // _arrangeWidgets — VERBATIM port. Bundle @211494 / LGraphNode.ts:4246.
    //   startY = widgets_start_y ?? ((widgets_up ? 0 : widgetStartY) + 2)
    //   computeSize widgets:      computedHeight = computeSize()[1] + 4   <- +4 pad
    //   computeLayoutSize widgets: {minHeight, maxHeight} -> distributeSpace over
    //       free = bodyHeight - startY - fixed; computedHeight = allocation, NO +4 —
    //       computedHeight for a layout widget is the RAW allocation, margins included
    //       (DomWidgets.vue subtracts margin*2 from it to size the element)
    //   neither:                  computedHeight = NODE_WIDGET_HEIGHT + 4
    //   stack y; then AUTO-GROW: `!Z.vueNodesMode&&l>t&&(this.setSize([this.size[0],l]),
    //       this.graph.setDirtyCanvas(!1,!0))` — grows the node DOWNWARD (pos untouched)
    //       whenever the stacked widgets overflow the body; never shrinks it. In
    //       vueNodesMode (Nodes 2.0, default OFF) the branch is dead.
    _arrangeWidgets(widgetStartY, { vueNodesMode = false } = {}) {
        if (!this.widgets || !this.widgets.length) return;
        const bodyHeight = this.bodyHeight;
        const startY = this.widgets_start_y ?? (this.widgets_up ? 0 : widgetStartY) + 2;
        let freeSpace = bodyHeight - startY;
        let fixedWidgetHeight = 0;
        const growableWidgets = [];
        const visibleWidgets = this.getLayoutWidgets();
        for (const w of visibleWidgets) {
            if (w.computeSize) {
                const height = w.computeSize()[1] + 4;
                w.computedHeight = height;
                fixedWidgetHeight += height;
            } else if (w.computeLayoutSize) {
                const { minHeight, maxHeight } = w.computeLayoutSize(this);
                growableWidgets.push({ minHeight, prefHeight: maxHeight, w });
            } else {
                const height = NODE_WIDGET_HEIGHT + 4;
                w.computedHeight = height;
                fixedWidgetHeight += height;
            }
        }
        freeSpace -= fixedWidgetHeight;
        this.freeWidgetSpace = freeSpace;
        const spaceRequests = growableWidgets.map((d) => ({
            minSize: d.minHeight,
            maxSize: d.prefHeight,
        }));
        const allocations = distributeSpace(Math.max(0, freeSpace), spaceRequests);
        for (const [i, d] of growableWidgets.entries()) {
            d.w.computedHeight = allocations[i];
        }
        let y = startY;
        for (const w of visibleWidgets) {
            w.y = y;
            y += w.computedHeight ?? 0;
        }
        if (!vueNodesMode && y > bodyHeight) {
            this.setSize([this.size[0], y]);
            // this.graph.setDirtyCanvas(false, true) — no-op in the harness
        }
    }

    // arrange — bundle @213050 / LGraphNode.ts:4378:
    //   widgetStartY = slotsBounds ? slotsBounds[1]+slotsBounds[3]-this.pos[1] : 0
    // (In vueNodesMode drawNode STILL calls arrange() — LGraphCanvas.ts: "Prepare
    // concrete slots and compute layout measures without rendering visuals" — so
    // computedHeight/y stay maintained for DomWidgets.vue; only the auto-grow inside
    // _arrangeWidgets is gated off.)
    arrange(opts) {
        const slotsBounds = this._measureSlots();
        const widgetStartY = slotsBounds
            ? slotsBounds[1] + slotsBounds[3] - this.pos[1]
            : 0;
        this._arrangeWidgets(widgetStartY, opts);
    }

    measure(rect) {
        const titleH = NODE_TITLE_HEIGHT;
        rect[0] = this.pos[0];
        rect[1] = this.pos[1] + -titleH;
        rect[2] = this.size[0];
        rect[3] = this.size[1] + titleH;
    }
    updateArea() { this.measure(this._boundingRect); }
    findResizeDirection(x, y) {
        if (this.resizable === false) return;
        const { boundingRect } = this;
        if (boundingRect.containsXy(x, y)) {
            return boundingRect.findContainingCorner(x, y, LGraphNode.resizeHandleSize);
        }
    }
}

// ---------------------------------------------------------------------------
// LGraphCanvas essentials.
// processMouseDown resize branch, deminified verbatim from bundle @301858:
//
//   let o=e.canvasX,s=e.canvasY;                                (_processPrimaryButton)
//   ...
//   if(!n.flags.collapsed){let e=n.findResizeDirection(o,s);if(e){
//     r.resizeDirection=e;
//     let t=new Dc(n.pos[0],n.pos[1],n.size[0],n.size[1]);      <- snapshot AT POINTERDOWN,
//     r.onDragStart=()=>{i.beforeChange(),this.resizing_node=n},   never re-read mid-drag
//     r.onDrag=r=>{if(this.read_only)return;
//       let i=r.canvasX-o,a=r.canvasY-s,                        <- ABSOLUTE delta from down
//       c=new Dc(t.x,t.y,t.width,t.height);                     <- fresh rect per frame
//       switch(e){
//         case`NE`:c.y=t.y+a,c.width=t.width+i,c.height=t.height-a;break;
//         case`SE`:c.width=t.width+i,c.height=t.height+a;break;
//         case`SW`:c.x=t.x+i,c.width=t.width-i,c.height=t.height+a;break;
//         case`NW`:c.x=t.x+i,c.y=t.y+a,c.width=t.width-i,c.height=t.height-a}
//       if(this._snapToGrid){...}
//       let l=n.computeSize();
//       this._snapToGrid&&snapPoint(l,this._snapToGrid,`ceil`),
//       c.width<l[0]&&(e.includes(`W`)&&(c.x=t.x+t.width-l[0]),c.width=l[0]),
//       c.height<l[1]&&(e.includes(`N`)&&(c.y=t.y+t.height-l[1]),c.height=l[1]),
//       n.pos=c.pos,n.setSize(c.size),this._dirty()},
//     r.onDragEnd=()=>{this._dirty(),i.afterChange(n)},
//     r.finally=()=>{this.resizing_node=null,r.resizeDirection=void 0},
//     this._setCursor(ul[e]);return}}
//
// processMouseMove (bundle @294xxx): graph_mouse is updated BEFORE pointer.move(e):
//   let{canvasX:c,canvasY:l}=e;this.graph_mouse[0]=c,this.graph_mouse[1]=l,e.isPrimary&&i.move(e)
// processMouseCancel(){this.pointer.reset()}
// ---------------------------------------------------------------------------
class LGraphCanvas {
    constructor() {
        this.pointer = new CanvasPointer({
            setPointerCapture() {}, hasPointerCapture() { return false; }, releasePointerCapture() {},
        });
        this.graph_mouse = [0, 0];
        this.resizing_node = null;
        this.read_only = false;
        this._snapToGrid = undefined;
        this.graph = { beforeChange() {}, afterChange() {} };
        this.dirtyCount = 0;
    }
    _dirty() { this.dirtyCount++; }
    processMouseDown(e) {
        const n = this.pointer;
        n.down(e);
        n.isDown = true;
        const node = e.__node; // harness supplies the node under the pointer
        if (!node) return;
        const o = e.canvasX, s = e.canvasY;
        const r = n, i = this.graph;
        if (!node.flags.collapsed) {
            const dir = node.findResizeDirection(o, s);
            if (dir) {
                r.resizeDirection = dir;
                const t = new Rectangle(node.pos[0], node.pos[1], node.size[0], node.size[1]);
                r.onDragStart = () => { i.beforeChange(); this.resizing_node = node; };
                r.onDrag = (ev) => {
                    if (this.read_only) return;
                    const di = ev.canvasX - o, a = ev.canvasY - s;
                    const c = new Rectangle(t.x, t.y, t.width, t.height);
                    switch (dir) {
                        case "NE": c.y = t.y + a; c.width = t.width + di; c.height = t.height - a; break;
                        case "SE": c.width = t.width + di; c.height = t.height + a; break;
                        case "SW": c.x = t.x + di; c.width = t.width - di; c.height = t.height + a; break;
                        case "NW": c.x = t.x + di; c.y = t.y + a; c.width = t.width - di; c.height = t.height - a; break;
                    }
                    // (grid snap elided: this._snapToGrid is undefined in these scenarios)
                    const l = node.computeSize();
                    if (c.width < l[0]) { if (dir.includes("W")) c.x = t.x + t.width - l[0]; c.width = l[0]; }
                    if (c.height < l[1]) { if (dir.includes("N")) c.y = t.y + t.height - l[1]; c.height = l[1]; }
                    node.pos = c.pos;
                    node.setSize(c.size);
                    this._dirty();
                };
                r.onDragEnd = () => { this._dirty(); i.afterChange(node); };
                r.finally = () => { this.resizing_node = null; r.resizeDirection = undefined; };
                return;
            }
        }
        // non-resize press: node drag — irrelevant to these scenarios
        r.onDragStart = () => {};
    }
    processMouseMove(e) {
        this.graph_mouse[0] = e.canvasX;
        this.graph_mouse[1] = e.canvasY;
        if (e.isPrimary) this.pointer.move(e);
    }
    processMouseUp(e) {
        if (e.isPrimary === false) return;
        this.pointer.up(e);
    }
    processMouseCancel() {
        this.pointer.reset();
    }
}

// ---------------------------------------------------------------------------
// One frame of the real draw pipeline, for the parts that touch layout.
// drawNode (LGraphCanvas.ts): `node._setConcreteSlots(); if (!node.collapsed)
// { node.arrange(); ... }` then drawWidgets, which mirrors y into last_y for every
// visible widget (LGraphNode.ts:4082 `widget.last_y = y`).
// After the canvas frame, DomWidgets.vue::updateWidgets (chained on
// canvas.onDrawForeground) derives every DOM element's rect FROM THE WIDGET:
//
//     const margin = widget.margin
//     widgetState.pos  = [posNode.pos[0] + margin, posNode.pos[1] + margin + widget.y]
//     widgetState.size = [(widget.width ?? posNode.width) - margin * 2,
//                        (widget.computedHeight ?? 50)   - margin * 2]
//
// With the dom widget's computeSize gone NOTHING assigns widget.width (litegraph
// only reads it: drawWidgets' `widget.width || nodeWidth`), so `?? posNode.width`
// covers the element width — as long as no stale widget.width was ever written.
// widget.computedHeight is the distributeSpace allocation; the element is inset by
// margin on all four sides of the widget's slot.
// ---------------------------------------------------------------------------
function updateWidgetsVue(node) {
    const w = node._mmrpDomWidget;
    if (!w) return;
    const margin = typeof w.margin === "number" ? w.margin : 10;
    node._domElemPos = [node.pos[0] + margin, node.pos[1] + margin + (w.y ?? 0)];
    node._domElemSize = [
        (w.width ?? node.size[0]) - margin * 2,
        (w.computedHeight ?? 50) - margin * 2,
    ];
}

function drawFrame(node, { vueNodesMode = false } = {}) {
    if (!node.collapsed) node.arrange({ vueNodesMode });
    if (node.widgets) {
        for (const w of node.widgets) {
            if (node.isWidgetVisible(w)) w.last_y = w.y; // drawWidgets, LGraphNode.ts:4082
        }
    }
    updateWidgetsVue(node);
    node.updateArea();
}

// Post-workflow-load pass (bundle @1619152):
//   let t=e.computeSize();t[0]=Math.max(e.size[0],t[0]),t[1]=Math.max(e.size[1],t[1]),
//   snapPoint(t,n,`ceil`),e.setSize(t)
function afterLoadPass(node) {
    const t = node.computeSize();
    t[0] = Math.max(node.size[0], t[0]);
    t[1] = Math.max(node.size[1], t[1]);
    node.setSize(t);
}

module.exports = {
    Rectangle, CanvasPointer, LGraphNode, LGraphCanvas,
    drawFrame, afterLoadPass, updateWidgetsVue,
    distributeSpace, domComputeLayoutSize,
    NODE_TITLE_HEIGHT, NODE_WIDGET_HEIGHT, NODE_SLOT_HEIGHT,
};
