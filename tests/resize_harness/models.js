"use strict";
// How the shipped code is installed onto the harness node. There is one "model" here
// because the research models this harness was built to compare (a clamp-disarming
// candidate, and the native computeLayoutSize/distributeSpace layout) were evaluated and
// rejected - see tests/test_resize.py for what that comparison found. What remains is a
// single indirection so the shipped path is installed in exactly one place.

// ---- Model A: current shipped code -----------------------------------------
function installA(S, node) {
    S.installSizeGuards(node);
}

module.exports = { installA };
