/*:
 * @plugindesc RPGM Launcher runtime translation for standard MV/MZ text.
 * @author RPGM Launcher
 *
 * This plugin is installed and configured by RPGM Launcher. It never modifies
 * data/*.json files, event command lists, or raw actor names.
 *
 * Note: On old NW.js builds without String.prototype.normalize, CRLF is still
 * normalized but NFD dictionary/source strings may not match until a newer
 * runtime is used.
 */

(function() {
    if (typeof window === "undefined") {
        return;
    }

    var LOG_PREFIX = "[RPGM Launcher][Translation]";
    var PLUGIN_DIR = "js/plugins/";
    var CONFIG_FILE = "RPGM_Launcher_Translate.config.json";
    var DICT_FILE = "RPGM_Launcher_Translate.json";

    var config = null;
    var fontFace = "";
    var wrapEnabled = false;
    var debugEnabled = false;
    var titleOverlayEnabled = false;
    var titleOverlayX = 0;
    var titleOverlayY = 0;
    var titleOverlayFontSize = 48;
    var titleOverlayBackground = 0;
    var configuredUiLabels = [];
    var uiLabelSet = createMap();
    var uiLabelCount = 0;

    var dictionary = null;
    var translatedValues = null;
    var entryCount = 0;
    var speakerNameMap = createMap();
    var speakerNameCount = 0;

    var hitCount = 0;
    var missCount = 0;
    var blockHitCount = 0;
    var lineHitCount = 0;
    var pageHitCount = 0;
    var missSeen = createMap();

    var hooks = [];
    var rawNamesForConditional = false;
    var rawDataNamesForScript = false;
    var baseAllText = null;

    var hasNormalizeSupport = false;
    try {
        hasNormalizeSupport = typeof String.prototype.normalize === "function";
    } catch (normalizeCheckError) {
        hasNormalizeSupport = false;
    }

    // ---------------------------------------------------------------------
    // Small utilities
    // ---------------------------------------------------------------------

    function errorText(error) {
        try {
            if (error && typeof error.message === "string") {
                return error.message;
            }
        } catch (messageError) {
            // Fall through to String().
        }
        try {
            return String(error);
        } catch (stringError) {
            return "unknown error";
        }
    }

    function log(message) {
        try {
            if (typeof console !== "undefined" && console && typeof console.log === "function") {
                console.log(LOG_PREFIX + " " + message);
            }
        } catch (consoleError) {
            // Logging must never break the game.
        }
    }

    function logWarn(message) {
        try {
            if (typeof console !== "undefined" && console) {
                if (typeof console.warn === "function") {
                    console.warn(LOG_PREFIX + " " + message);
                    return;
                }
                if (typeof console.log === "function") {
                    console.log(LOG_PREFIX + " " + message);
                }
            }
        } catch (consoleError) {
            // Logging must never break the game.
        }
    }

    function logDebug(message) {
        if (debugEnabled) {
            log(message);
        }
    }

    function formatForLog(value) {
        var text = "";
        try {
            text = String(value);
        } catch (error) {
            text = "[unprintable]";
        }
        if (text.length > 200) {
            text = text.slice(0, 200) + "...";
        }
        return text.replace(/\r/g, "\\r").replace(/\n/g, "\\n");
    }

    function createMap() {
        try {
            if (typeof Object.create === "function") {
                return Object.create(null);
            }
        } catch (createError) {
            // Fall through to a plain object with hasOwnProperty checks.
        }
        return {};
    }

    function setMapValue(map, key, value) {
        if (key === "__proto__" && typeof Object.defineProperty === "function") {
            try {
                Object.defineProperty(map, key, {
                    value: value,
                    enumerable: true,
                    writable: true,
                    configurable: true
                });
                return;
            } catch (defineError) {
                // Fall through to a normal assignment.
            }
        }
        try {
            map[key] = value;
        } catch (assignError) {
            // A frozen map cannot be populated; callers handle empty maps.
        }
    }

    function hasOwn(object, key) {
        return Object.prototype.hasOwnProperty.call(object, key);
    }

    function isArray(value) {
        return Object.prototype.toString.call(value) === "[object Array]";
    }

    function isPlainObject(value) {
        return value !== null && typeof value === "object" && !isArray(value);
    }

    function isFiniteNumber(value) {
        return typeof value === "number" && isFinite(value);
    }

    function isBlankString(value) {
        return typeof value === "string" && /^\s*$/.test(value);
    }

    function normalizeText(value) {
        if (typeof value !== "string") {
            return value;
        }
        var result = value;
        try {
            result = result.replace(/\r\n?/g, "\n");
        } catch (replaceError) {
            // Keep the original string if an old runtime rejects the regexp.
        }
        if (hasNormalizeSupport) {
            try {
                result = result.normalize("NFC");
            } catch (normalizeError) {
                // CRLF normalization above still applies.
            }
        }
        return result;
    }

    // ---------------------------------------------------------------------
    // Filesystem / URL loading
    // ---------------------------------------------------------------------

    function getNodeFs() {
        var localRequire = null;
        try {
            if (typeof require === "function") {
                localRequire = require;
            }
        } catch (requireError) {
            localRequire = null;
        }
        if (!localRequire) {
            try {
                if (window && typeof window.require === "function") {
                    localRequire = window.require;
                }
            } catch (windowRequireError) {
                localRequire = null;
            }
        }
        if (!localRequire) {
            return null;
        }
        try {
            var fs = localRequire("fs");
            if (fs && typeof fs.readFileSync === "function") {
                return fs;
            }
        } catch (fsError) {
            // Node integration is unavailable; use XMLHttpRequest below.
        }
        return null;
    }

    function getXhrConstructor() {
        try {
            if (typeof XMLHttpRequest !== "undefined") {
                return XMLHttpRequest;
            }
        } catch (xhrReferenceError) {
            // Check window.XMLHttpRequest below.
        }
        try {
            if (window && typeof window.XMLHttpRequest === "function") {
                return window.XMLHttpRequest;
            }
        } catch (windowXhrError) {
            // No synchronous XHR is available.
        }
        return null;
    }

    function getPageProtocol() {
        try {
            if (window.location && typeof window.location.protocol === "string") {
                return window.location.protocol.toLowerCase();
            }
        } catch (locationError) {
            // Treat an unknown location as a local file.
        }
        return "";
    }

    function getBaseDirectory() {
        var href = "";
        try {
            if (window.location && typeof window.location.href === "string") {
                href = window.location.href;
            }
            if (!href && window.location && typeof window.location.pathname === "string") {
                href = window.location.pathname;
            }
        } catch (hrefError) {
            href = "";
        }
        if (!href) {
            return "";
        }

        // Strip the query string and fragment before touching the filesystem path.
        var clean = href.replace(/[?#].*$/, "");
        var path = clean;

        if (/^file:\/\//i.test(path)) {
            path = path.replace(/^file:\/\//i, "");
            if (/^localhost\//i.test(path)) {
                path = path.slice("localhost".length);
            }
            // file:///C:/... becomes /C:/...; normalize Windows-style URLs.
            if (/^\/[A-Za-z]:\//.test(path)) {
                path = path.slice(1);
            }
        } else if (/^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(path)) {
            try {
                path = window.location.pathname || "";
            } catch (pathnameError) {
                path = clean.replace(/^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\/]*/, "");
            }
        }

        try {
            path = decodeURIComponent(path);
        } catch (decodeError) {
            // Leave the encoded path alone; relative XHR can still load the file.
        }

        path = path.replace(/\\/g, "/");
        if (!path) {
            return "";
        }

        var hadLeadingSlash = path.charAt(0) === "/";
        if (path.charAt(path.length - 1) === "/") {
            path = path.slice(0, -1);
        } else {
            var slash = path.lastIndexOf("/");
            if (slash >= 0) {
                path = path.slice(0, slash);
            } else {
                path = "";
            }
        }

        if (!path && hadLeadingSlash) {
            path = "/";
        }
        return path;
    }

    function joinPath(directory, relativePath) {
        if (!directory) {
            return relativePath;
        }
        if (directory.charAt(directory.length - 1) === "/") {
            return directory + relativePath;
        }
        return directory + "/" + relativePath;
    }

    function isFileProtocol() {
        var protocol = getPageProtocol();
        return !protocol || protocol === "file:";
    }

    function readViaXhr(relativePath) {
        var XhrConstructor = getXhrConstructor();
        if (!XhrConstructor) {
            return null;
        }
        try {
            var xhr = new XhrConstructor();
            xhr.open("GET", relativePath, false);
            try {
                if (typeof xhr.overrideMimeType === "function") {
                    xhr.overrideMimeType("text/plain; charset=utf-8");
                }
            } catch (mimeError) {
                // Some old runtimes do not support overrideMimeType.
            }
            xhr.send(null);
            if (xhr.status === 0 || (xhr.status >= 200 && xhr.status < 300)) {
                if (typeof xhr.responseText === "string") {
                    return xhr.responseText;
                }
            }
            logDebug("XHR could not load " + relativePath + " (status " + xhr.status + ").");
        } catch (xhrError) {
            logDebug("XHR failed for " + relativePath + ": " + errorText(xhrError));
        }
        return null;
    }

    function readPluginText(fileName) {
        var relativePath = PLUGIN_DIR + fileName;
        var directory = getBaseDirectory();
        var fs = getNodeFs();
        var fileProtocol = isFileProtocol();

        if (fs && directory && fileProtocol) {
            try {
                return fs.readFileSync(joinPath(directory, relativePath), "utf8");
            } catch (readError) {
                logDebug("fs could not read " + joinPath(directory, relativePath) + ": " + errorText(readError));
            }
        }

        var xhrText = readViaXhr(relativePath);
        if (xhrText !== null) {
            return xhrText;
        }

        // If an unusual URL made the fs path look non-file-like, try it once more.
        if (fs && directory && !fileProtocol) {
            try {
                return fs.readFileSync(joinPath(directory, relativePath), "utf8");
            } catch (readError2) {
                logDebug("fs fallback could not read " + joinPath(directory, relativePath) + ": " + errorText(readError2));
            }
        }

        return null;
    }

    function parseJsonText(text) {
        if (typeof text !== "string") {
            return null;
        }
        if (text.length > 0 && text.charCodeAt(0) === 0xFEFF) {
            text = text.slice(1);
        }
        try {
            return JSON.parse(text);
        } catch (parseError) {
            logDebug("JSON parse failed: " + errorText(parseError));
            return null;
        }
    }

    // ---------------------------------------------------------------------
    // Config and dictionary
    // ---------------------------------------------------------------------

    function readConfig() {
        var text = readPluginText(CONFIG_FILE);
        if (text === null) {
            logWarn("Translation config not found or unreadable; translation disabled.");
            return null;
        }

        var parsed = parseJsonText(text);
        if (!isPlainObject(parsed)) {
            logWarn("Translation config is invalid; translation disabled.");
            return null;
        }
        if (parsed.enabled !== true) {
            log("Translation disabled by config.");
            return null;
        }

        var configuredFont = "";
        if (typeof parsed.fontFace === "string") {
            configuredFont = parsed.fontFace.replace(/^\s+|\s+$/g, "");
        }

        var uiLabels = [];
        if (isArray(parsed.uiLabels)) {
            for (var i = 0; i < parsed.uiLabels.length; i++) {
                if (typeof parsed.uiLabels[i] === "string" &&
                        parsed.uiLabels[i].length > 0) {
                    uiLabels.push(parsed.uiLabels[i]);
                }
            }
        }

        function configNumber(value, fallback, min, max) {
            if (typeof value !== "number" || !isFinite(value)) {
                return fallback;
            }
            return Math.max(min, Math.min(max, Math.floor(value)));
        }

        return {
            enabled: true,
            fontFace: configuredFont,
            wrap: typeof parsed.wrap === "boolean" ? parsed.wrap : true,
            debug: parsed.debug === true,
            titleOverlay: parsed.titleOverlay === true,
            titleOverlayX: configNumber(parsed.titleOverlayX, 0, -2000, 2000),
            titleOverlayY: configNumber(parsed.titleOverlayY, 0, -2000, 2000),
            titleOverlayFontSize: configNumber(
                parsed.titleOverlayFontSize, 48, 8, 200
            ),
            titleOverlayBackground: configNumber(
                parsed.titleOverlayBackground, 0, 0, 255
            ),
            uiLabels: uiLabels
        };
    }

    function loadDictionary() {
        var text = readPluginText(DICT_FILE);
        if (text === null) {
            logWarn("Translation dictionary not found or unreadable; translation disabled.");
            return false;
        }

        var parsed = parseJsonText(text);
        if (!isPlainObject(parsed)) {
            logWarn("Translation dictionary is invalid; translation disabled.");
            return false;
        }

        var loaded = createMap();
        var values = createMap();
        var count = 0;
        var emptyCount = 0;
        var nonStringCount = 0;
        var duplicateCount = 0;

        for (var sourceKey in parsed) {
            if (!hasOwn(parsed, sourceKey)) {
                continue;
            }
            var translation = parsed[sourceKey];
            if (typeof translation !== "string") {
                nonStringCount++;
                continue;
            }

            var normalizedKey = normalizeText(sourceKey);
            var normalizedValue = normalizeText(translation);
            if (!normalizedKey || isBlankString(normalizedKey) ||
                    !normalizedValue || isBlankString(normalizedValue)) {
                emptyCount++;
                continue;
            }

            if (hasOwn(loaded, normalizedKey)) {
                duplicateCount++;
            } else {
                count++;
            }
            setMapValue(loaded, normalizedKey, normalizedValue);
        }

        // Build the double-translation guard from the final values only, so a
        // value replaced by a duplicate normalized key cannot block a source.
        for (var finalKey in loaded) {
            if (hasOwn(loaded, finalKey)) {
                setMapValue(values, loaded[finalKey], true);
            }
        }

        dictionary = loaded;
        translatedValues = values;
        entryCount = count;
        buildSpeakerNameMap();

        if (nonStringCount > 0) {
            logWarn("Skipped " + nonStringCount + " non-string dictionary value(s).");
        }
        if (duplicateCount > 0) {
            logDebug("Skipped or replaced " + duplicateCount + " duplicate normalized dictionary key(s).");
        }
        if (emptyCount > 0) {
            logDebug("Skipped " + emptyCount + " empty dictionary entry or value.");
        }
        logDebug("Loaded " + entryCount + " translation entry/entries.");
        if (!hasNormalizeSupport) {
            logDebug("String.prototype.normalize is unavailable; NFD matching may fail.");
        }
        return true;
    }

    function extractNameTags(text) {
        var tags = [];
        if (typeof text !== "string") {
            return tags;
        }
        var regex = /\\n[cr]?<([^>]*)>/g;
        var match = null;
        while ((match = regex.exec(text)) !== null) {
            tags.push(match[1]);
        }
        return tags;
    }

    function isDynamicName(name) {
        return /\\(?:V|N|P|v|n|p)\s*\[/.test(name);
    }

    function buildSpeakerNameMap() {
        speakerNameMap = createMap();
        speakerNameCount = 0;
        if (!dictionary) {
            return;
        }
        for (var sourceKey in dictionary) {
            if (!hasOwn(dictionary, sourceKey)) {
                continue;
            }
            var translated = dictionary[sourceKey];
            var rawTags = extractNameTags(sourceKey);
            var translatedTags = extractNameTags(translated);
            if (rawTags.length === 0 || rawTags.length !== translatedTags.length) {
                continue;
            }
            for (var i = 0; i < rawTags.length; i++) {
                var rawName = normalizeText(rawTags[i]).replace(/^\s+|\s+$/g, "");
                var translatedName = normalizeText(translatedTags[i]).replace(/^\s+|\s+$/g, "");
                if (!rawName || !translatedName || rawName === translatedName ||
                        isDynamicName(rawName) || hasOwn(speakerNameMap, rawName)) {
                    continue;
                }
                setMapValue(speakerNameMap, rawName, translatedName);
                speakerNameCount++;
            }
        }
        if (speakerNameCount > 0) {
            logDebug("Derived " + speakerNameCount + " speaker name translation(s).");
        }
    }

    function lookupSpeakerName(text) {
        if (typeof text !== "string") {
            return null;
        }
        var normalized = normalizeText(text);
        if (!normalized || isBlankString(normalized)) {
            return null;
        }
        if (translatedValues && hasOwn(translatedValues, normalized)) {
            return null;
        }
        if (hasOwn(speakerNameMap, normalized)) {
            return speakerNameMap[normalized];
        }
        return null;
    }

    function buildUiLabelSet() {
        uiLabelSet = createMap();
        uiLabelCount = 0;
        if (!dictionary) {
            return;
        }
        for (var i = 0; i < configuredUiLabels.length; i++) {
            var label = normalizeText(configuredUiLabels[i]).replace(/^\s+|\s+$/g, "");
            if (!label || hasOwn(uiLabelSet, label) || !hasOwn(dictionary, label)) {
                continue;
            }
            setMapValue(uiLabelSet, label, true);
            uiLabelCount++;
        }
    }

    function translateUiLabel(text) {
        if (typeof text !== "string" || text.length < 2) {
            return text;
        }
        var normalized = normalizeText(text);
        if (!normalized || !hasOwn(uiLabelSet, normalized)) {
            return text;
        }
        if (translatedValues && hasOwn(translatedValues, normalized)) {
            return text;
        }
        var translated = dictionary[normalized];
        return typeof translated === "string" && translated.length > 0 ?
            translated : text;
    }

    function recordMiss(normalizedText, originalText) {
        missCount++;
        if (!debugEnabled) {
            return;
        }
        if (hasOwn(missSeen, normalizedText)) {
            return;
        }
        setMapValue(missSeen, normalizedText, true);
        logDebug("Dictionary miss: " + formatForLog(originalText));
    }

    function translateTextInternal(text) {
        if (typeof text !== "string" || text.length === 0) {
            return text;
        }
        if (!dictionary || !translatedValues) {
            return text;
        }

        var normalized = normalizeText(text);
        if (!normalized || isBlankString(normalized)) {
            return text;
        }
        if (hasOwn(dictionary, normalized)) {
            hitCount++;
            return dictionary[normalized];
        }
        // A translated value that is not also a source key should not be
        // translated a second time by runtime display hooks.
        if (hasOwn(translatedValues, normalized)) {
            return text;
        }

        recordMiss(normalized, text);
        return text;
    }

    function translateNameText(text) {
        if (typeof text !== "string" || text.length === 0) {
            return text;
        }
        try {
            var exact = translateTextInternal(text);
            if (exact !== text) {
                return exact;
            }
            var exactName = lookupSpeakerName(text);
            if (exactName !== null) {
                return exactName;
            }

            var output = "";
            var run = "";
            var changed = false;

            function flushNameRun() {
                if (!run) {
                    return;
                }
                var translated = translateTextInternal(run);
                if (translated === run) {
                    var mapped = lookupSpeakerName(run);
                    if (mapped !== null) {
                        translated = mapped;
                    }
                }
                if (translated !== run) {
                    changed = true;
                }
                output += translated;
                run = "";
            }

            var index = 0;
            while (index < text.length) {
                if (text.charAt(index) === "\\") {
                    flushNameRun();
                    var escape = readEscapeCode(text, index);
                    output += escape.text;
                    index = escape.end;
                } else {
                    run += text.charAt(index);
                    index++;
                }
            }
            flushNameRun();
            return changed ? output : text;
        } catch (nameError) {
            logDebug("Name translation failed: " + errorText(nameError));
            return text;
        }
    }

    function translatePageOrLines(text) {
        if (typeof text !== "string" || text.length === 0) {
            return text;
        }
        var direct = translateTextInternal(text);
        if (direct !== text) {
            return direct;
        }
        var lines = text.split("\n");
        var translatedLines = [];
        var changed = false;
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            var translatedLine = translateTextInternal(line);
            if (translatedLine !== line) {
                changed = true;
            }
            translatedLines.push(translatedLine);
        }
        if (changed) {
            lineHitCount++;
            return translatedLines.join("\n");
        }
        return text;
    }

    function pageSeparatorSignature(text) {
        var parts = text.split(/(\f\n?)/);
        var signature = [];
        for (var i = 1; i < parts.length; i += 2) {
            signature.push(parts[i]);
        }
        return signature.join("\u0001");
    }

    function translateBlockInternal(text) {
        if (typeof text !== "string" || text.length === 0) {
            return text;
        }
        if (!dictionary || !translatedValues) {
            return text;
        }

        var normalized = normalizeText(text);
        if (!normalized) {
            return text;
        }
        if (hasOwn(dictionary, normalized)) {
            var exactTranslation = dictionary[normalized];
            if (pageSeparatorSignature(exactTranslation) ===
                    pageSeparatorSignature(normalized)) {
                blockHitCount++;
                hitCount++;
                return exactTranslation;
            }
            logWarn("Translation changed the message page separators; keeping source text.");
        }
        // A block key is the preferred lookup. If it misses, translate each
        // page (or line) with MTool-style line keys and rejoin the result.
        if (normalized.indexOf("\n") >= 0) {
            recordMiss(normalized, text);
        }

        if (normalized.indexOf("\f") >= 0) {
            var parts = normalized.split(/(\f\n?)/);
            var translatedParts = [];
            var pageChanged = false;
            for (var p = 0; p < parts.length; p++) {
                var part = parts[p];
                if (p % 2 === 1) {
                    translatedParts.push(part);
                    continue;
                }
                var translatedPart = translatePageOrLines(part);
                if (translatedPart.indexOf("\f") >= 0) {
                    logWarn("A page translation introduced a form feed; keeping source page.");
                    translatedPart = part;
                }
                if (translatedPart !== part) {
                    pageChanged = true;
                }
                translatedParts.push(translatedPart);
            }
            if (pageChanged) {
                pageHitCount++;
                return translatedParts.join("");
            }
            return text;
        }

        var translated = translatePageOrLines(normalized);
        return translated !== normalized ? translated : text;
    }

    // ---------------------------------------------------------------------
    // DataManager.onLoad display-field translation
    // ---------------------------------------------------------------------

    function defineTranslatedField(object, fieldName, translated) {
        if (!object || typeof object !== "object" ||
                typeof translated !== "string" || translated.length === 0) {
            return;
        }
        try {
            var descriptor = Object.getOwnPropertyDescriptor(object, fieldName);
            if (descriptor && descriptor.get && descriptor.get.__rpgmTranslated) {
                return;
            }
            var rawValue = object[fieldName];
            var currentValue = translated;
            var getter = function() {
                return rawDataNamesForScript ? rawValue : currentValue;
            };
            getter.__rpgmTranslated = true;
            Object.defineProperty(object, fieldName, {
                configurable: true,
                enumerable: true,
                get: getter,
                set: function(value) {
                    rawValue = value;
                    try {
                        currentValue = translateTextInternal(value);
                    } catch (setterError) {
                        currentValue = value;
                    }
                        }
            });
        } catch (defineError) {
            try {
                object[fieldName] = translated;
            } catch (assignError) {
                logDebug("Could not apply translated field " + fieldName + ": " +
                    errorText(assignError));
            }
        }
    }

    function translateField(object, fieldName) {
        if (!object || typeof object !== "object") {
            return;
        }
        var value = object[fieldName];
        if (typeof value !== "string" || value.length === 0) {
            return;
        }
        var translated = translateTextInternal(value);
        if (translated !== value) {
            defineTranslatedField(object, fieldName, translated);
        }
    }

    function translateArrayFields(array, fieldNames) {
        if (!array) {
            return;
        }
        for (var i = 0; i < array.length; i++) {
            var item = array[i];
            if (!item || typeof item !== "object") {
                continue;
            }
            for (var j = 0; j < fieldNames.length; j++) {
                translateField(item, fieldNames[j]);
            }
        }
    }

    function translateStringArray(array) {
        if (!isArray(array)) {
            return;
        }
        for (var i = 0; i < array.length; i++) {
            if (typeof array[i] !== "string" || array[i].length === 0) {
                continue;
            }
            var translated = translateTextInternal(array[i]);
            if (translated !== array[i]) {
                defineTranslatedField(array, i, translated);
            }
        }
    }

    function translateTermMessages(messages) {
        if (!messages || typeof messages !== "object") {
            return;
        }
        for (var key in messages) {
            if (!hasOwn(messages, key)) {
                continue;
            }
            if (typeof messages[key] !== "string" || messages[key].length === 0) {
                continue;
            }
            var translated = translateTextInternal(messages[key]);
            if (translated !== messages[key]) {
                defineTranslatedField(messages, key, translated);
            }
        }
    }

    function translateSystem(system) {
        if (!system || typeof system !== "object") {
            return;
        }

        translateField(system, "currencyUnit");
        translateStringArray(system.elements);
        translateStringArray(system.skillTypes);
        translateStringArray(system.weaponTypes);
        translateStringArray(system.armorTypes);
        translateStringArray(system.equipTypes);

        var terms = system.terms;
        if (terms && typeof terms === "object") {
            translateStringArray(terms.basic);
            translateStringArray(terms.commands);
            translateStringArray(terms.params);
            translateTermMessages(terms.messages);
        }
    }

    function translateDataObject(object) {
        if (!object) {
            return;
        }

        // Never translate $dataActors, $dataTilesets, $dataAnimations,
        // $dataTroops, or $dataCommonEvents: their text is either editor-only
        // or handled at display time.
        if (object === window.$dataClasses) {
            translateArrayFields(object, ["name"]);
        } else if (object === window.$dataSkills) {
            translateArrayFields(object, ["name", "description", "message1", "message2"]);
        } else if (object === window.$dataItems) {
            translateArrayFields(object, ["name", "description"]);
        } else if (object === window.$dataWeapons) {
            translateArrayFields(object, ["name", "description"]);
        } else if (object === window.$dataArmors) {
            translateArrayFields(object, ["name", "description"]);
        } else if (object === window.$dataEnemies) {
            translateArrayFields(object, ["name"]);
        } else if (object === window.$dataStates) {
            translateArrayFields(object, ["name", "message1", "message2", "message3", "message4"]);
        } else if (object === window.$dataMapInfos) {
            translateArrayFields(object, ["name"]);
        } else if (object === window.$dataMap) {
            // Map event commands, names, notes, and metadata stay untouched.
            translateField(object, "displayName");
        } else if (object === window.$dataSystem) {
            translateSystem(object);
        }
    }

    // ---------------------------------------------------------------------
    // Wrapping
    // ---------------------------------------------------------------------

    function isHighSurrogate(code) {
        return code >= 0xD800 && code <= 0xDBFF;
    }

    function isLowSurrogate(code) {
        return code >= 0xDC00 && code <= 0xDFFF;
    }

    function isCJKCode(code) {
        return (code >= 0x2E80 && code <= 0x2FFF) ||
            (code >= 0x3000 && code <= 0x303F) ||
            (code >= 0x3040 && code <= 0x30FF) ||
            (code >= 0x3100 && code <= 0x312F) ||
            (code >= 0x3130 && code <= 0x318F) ||
            (code >= 0x3190 && code <= 0x31FF) ||
            (code >= 0x3200 && code <= 0x4DBF) ||
            (code >= 0x4E00 && code <= 0x9FFF) ||
            (code >= 0xA960 && code <= 0xA97F) ||
            (code >= 0xAC00 && code <= 0xD7AF) ||
            (code >= 0xF900 && code <= 0xFAFF) ||
            (code >= 0xFE10 && code <= 0xFE1F) ||
            (code >= 0xFE30 && code <= 0xFE4F) ||
            (code >= 0xFF00 && code <= 0xFFEF);
    }

    function isWrapWhitespace(ch) {
        if (ch === " " || ch === "\t" || ch === "\u00A0" || ch === "\u3000") {
            return true;
        }
        var code = ch.charCodeAt(0);
        return code === 0x0B || code === 0x1680 ||
            (code >= 0x2000 && code <= 0x200A) ||
            code === 0x202F || code === 0x205F;
    }

    function readEscapeCode(text, index) {
        var length = text.length;
        var i = index + 1;
        if (i >= length) {
            return { text: "\\", end: i };
        }

        var ch = text.charAt(i);
        if (ch === "\r" || ch === "\n" || ch === "\f") {
            // Keep layout characters separate from a dangling backslash.
            return { text: "\\", end: index + 1 };
        }
        if (/[A-Za-z]/.test(ch)) {
            i++;
            // MZ additionally has two-letter codes. Only treat the second
            // letter as part of the code when a bracketed parameter follows,
            // so plain text after \G or \P stays visible.
            if (i + 1 < length && /[A-Za-z]/.test(text.charAt(i)) &&
                    text.charAt(i + 1) === "[") {
                var pair = (ch + text.charAt(i)).toUpperCase();
                if (pair === "FS" || pair === "PX" || pair === "PY") {
                    i++;
                }
            }
            if (i < length && text.charAt(i) === "[") {
                var closeIndex = text.indexOf("]", i + 1);
                if (closeIndex >= 0) {
                    i = closeIndex + 1;
                } else {
                    i = length;
                }
            }
        } else {
            i++;
        }
        return { text: text.slice(index, i), end: i };
    }

    function parseWrapUnits(rawText) {
        var units = [];
        var pending = "";
        var i = 0;
        var length = rawText.length;

        while (i < length) {
            var ch = rawText.charAt(i);
            if (ch === "\\") {
                var escape = readEscapeCode(rawText, i);
                pending += escape.text;
                i = escape.end;
                continue;
            }

            var unit = ch;
            var code = rawText.charCodeAt(i);
            if (isHighSurrogate(code) && i + 1 < length &&
                    isLowSurrogate(rawText.charCodeAt(i + 1))) {
                unit += rawText.charAt(i + 1);
                i += 2;
            } else {
                i++;
            }
            units.push({ text: pending + unit, visible: unit });
            pending = "";
        }

        if (pending) {
            if (units.length > 0) {
                units[units.length - 1].text += pending;
            } else {
                units.push({ text: pending, visible: "" });
            }
        }
        return units;
    }

    function tokenizeForWrap(text) {
        var tokens = [];
        var pending = "";
        var i = 0;
        var length = text.length;

        while (i < length) {
            var ch = text.charAt(i);
            var code = text.charCodeAt(i);

            if (ch === "\\") {
                var escape = readEscapeCode(text, i);
                pending += escape.text;
                i = escape.end;
                continue;
            }

            if (ch === "\r") {
                var newline = "\n";
                if (i + 1 < length && text.charAt(i + 1) === "\n") {
                    newline = "\r\n";
                    i += 2;
                } else {
                    i += 1;
                }
                tokens.push({ kind: "newline", text: pending + newline, visible: "" });
                pending = "";
                continue;
            }

            if (ch === "\n") {
                tokens.push({ kind: "newline", text: pending + "\n", visible: "" });
                pending = "";
                i += 1;
                continue;
            }

            if (ch === "\f") {
                tokens.push({ kind: "page", text: pending + "\f", visible: "" });
                pending = "";
                i += 1;
                continue;
            }

            if (isWrapWhitespace(ch)) {
                var whitespaceStart = i;
                while (i < length && isWrapWhitespace(text.charAt(i))) {
                    i++;
                }
                var whitespace = text.slice(whitespaceStart, i);
                tokens.push({ kind: "space", text: pending + whitespace, visible: whitespace });
                pending = "";
                continue;
            }

            if (isHighSurrogate(code) || isCJKCode(code)) {
                var unit = ch;
                if (isHighSurrogate(code) && i + 1 < length &&
                        isLowSurrogate(text.charCodeAt(i + 1))) {
                    unit += text.charAt(i + 1);
                    i += 2;
                } else {
                    i += 1;
                }
                tokens.push({ kind: "char", text: pending + unit, visible: unit });
                pending = "";
                continue;
            }

            var wordText = pending;
            var wordVisible = "";
            pending = "";

            while (i < length) {
                ch = text.charAt(i);
                code = text.charCodeAt(i);
                if (ch === "\r" || ch === "\n" || ch === "\f" ||
                        isWrapWhitespace(ch) || isCJKCode(code) || isHighSurrogate(code)) {
                    break;
                }
                if (ch === "\\") {
                    var wordEscape = readEscapeCode(text, i);
                    wordText += wordEscape.text;
                    i = wordEscape.end;
                    continue;
                }
                wordText += ch;
                wordVisible += ch;
                i++;
            }

            if (wordText.length > 0 || wordVisible.length > 0) {
                tokens.push({ kind: "word", text: wordText, visible: wordVisible });
            } else {
                // Defensive fallback: consume one code unit so this cannot loop.
                tokens.push({ kind: "word", text: pending + ch, visible: ch });
                pending = "";
                i++;
            }
        }

        if (pending) {
            if (tokens.length > 0) {
                tokens[tokens.length - 1].text += pending;
            } else {
                tokens.push({ kind: "code", text: pending, visible: "" });
            }
        }
        return tokens;
    }

    function defaultMeasure(text) {
        return text.length * 12;
    }

    function measureWidth(measureFn, text) {
        if (!text) {
            return 0;
        }
        if (typeof measureFn !== "function") {
            measureFn = defaultMeasure;
        }
        var width = 0;
        try {
            width = measureFn(text);
        } catch (measureError) {
            width = 0;
        }
        if (!isFiniteNumber(width) || width < 0) {
            width = text.length * 12;
        }
        return width;
    }

    function splitTokenForWrap(token, maxWidth, measureFn) {
        var units = parseWrapUnits(token.text);
        if (units.length === 0) {
            return [token];
        }

        var parts = [];
        var partText = "";
        var partVisible = "";
        var partWidth = 0;

        for (var i = 0; i < units.length; i++) {
            var unit = units[i];
            var unitWidth = measureWidth(measureFn, unit.visible) +
                estimateCodeWidth(unit.text, measureFn);
            if (partVisible.length > 0 && partWidth + unitWidth > maxWidth) {
                parts.push({ kind: token.kind, text: partText, visible: partVisible });
                partText = "";
                partVisible = "";
                partWidth = 0;
            }
            partText += unit.text;
            partVisible += unit.visible;
            partWidth += unitWidth;
        }

        if (partText.length > 0 || partVisible.length > 0) {
            parts.push({ kind: token.kind, text: partText, visible: partVisible });
        }
        return parts.length > 0 ? parts : [token];
    }

    function flushWrappedLine(output, line) {
        // Drop only the whitespace at an automatically inserted break. Manual
        // newlines keep their preceding whitespace intact.
        var trimmed = line.replace(/[ \t]+$/, "");
        return output + trimmed + "\n";
    }

    function estimateCodeWidth(text, measureFn) {
        var total = 0;
        var i = 0;
        while (i < text.length) {
            if (text.charAt(i) !== "\\") {
                i++;
                continue;
            }
            var escape = readEscapeCode(text, i);
            var code = escape.text.toUpperCase();
            if (code.indexOf("\\I[") === 0) {
                var iconWidth = 32;
                try {
                    if (window.ImageManager && isFiniteNumber(window.ImageManager.iconWidth)) {
                        iconWidth = window.ImageManager.iconWidth;
                    }
                } catch (iconError) {
                    iconWidth = 32;
                }
                total += iconWidth + 4;
            } else if (code.indexOf("\\N[") === 0 || code.indexOf("\\P[") === 0) {
                total += measureWidth(measureFn, "MMMMMMMM");
            } else if (code.indexOf("\\V[") === 0) {
                total += measureWidth(measureFn, "MMMM");
            } else if (code === "\\G") {
                total += measureWidth(measureFn, "M");
            }
            i = escape.end;
        }
        return total;
    }

    function wrapTextInternal(text, maxWidth, measureFn) {
        if (typeof text !== "string" || text.length === 0) {
            return text;
        }
        if (!isFiniteNumber(maxWidth) || maxWidth <= 0) {
            return text;
        }
        if (typeof measureFn !== "function") {
            measureFn = defaultMeasure;
        }

        try {
            var tokens = tokenizeForWrap(text);
            var output = "";
            var line = "";
            var lineWidth = 0;
            var hasContent = false;

            for (var i = 0; i < tokens.length; i++) {
                var token = tokens[i];

                if (token.kind === "newline") {
                    line += token.text;
                    output += line;
                    line = "";
                    lineWidth = 0;
                    hasContent = false;
                    continue;
                }

                if (token.kind === "page") {
                    line += token.text;
                    // Text after a form feed starts at the left edge of a new page.
                    lineWidth = 0;
                    hasContent = false;
                    continue;
                }

                var width = measureWidth(measureFn, token.visible) +
                    estimateCodeWidth(token.text, measureFn);
                if (token.kind === "space" || token.kind === "code") {
                    line += token.text;
                    lineWidth += width;
                    continue;
                }

                if (hasContent && lineWidth + width > maxWidth) {
                    output = flushWrappedLine(output, line);
                    line = "";
                    lineWidth = 0;
                    hasContent = false;
                }

                if (width > maxWidth && lineWidth + width > maxWidth) {
                    // Long Latin words (or a single CJK glyph wider than the
                    // window) fall back to character boundaries.
                    var parts = splitTokenForWrap(token, maxWidth, measureFn);
                    for (var p = 0; p < parts.length; p++) {
                        var part = parts[p];
                        var partWidth = measureWidth(measureFn, part.visible) +
                            estimateCodeWidth(part.text, measureFn);
                        if (hasContent && lineWidth + partWidth > maxWidth) {
                            output = flushWrappedLine(output, line);
                            line = "";
                            lineWidth = 0;
                            hasContent = false;
                        }
                        line += part.text;
                        lineWidth += partWidth;
                        if (part.visible.length > 0) {
                            hasContent = true;
                        }
                    }
                    continue;
                }

                line += token.text;
                lineWidth += width;
                if (token.visible.length > 0) {
                    hasContent = true;
                }
            }

            output += line;
            return output;
        } catch (wrapError) {
            logDebug("Text wrapping failed: " + errorText(wrapError));
            return text;
        }
    }

    function getTotalWidth(windowObject) {
        var total = null;
        try {
            if (windowObject.innerWidth !== undefined && windowObject.innerWidth !== null) {
                total = typeof windowObject.innerWidth === "function" ?
                    windowObject.innerWidth() : windowObject.innerWidth;
            }
        } catch (innerWidthError) {
            total = null;
        }
        if (!isFiniteNumber(total) || total <= 0) {
            try {
                if (typeof windowObject.contentsWidth === "function") {
                    total = windowObject.contentsWidth();
                } else if (typeof windowObject.contentsWidth === "number") {
                    total = windowObject.contentsWidth;
                }
            } catch (contentsWidthError) {
                total = null;
            }
        }
        if (!isFiniteNumber(total) || total <= 0) {
            try {
                total = typeof windowObject.width === "function" ?
                    windowObject.width() : windowObject.width;
            } catch (widthError) {
                total = null;
            }
        }
        return isFiniteNumber(total) && total > 0 ? total : 0;
    }

    function getNewLineX(windowObject) {
        if (!windowObject) {
            return 0;
        }
        var startX = null;
        if (typeof windowObject.newLineX === "function") {
            try {
                startX = windowObject.newLineX({ rtl: false });
            } catch (newLineError) {
                startX = null;
            }
            if (!isFiniteNumber(startX)) {
                try {
                    startX = windowObject.newLineX();
                } catch (newLineError2) {
                    startX = null;
                }
            }
        }
        return isFiniteNumber(startX) ? startX : 0;
    }

    function getMessageMaxWidth(windowObject) {
        // Window_Message has a face-aware newLineX(); baseTextRect is a generic
        // content width and would ignore the face-image offset. Only use
        // baseTextRect for windows that do not provide newLineX (e.g. scroll text).
        if (!windowObject || typeof windowObject.newLineX !== "function") {
            try {
                if (windowObject && typeof windowObject.baseTextRect === "function") {
                    var textRect = windowObject.baseTextRect();
                    if (textRect && isFiniteNumber(textRect.width) && textRect.width > 0) {
                        return textRect.width;
                    }
                }
            } catch (textRectError) {
                logDebug("Could not read baseTextRect for wrapping: " + errorText(textRectError));
            }
        }
        var totalWidth = getTotalWidth(windowObject);
        if (!(totalWidth > 0)) {
            return 0;
        }
        var startX = getNewLineX(windowObject);
        var maxWidth = totalWidth - startX - 8;
        return maxWidth > 0 ? maxWidth : 0;
    }

    function wrapTextForWindow(windowObject, text, maxWidth) {
        var measure = function(value) {
            var width = null;
            var measured = false;
            try {
                if (windowObject && typeof windowObject.textWidth === "function") {
                    width = windowObject.textWidth(value);
                    measured = true;
                } else if (windowObject && windowObject.contents &&
                        typeof windowObject.contents.measureTextWidth === "function") {
                    width = windowObject.contents.measureTextWidth(value);
                    measured = true;
                }
            } catch (measureError) {
                measured = false;
                width = null;
            }
            if (!measured || !isFiniteNumber(width) || width < 0) {
                width = value.length * 12;
            }
            return width;
        };
        return wrapTextInternal(text, maxWidth, measure);
    }

    function getRawMessageText(messageObject) {
        if (baseAllText && typeof baseAllText === "function") {
            try {
                return baseAllText.apply(messageObject, []);
            } catch (allTextError) {
                logDebug("Raw allText call failed: " + errorText(allTextError));
            }
        }
        if (messageObject && messageObject._texts &&
                typeof messageObject._texts.join === "function") {
            try {
                return messageObject._texts.join("\n");
            } catch (joinError) {
                logDebug("Could not read message _texts: " + errorText(joinError));
            }
        }
        return null;
    }

    function restoreMessageAllText(context) {
        if (!context || !context.message || !context.needRestore) {
            return;
        }
        var messageObject = context.message;
        var hadOwn = context.hadOwn;
        var oldValue = context.oldValue;
        context.needRestore = false;

        try {
            if (hadOwn) {
                messageObject.allText = oldValue;
            } else {
                var deleted = false;
                try {
                    deleted = delete messageObject.allText;
                } catch (deleteError) {
                    deleted = false;
                }
                if (!deleted) {
                    messageObject.allText = oldValue;
                }
            }
        } catch (restoreError) {
            try {
                messageObject.allText = oldValue;
            } catch (restoreError2) {
                logWarn("Could not restore $gameMessage.allText: " + errorText(restoreError2));
            }
        }
        try {
            if (context.shadow && messageObject.allText === context.shadow) {
                logWarn("The temporary $gameMessage.allText wrapper could not be removed.");
            }
        } catch (verifyRestoreError) {
            logDebug("Could not verify $gameMessage.allText restore: " + errorText(verifyRestoreError));
        }
    }

    // Note: an own non-writable $gameMessage.allText cannot be shadowed for
    // wrapping. Standard MV/MZ message objects inherit allText, so this only
    // affects unusual plugin-created message objects.
    function prepareMessageWrap(windowObject) {
        var context = {
            message: null,
            hadOwn: false,
            oldValue: null,
            needRestore: false
        };

        try {
            var messageObject = window.$gameMessage;
            if (!messageObject || typeof messageObject.allText !== "function") {
                return null;
            }

            // Translation is applied by the allText wrapper first. Wrapping is
            // only useful when the displayed text actually differs.
            var translated = messageObject.allText();
            if (typeof translated !== "string" || translated.length === 0) {
                return null;
            }
            var raw = getRawMessageText(messageObject);
            if (typeof raw !== "string" || translated === raw) {
                return null;
            }

            var maxWidth = getMessageMaxWidth(windowObject);
            if (!(maxWidth > 0)) {
                return null;
            }

            var wrapped = wrapTextForWindow(windowObject, translated, maxWidth);
            if (typeof wrapped !== "string" || wrapped === translated) {
                return null;
            }

            context.message = messageObject;
            context.hadOwn = hasOwn(messageObject, "allText");
            context.oldValue = messageObject.allText;
            context.needRestore = true;

            var shadow = function() {
                return wrapped;
            };
            context.shadow = shadow;
            try {
                messageObject.allText = shadow;
            } catch (shadowError) {
                logDebug("Could not install $gameMessage.allText shadow: " + errorText(shadowError));
            }

            var installed = false;
            try {
                installed = messageObject.allText === shadow;
            } catch (verifyError) {
                installed = false;
            }
            if (!installed) {
                restoreMessageAllText(context);
                return null;
            }
            return context;
        } catch (prepareError) {
            restoreMessageAllText(context);
            logDebug("Message wrapping skipped: " + errorText(prepareError));
            return null;
        }
    }

    function wrapStartMessage(originalStartMessage) {
        return function() {
            var context = null;
            try {
                if (wrapEnabled) {
                    context = prepareMessageWrap(this);
                }
            } catch (contextError) {
                if (context) {
                    restoreMessageAllText(context);
                }
                context = null;
                logDebug("Message wrapping preparation failed: " + errorText(contextError));
            }

            try {
                // The original method is always called exactly once. If the
                // shadow could not be installed, translation still happens via
                // the allText wrapper.
                return originalStartMessage.apply(this, arguments);
            } finally {
                if (context) {
                    restoreMessageAllText(context);
                }
            }
        };
    }

    // ---------------------------------------------------------------------
    // Hook installation
    // ---------------------------------------------------------------------

    function wrapExistingMethod(prototypeObject, methodName, createWrapper) {
        if (!prototypeObject || typeof prototypeObject[methodName] !== "function") {
            return false;
        }
        var original = prototypeObject[methodName];
        var wrapped = createWrapper(original);
        if (typeof wrapped !== "function") {
            return false;
        }
        try {
            prototypeObject[methodName] = wrapped;
        } catch (assignError) {
            logWarn("Could not install " + methodName + " hook: " + errorText(assignError));
            return false;
        }
        try {
            if (prototypeObject[methodName] !== wrapped) {
                logWarn("Could not install " + methodName + " hook (property is read-only).");
                return false;
            }
        } catch (verifyError) {
            logWarn("Could not verify " + methodName + " hook: " + errorText(verifyError));
            return false;
        }
        return true;
    }

    function installHook(name, installer) {
        try {
            var installed = installer();
            if (installed === false) {
                logDebug("Hook unavailable: " + name);
                return false;
            }
            hooks.push(name);
            logDebug("Installed hook: " + name);
            return true;
        } catch (hookError) {
            logWarn("Failed to install " + name + ": " + errorText(hookError));
            return false;
        }
    }

    function installDataManagerHook() {
        if (!window.DataManager || typeof window.DataManager.onLoad !== "function") {
            return false;
        }
        return wrapExistingMethod(window.DataManager, "onLoad", function(original) {
            return function(object) {
                var result = original.apply(this, arguments);
                try {
                    translateDataObject(object);
                } catch (translateError) {
                    logWarn("Could not translate loaded data: " + errorText(translateError));
                }
                return result;
            };
        });
    }

    function installAllTextHook() {
        if (!window.Game_Message || !window.Game_Message.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_Message.prototype, "allText", function(original) {
            if (!baseAllText) {
                baseAllText = original;
            }
            return function() {
                var value = original.apply(this, arguments);
                try {
                    return translateBlockInternal(value);
                } catch (blockError) {
                    logDebug("allText translation failed: " + errorText(blockError));
                    return value;
                }
            };
        });
    }

    function installChoicesHook() {
        if (!window.Game_Message || !window.Game_Message.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_Message.prototype, "setChoices", function(original) {
            return function(choices) {
                var args = Array.prototype.slice.call(arguments);
                if (isArray(choices)) {
                    try {
                        var translatedChoices = [];
                        for (var i = 0; i < choices.length; i++) {
                            translatedChoices.push(translateTextInternal(choices[i]));
                        }
                        args[0] = translatedChoices;
                    } catch (choiceError) {
                        args = Array.prototype.slice.call(arguments);
                        logDebug("Choice translation failed: " + errorText(choiceError));
                    }
                }
                return original.apply(this, args);
            };
        });
    }

    function installSpeakerNameHook() {
        if (!window.Game_Message || !window.Game_Message.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_Message.prototype, "setSpeakerName", function(original) {
            return function(name) {
                var args = Array.prototype.slice.call(arguments);
                if (args.length > 0) {
                    try {
                        args[0] = translateNameText(args[0]);
                    } catch (speakerError) {
                        args[0] = name;
                        logDebug("Speaker name translation failed: " + errorText(speakerError));
                    }
                }
                return original.apply(this, args);
            };
        });
    }

    function installNameBoxRefreshHook() {
        if (!window.Window_NameBox || !window.Window_NameBox.prototype) {
            return false;
        }
        // YEP MessageCore (MV) exposes refresh(text, position).  MZ exposes
        // setName(name) instead; translating in refresh() would be too late
        // for MZ width/placement.
        if (typeof window.Window_NameBox.prototype.refresh !== "function" ||
                typeof window.Window_NameBox.prototype.setName === "function") {
            return false;
        }
        return wrapExistingMethod(window.Window_NameBox.prototype, "refresh", function(original) {
            return function(text, position) {
                var args = Array.prototype.slice.call(arguments);
                if (args.length > 0) {
                    try {
                        args[0] = translateNameText(args[0]);
                    } catch (refreshError) {
                        args[0] = text;
                        logDebug("Name box refresh translation failed: " + errorText(refreshError));
                    }
                }
                return original.apply(this, args);
            };
        });
    }

    function installNameBoxSetNameHook() {
        if (!window.Window_NameBox || !window.Window_NameBox.prototype) {
            return false;
        }
        if (typeof window.Window_NameBox.prototype.setName !== "function") {
            return false;
        }
        return wrapExistingMethod(window.Window_NameBox.prototype, "setName", function(original) {
            return function(name) {
                var args = Array.prototype.slice.call(arguments);
                if (args.length > 0) {
                    try {
                        args[0] = translateNameText(args[0]);
                    } catch (setNameError) {
                        args[0] = name;
                        logDebug("Name box setName translation failed: " + errorText(setNameError));
                    }
                }
                return original.apply(this, args);
            };
        });
    }

    function applyDocumentTitle() {
        try {
            var system = window.$dataSystem;
            if (!system || typeof system.gameTitle !== "string" ||
                    typeof document === "undefined" || !document) {
                return;
            }
            var translated = translateTextInternal(system.gameTitle);
            if (translated !== system.gameTitle) {
                document.title = translated;
            }
        } catch (titleError) {
            logDebug("Document title translation failed: " + errorText(titleError));
        }
    }

    function installSceneBootHook() {
        if (!window.Scene_Boot || !window.Scene_Boot.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Scene_Boot.prototype, "updateDocumentTitle", function(original) {
            return function() {
                var result = original.apply(this, arguments);
                applyDocumentTitle();
                return result;
            };
        });
    }

    function installSceneTitleStartHook() {
        if (!window.Scene_Title || !window.Scene_Title.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Scene_Title.prototype, "start", function(original) {
            return function() {
                var result = original.apply(this, arguments);
                applyDocumentTitle();
                return result;
            };
        });
    }

    function installActorNameHook() {
        if (!window.Game_Actor || !window.Game_Actor.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_Actor.prototype, "name", function(original) {
            return function() {
                var value = original.apply(this, arguments);
                if (rawNamesForConditional) {
                    return this._name;
                }
                try {
                    return translateTextInternal(value);
                } catch (nameError) {
                    logDebug("Actor name translation failed: " + errorText(nameError));
                    return value;
                }
            };
        });
    }

    function installActorNicknameHook() {
        if (!window.Game_Actor || !window.Game_Actor.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_Actor.prototype, "nickname", function(original) {
            return function() {
                var value = original.apply(this, arguments);
                if (rawNamesForConditional) {
                    return value;
                }
                try {
                    return translateTextInternal(value);
                } catch (nicknameError) {
                    logDebug("Actor nickname translation failed: " + errorText(nicknameError));
                    return value;
                }
            };
        });
    }

    function installActorProfileHook() {
        if (!window.Game_Actor || !window.Game_Actor.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_Actor.prototype, "profile", function(original) {
            return function() {
                var value = original.apply(this, arguments);
                if (rawNamesForConditional) {
                    return value;
                }
                try {
                    return translateTextInternal(value);
                } catch (profileError) {
                    logDebug("Actor profile translation failed: " + errorText(profileError));
                    return value;
                }
            };
        });
    }

    function installCommand111Hook() {
        if (!window.Game_Interpreter || !window.Game_Interpreter.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_Interpreter.prototype, "command111", function(original) {
            return function() {
                var previousNames = rawNamesForConditional;
                var previousData = rawDataNamesForScript;
                rawNamesForConditional = true;
                rawDataNamesForScript = true;
                try {
                    return original.apply(this, arguments);
                } finally {
                    rawNamesForConditional = previousNames;
                    rawDataNamesForScript = previousData;
                }
            };
        });
    }

    function captureNameEditState(editWindow) {
        if (!editWindow || typeof editWindow !== "object") {
            return;
        }
        var actor = editWindow._actor;
        if (!actor || typeof actor !== "object") {
            return;
        }
        editWindow._rpgmRawName = actor._name;
        editWindow._rpgmDisplayName = editWindow._name;

        // Standard MV/MZ windows provide name(), but tolerate a non-standard
        // window shape as long as it exposes the display string.
        if (typeof editWindow.name !== "function" &&
                typeof editWindow._name === "string") {
            try {
                editWindow.name = function() {
                    return this._name;
                };
            } catch (nameShimError) {
                logDebug("Could not add name edit accessor: " + errorText(nameShimError));
            }
        }
    }

    function installNameEditInitializeHook() {
        if (!window.Window_NameEdit || !window.Window_NameEdit.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_NameEdit.prototype, "initialize", function(original) {
            return function() {
                var result = original.apply(this, arguments);
                try {
                    captureNameEditState(this);
                } catch (captureError) {
                    logDebug("Could not capture name edit state: " + errorText(captureError));
                }
                return result;
            };
        });
    }

    function installNameEditSetupHook() {
        if (!window.Window_NameEdit || !window.Window_NameEdit.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_NameEdit.prototype, "setup", function(original) {
            return function() {
                var result = original.apply(this, arguments);
                try {
                    captureNameEditState(this);
                } catch (captureError) {
                    logDebug("Could not capture name edit state: " + errorText(captureError));
                }
                return result;
            };
        });
    }

    function installSceneNameInputOkHook() {
        if (!window.Scene_Name || !window.Scene_Name.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Scene_Name.prototype, "onInputOk", function(original) {
            return function() {
                var editWindow = null;
                var actor = null;
                var unchanged = false;

                try {
                    editWindow = this._editWindow;
                    actor = this._actor;
                    unchanged = !!(editWindow && actor &&
                        typeof editWindow._rpgmRawName === "string" &&
                        typeof editWindow._rpgmDisplayName === "string" &&
                        typeof editWindow.name === "function" &&
                        editWindow.name() === editWindow._rpgmDisplayName);
                } catch (guardError) {
                    unchanged = false;
                    logDebug("Name input guard failed: " + errorText(guardError));
                }

                try {
                    // The original stores editWindow.name() in the actor and
                    // pops the scene. Correct the raw value only afterwards.
                    return original.apply(this, arguments);
                } finally {
                    if (unchanged && editWindow && actor &&
                            typeof actor.setName === "function") {
                        try {
                            actor.setName(editWindow._rpgmRawName);
                        } catch (restoreError) {
                            logWarn("Could not restore raw actor name: " + errorText(restoreError));
                        }
                    }
                }
            };
        });
    }

    function addFontFallback(originalFace) {
        if (!fontFace) {
            return originalFace;
        }
        if (typeof originalFace !== "string" || originalFace === "") {
            return fontFace;
        }

        var result = [];
        var seen = createMap();
        var addFamily = function(value) {
            var family = String(value).replace(/^\s+|\s+$/g, "");
            family = family.replace(/^["']|["']$/g, "");
            if (!family) {
                return;
            }
            var key = family.toLowerCase();
            if (hasOwn(seen, key)) {
                return;
            }
            setMapValue(seen, key, true);
            result.push(family);
        };

        var configured = fontFace.split(",");
        var original = originalFace.split(",");
        for (var i = 0; i < configured.length; i++) {
            addFamily(configured[i]);
        }
        for (var j = 0; j < original.length; j++) {
            addFamily(original[j]);
        }
        return result.length > 0 ? result.join(", ") : originalFace;
    }

    function installWindowFontHook() {
        if (!window.Window_Base || !window.Window_Base.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_Base.prototype, "standardFontFace", function(original) {
            return function() {
                var value = original.apply(this, arguments);
                try {
                    return addFontFallback(value);
                } catch (fontError) {
                    logDebug("Window font override failed: " + errorText(fontError));
                    return value;
                }
            };
        });
    }

    function installGameSystemFontHook() {
        if (!window.Game_System || !window.Game_System.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Game_System.prototype, "mainFontFace", function(original) {
            return function() {
                var value = original.apply(this, arguments);
                try {
                    return addFontFallback(value);
                } catch (fontError) {
                    logDebug("Game_System font override failed: " + errorText(fontError));
                    return value;
                }
            };
        });
    }

    function installBitmapUiLabelHook() {
        if (!window.Bitmap || typeof window.Bitmap.prototype.drawText !== "function") {
            return false;
        }
        return wrapExistingMethod(window.Bitmap.prototype, "drawText", function(original) {
            return function(text) {
                var args = Array.prototype.slice.call(arguments);
                if (typeof args[0] === "string") {
                    args[0] = translateUiLabel(args[0]);
                }
                return original.apply(this, args);
            };
        });
    }

    function installWindowBaseUiLabelHooks() {
        if (!window.Window_Base || !window.Window_Base.prototype) {
            return false;
        }
        var installed = false;
        if (typeof window.Window_Base.prototype.drawText === "function") {
            installed = wrapExistingMethod(window.Window_Base.prototype, "drawText", function(original) {
                return function(text) {
                    var args = Array.prototype.slice.call(arguments);
                    if (typeof args[0] === "string") {
                        args[0] = translateUiLabel(args[0]);
                    }
                    return original.apply(this, args);
                };
            }) || installed;
        }
        if (typeof window.Window_Base.prototype.drawTextEx === "function") {
            installed = wrapExistingMethod(window.Window_Base.prototype, "drawTextEx", function(original) {
                return function(text) {
                    var args = Array.prototype.slice.call(arguments);
                    if (typeof args[0] === "string") {
                        args[0] = translateUiLabel(args[0]);
                    }
                    return original.apply(this, args);
                };
            }) || installed;
        }
        if (typeof window.Window_Base.prototype.drawFormatTextEx === "function") {
            installed = wrapExistingMethod(window.Window_Base.prototype, "drawFormatTextEx", function(original) {
                return function(format) {
                    var args = Array.prototype.slice.call(arguments);
                    if (typeof args[0] === "string") {
                        args[0] = translateUiLabel(args[0]);
                    }
                    return original.apply(this, args);
                };
            }) || installed;
        }
        return installed;
    }

    function addTitleOverlay(scene) {
        if (!titleOverlayEnabled || !scene || !window.Bitmap || !window.Sprite) {
            return;
        }
        var system = window.$dataSystem;
        if (!system || system.optDrawTitle) {
            return;
        }
        var rawTitle = system.gameTitle;
        if (typeof rawTitle !== "string") {
            return;
        }
        var text = translateTextInternal(rawTitle);
        if (text === rawTitle) {
            return;
        }
        var width = window.Graphics && isFiniteNumber(window.Graphics.boxWidth) ?
            window.Graphics.boxWidth : 816;
        var height = window.Graphics && isFiniteNumber(window.Graphics.boxHeight) ?
            window.Graphics.boxHeight : 624;
        if (scene._rpgmTitleOverlay && typeof scene.removeChild === "function") {
            try {
                scene.removeChild(scene._rpgmTitleOverlay);
            } catch (removeError) {
                // Ignore stale overlay removal failures.
            }
        }
        var fontSize = titleOverlayFontSize;
        var bitmap = new window.Bitmap(width, height);
        bitmap.fontSize = fontSize;
        if (fontFace) {
            bitmap.fontFace = addFontFallback(bitmap.fontFace);
        }
        bitmap.textColor = "#ffffff";
        bitmap.outlineColor = "rgba(0, 0, 0, 0.85)";
        bitmap.outlineWidth = 4;
        var textWidth = bitmap.measureTextWidth(text);
        while (textWidth > width - 32 && fontSize > 12) {
            fontSize -= 2;
            bitmap.fontSize = fontSize;
            textWidth = bitmap.measureTextWidth(text);
        }
        var x = titleOverlayX ? titleOverlayX : Math.floor((width - textWidth) / 2);
        x = Math.max(16, Math.min(width - textWidth - 16, x));
        var y = titleOverlayY ? titleOverlayY : Math.floor(height * 0.72);
        if (titleOverlayBackground > 0) {
            bitmap.paintOpacity = titleOverlayBackground;
            bitmap.fillRect(x - 8, y - 6, textWidth + 16, fontSize + 14, "#000000");
            bitmap.paintOpacity = 255;
        }
        bitmap.drawText(text, x, y, textWidth + 8, fontSize + 8, "left");
        var sprite = new window.Sprite(bitmap);
        sprite.z = 100;
        scene.addChild(sprite);
        scene._rpgmTitleOverlay = sprite;
    }

    function installTitleOverlayHook() {
        if (!titleOverlayEnabled || !window.Scene_Title ||
                typeof window.Scene_Title.prototype.createForeground !== "function") {
            return false;
        }
        return wrapExistingMethod(window.Scene_Title.prototype, "createForeground", function(original) {
            return function() {
                var result = original.apply(this, arguments);
                try {
                    addTitleOverlay(this);
                } catch (overlayError) {
                    logDebug("Title overlay failed: " + errorText(overlayError));
                }
                return result;
            };
        });
    }

    function installCommandDrawItemHook() {
        if (!window.Window_Command || !window.Window_Command.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_Command.prototype, "drawItem", function(original) {
            return function(index) {
                var item = this._list && this._list[index];
                var changed = null;
                if (item && typeof item.name === "string") {
                    var translated = translateTextInternal(item.name);
                    if (translated !== item.name) {
                        var oldName = item.name;
                        try {
                            item.name = translated;
                            changed = { item: item, oldName: oldName };
                        } catch (assignError) {
                            changed = null;
                        }
                    }
                }
                try {
                    return original.apply(this, arguments);
                } finally {
                    if (changed) {
                        try {
                            changed.item.name = changed.oldName;
                        } catch (restoreError) {
                            logDebug("Could not restore command label: " + errorText(restoreError));
                        }
                    }
                }
            };
        });
    }

    function installTickerManagerHook() {
        if (!window.TickerManager ||
                typeof window.TickerManager.show !== "function") {
            return false;
        }
        return wrapExistingMethod(window.TickerManager, "show", function(original) {
            return function(text, needPlus) {
                var args = Array.prototype.slice.call(arguments);
                if (typeof args[0] === "string") {
                    try {
                        args[0] = translateTextInternal(args[0]);
                    } catch (tickerError) {
                        args[0] = text;
                        logDebug("TickerManager translation failed: " + errorText(tickerError));
                    }
                }
                return original.apply(this, args);
            };
        });
    }

    function installTickerHook() {
        if (!window.Window_Ticker ||
                typeof window.Window_Ticker.prototype.drawTextEx !== "function") {
            return false;
        }
        return wrapExistingMethod(window.Window_Ticker.prototype, "drawTextEx", function(original) {
            return function(text, x, y) {
                var args = Array.prototype.slice.call(arguments);
                if (typeof args[0] === "string") {
                    try {
                        args[0] = translateTextInternal(args[0]);
                    } catch (tickerError) {
                        args[0] = text;
                        logDebug("Ticker translation failed: " + errorText(tickerError));
                    }
                }
                return original.apply(this, args);
            };
        });
    }

    function installFtkDescTitleHook() {
        if (!window.Window_Base ||
                typeof window.Window_Base.prototype.drawStsDescTitle !== "function") {
            return false;
        }
        return wrapExistingMethod(window.Window_Base.prototype, "drawStsDescTitle", function(original) {
            return function() {
                var actor = this._actor;
                var oldName = null;
                try {
                    if (actor && typeof actor._name === "string") {
                        var translated = translateNameText(actor._name);
                        if (translated !== actor._name) {
                            oldName = actor._name;
                            actor._name = translated;
                        }
                    }
                } catch (nameError) {
                    logDebug("FTKR title name translation failed: " + errorText(nameError));
                }
                try {
                    return original.apply(this, arguments);
                } finally {
                    if (oldName !== null && actor) {
                        try {
                            actor._name = oldName;
                        } catch (restoreError) {
                            logDebug("Could not restore FTKR actor name: " + errorText(restoreError));
                        }
                    }
                }
            };
        });
    }

    function installSaveInfoTitleHook(className, methodName) {
        var saveClass = window[className];
        if (!saveClass || !saveClass.prototype ||
                typeof saveClass.prototype[methodName] !== "function") {
            return false;
        }
        return wrapExistingMethod(saveClass.prototype, methodName, function(original) {
            return function() {
                var args = Array.prototype.slice.call(arguments);
                var changed = null;
                try {
                    var info = args[0];
                    if (info && typeof info.title === "string") {
                        var translated = translateTextInternal(info.title);
                        if (translated !== info.title) {
                            changed = { info: info, oldTitle: info.title };
                            info.title = translated;
                        }
                    }
                } catch (titleError) {
                    logDebug("Save title translation failed: " + errorText(titleError));
                }
                try {
                    return original.apply(this, args);
                } finally {
                    if (changed) {
                        try {
                            changed.info.title = changed.oldTitle;
                        } catch (restoreError) {
                            logDebug("Could not restore save info title: " + errorText(restoreError));
                        }
                    }
                }
            };
        });
    }

    function installYepSaveTitleHook() {
        if (!window.Window_SaveInfo || !window.Window_SaveInfo.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_SaveInfo.prototype, "drawGameTitle", function(original) {
            return function(dy) {
                var changed = null;
                try {
                    if (this._info && typeof this._info.title === "string") {
                        var translated = translateTextInternal(this._info.title);
                        if (translated !== this._info.title) {
                            changed = { info: this._info, oldTitle: this._info.title };
                            this._info.title = translated;
                        }
                    }
                } catch (titleError) {
                    logDebug("YEP save title translation failed: " + errorText(titleError));
                }
                try {
                    return original.apply(this, arguments);
                } finally {
                    if (changed) {
                        try {
                            changed.info.title = changed.oldTitle;
                        } catch (restoreError) {
                            logDebug("Could not restore YEP save title: " + errorText(restoreError));
                        }
                    }
                }
            };
        });
    }

    function installYepSavePartyNamesHook() {
        if (!window.Window_SaveInfo || !window.Window_SaveInfo.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_SaveInfo.prototype, "drawPartyNames", function(original) {
            return function(dy) {
                var changes = [];
                try {
                    var saveContents = this._saveContents;
                    var actors = saveContents && saveContents.actors &&
                        saveContents.actors._data;
                    if (actors) {
                        for (var id in actors) {
                            if (!hasOwn(actors, id)) {
                                continue;
                            }
                            var member = actors[id];
                            if (member && typeof member._name === "string") {
                                var translated = translateNameText(member._name);
                                if (translated !== member._name) {
                                    changes.push([member, member._name]);
                                    member._name = translated;
                                }
                            }
                        }
                    }
                } catch (nameError) {
                    logDebug("YEP save party-name translation failed: " + errorText(nameError));
                }
                try {
                    return original.apply(this, arguments);
                } finally {
                    for (var i = 0; i < changes.length; i++) {
                        try {
                            changes[i][0]._name = changes[i][1];
                        } catch (restoreError) {
                            logDebug("Could not restore save party name: " + errorText(restoreError));
                        }
                    }
                }
            };
        });
    }

    function installSceneTitleHook() {
        if (!window.Scene_Title || !window.Scene_Title.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Scene_Title.prototype, "drawGameTitle", function(original) {
            return function() {
                var system = window.$dataSystem;
                var originalTitle = null;
                var titleChanged = false;

                try {
                    if (fontFace && this._gameTitleSprite && this._gameTitleSprite.bitmap) {
                        this._gameTitleSprite.bitmap.fontFace =
                            addFontFallback(this._gameTitleSprite.bitmap.fontFace);
                    }

                    if (system && typeof system.gameTitle === "string") {
                        originalTitle = system.gameTitle;
                        var translatedTitle = translateTextInternal(originalTitle);
                        if (translatedTitle !== originalTitle) {
                            system.gameTitle = translatedTitle;
                            titleChanged = true;
                        }
                    }
                } catch (titlePrepareError) {
                    logDebug("Title preparation failed: " + errorText(titlePrepareError));
                    if (titleChanged) {
                        try {
                            system.gameTitle = originalTitle;
                        } catch (titleRestoreError) {
                            logWarn("Could not restore game title: " + errorText(titleRestoreError));
                        }
                        titleChanged = false;
                    }
                }

                try {
                    return original.apply(this, arguments);
                } finally {
                    if (titleChanged) {
                        try {
                            system.gameTitle = originalTitle;
                        } catch (titleRestoreError2) {
                            logWarn("Could not restore game title: " + errorText(titleRestoreError2));
                        }
                    }
                }
            };
        });
    }

    function installWindowMessageWrapHook() {
        if (!window.Window_Message || !window.Window_Message.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_Message.prototype, "startMessage", function(original) {
            return wrapStartMessage(original);
        });
    }

    function installWindowScrollTextWrapHook() {
        if (!window.Window_ScrollText || !window.Window_ScrollText.prototype) {
            return false;
        }
        return wrapExistingMethod(window.Window_ScrollText.prototype, "startMessage", function(original) {
            return wrapStartMessage(original);
        });
    }

    function installHooks() {
        installHook("DataManager.onLoad", installDataManagerHook);
        installHook("Game_Message.allText", installAllTextHook);
        installHook("Game_Message.setChoices", installChoicesHook);
        installHook("Game_Message.setSpeakerName", installSpeakerNameHook);
        installHook("Window_NameBox.refresh", installNameBoxRefreshHook);
        installHook("Window_NameBox.setName", installNameBoxSetNameHook);
        installHook("Window_Command.drawItem", installCommandDrawItemHook);
        installHook("Bitmap.drawText", installBitmapUiLabelHook);
        installHook("Window_Base.uiLabels", installWindowBaseUiLabelHooks);
        installHook("Window_Ticker.drawTextEx", installTickerHook);
        installHook("TickerManager.show", installTickerManagerHook);
        installHook("Window_Base.drawStsDescTitle", installFtkDescTitleHook);
        installHook("Game_Actor.name", installActorNameHook);
        installHook("Game_Actor.nickname", installActorNicknameHook);
        installHook("Game_Actor.profile", installActorProfileHook);
        installHook("Game_Interpreter.command111", installCommand111Hook);
        installHook("Window_NameEdit.initialize", installNameEditInitializeHook);
        installHook("Window_NameEdit.setup", installNameEditSetupHook);
        installHook("Scene_Name.onInputOk", installSceneNameInputOkHook);
        installHook("Scene_Boot.updateDocumentTitle", installSceneBootHook);
        installHook("Scene_Title.start", installSceneTitleStartHook);
        installHook("Scene_Title.drawGameTitle", installSceneTitleHook);
        installHook("Scene_Title.createForeground", installTitleOverlayHook);
        installHook("Window_SavefileStatus.drawContents", function() {
            return installSaveInfoTitleHook("Window_SavefileStatus", "drawContents");
        });
        installHook("Window_SavefileList.drawContents", function() {
            return installSaveInfoTitleHook("Window_SavefileList", "drawContents");
        });
        installHook("Window_SaveFileList.drawFileInfo", function() {
            return installSaveInfoTitleHook("Window_SaveFileList", "drawFileInfo");
        });
        installHook("Window_SaveInfo.drawGameTitle", installYepSaveTitleHook);
        installHook("Window_SaveInfo.drawPartyNames", installYepSavePartyNamesHook);

        if (fontFace) {
            installHook("Window_Base.standardFontFace", installWindowFontHook);
            installHook("Game_System.mainFontFace", installGameSystemFontHook);
        }

        if (wrapEnabled) {
            installHook("Window_Message.startMessage", installWindowMessageWrapHook);
            installHook("Window_ScrollText.startMessage", installWindowScrollTextWrapHook);
        }
    }

    // ---------------------------------------------------------------------
    // Debug API
    // ---------------------------------------------------------------------

    function exposeDebugApi() {
        try {
            var api = {
                enabled: true,
                entryCount: entryCount,
                translateText: function(text) {
                    try {
                        return translateTextInternal(text);
                    } catch (error) {
                        logDebug("Debug translateText failed: " + errorText(error));
                        return text;
                    }
                },
                translateBlock: function(text) {
                    try {
                        return translateBlockInternal(text);
                    } catch (error) {
                        logDebug("Debug translateBlock failed: " + errorText(error));
                        return text;
                    }
                },
                translateName: function(text) {
                    try {
                        return translateNameText(text);
                    } catch (error) {
                        logDebug("Debug translateName failed: " + errorText(error));
                        return text;
                    }
                },
                wrapText: function(text, maxWidthOrWindow, measureFn) {
                    try {
                        // Debug callers may pass either a pixel width plus a
                        // measure function, or a window-like object.
                        if (maxWidthOrWindow && typeof maxWidthOrWindow === "object") {
                            var maxWidth = getMessageMaxWidth(maxWidthOrWindow);
                            if (!(maxWidth > 0)) {
                                return text;
                            }
                            return wrapTextForWindow(maxWidthOrWindow, text, maxWidth);
                        }
                        return wrapTextInternal(text, maxWidthOrWindow, measureFn);
                    } catch (error) {
                        logDebug("Debug wrapText failed: " + errorText(error));
                        return text;
                    }
                },
                getStats: function() {
                    try {
                        return {
                            enabled: true,
                            entryCount: entryCount,
                            hits: hitCount,
                            misses: missCount,
                            blockHits: blockHitCount,
                            lineHits: lineHitCount,
                            pageHits: pageHitCount,
                            speakerNames: speakerNameCount,
                            uiLabels: uiLabelCount,
                            titleOverlay: titleOverlayEnabled,
                            wrapEnabled: wrapEnabled,
                            fontFace: fontFace,
                            debug: debugEnabled,
                            hooks: hooks.slice(0)
                        };
                    } catch (statsError) {
                        return {
                            enabled: true,
                            entryCount: entryCount
                        };
                    }
                }
            };
            window.RPGMLauncherTranslate = api;
        } catch (exposeError) {
            logDebug("Could not expose RPGMLauncherTranslate debug API: " + errorText(exposeError));
        }
    }

    // ---------------------------------------------------------------------
    // Initialization
    // ---------------------------------------------------------------------

    try {
        if (window.RPGMLauncherTranslate &&
                typeof window.RPGMLauncherTranslate.getStats === "function") {
            logDebug("Translation plugin already installed; skipping duplicate load.");
            return;
        }
    } catch (existingApiError) {
        // Continue with initialization.
    }

    try {
        config = readConfig();
        if (!config) {
            return;
        }

        debugEnabled = config.debug;
        wrapEnabled = config.wrap;
        fontFace = config.fontFace;
        titleOverlayEnabled = config.titleOverlay;
        titleOverlayX = config.titleOverlayX;
        titleOverlayY = config.titleOverlayY;
        titleOverlayFontSize = config.titleOverlayFontSize;
        titleOverlayBackground = config.titleOverlayBackground;
        configuredUiLabels = config.uiLabels || [];

        if (!loadDictionary()) {
            return;
        }
        buildUiLabelSet();

        installHooks();
        exposeDebugApi();
        logDebug("Runtime translation active (entries=" + entryCount +
            ", hooks=" + hooks.length + ").");
    } catch (initError) {
        logWarn("Translation initialization failed: " + errorText(initError));
    }
})();
