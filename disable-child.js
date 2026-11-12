// disable-child.js
// Guard against using child_process in any way.
// Loading the module itself is allowed because some benign game plugins
// (for example PluginUtils) require it at startup. Every spawn/exec API is
// replaced with a blocked stub, so nothing can actually run a process.
const Module = require("module");
const origRequire = Module.prototype.require;

function showPopup(msg) {
    try {
        if (typeof window !== "undefined") {
            alert(msg);
            console.error(msg);
        } else {
            console.error(msg);
        }
    } catch (e) {
        console.error("Popup failed:", e);
    }
}

const BLOCKED_METHODS = [
    "exec",
    "execFile",
    "spawn",
    "fork",
    "spawnSync",
    "execSync",
    "execFileSync",
];

function blocked(name) {
    return function () {
        showPopup("⚠️ Attempt to use child_process." + name + "() was blocked!");
        throw new Error("child_process is disabled");
    };
}

function guardChildProcess(cp) {
    for (const name of BLOCKED_METHODS) {
        if (typeof cp[name] === "function") {
            cp[name] = blocked(name);
        }
    }

    // spawn()/exec() all end up here, so guard it as well.
    const ChildProcess = cp.ChildProcess;
    if (ChildProcess && ChildProcess.prototype) {
        if (typeof ChildProcess.prototype.spawn === "function") {
            ChildProcess.prototype.spawn = blocked("spawn");
        }
    }

    return cp;
}

function loadChildProcess(context) {
    try {
        return origRequire.call(context, "node:child_process");
    } catch (_) {
        // Older NW.js / Node versions do not support the node: prefix.
        return origRequire.call(context, "child_process");
    }
}

Module.prototype.require = function (id) {
    if (id === "child_process" || id === "node:child_process") {
        return guardChildProcess(loadChildProcess(this));
    }
    return origRequire.apply(this, arguments);
};

// If anything already obtained a reference before this guard was installed,
// neuter the methods on the cached module too.
try {
    guardChildProcess(loadChildProcess(module));
} catch (_) {
    /* ignore if loading itself throws */
}
