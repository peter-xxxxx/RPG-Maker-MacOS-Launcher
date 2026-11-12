# RPG Maker MV/MZ Translation Support — Implementation Plan

> **Follow-up:** `translation-followup-plan-v2.md` supersedes the export filename/format parts of this document. The v1 runtime design below remains the base; the follow-up covers `ManualTransFile.json`, cleaned line/fragment export, speaker-name coverage, and title handling.

**Status:** Draft for review (revision 7)  
**Target:** `RPG-Maker-MacOS-Launcher` (MacOS, NW.js MV/MZ games)  
**Scope:** Standard RPG Maker MV/MZ text only. No script/plugin-command deep scan.

**Locked decisions from product review**

1. A game restart after loading a translation is acceptable for v1.
2. Typical language pairs are Japanese → Chinese, English → Chinese, and Japanese → English.
3. V1 translates standard database, system, map, and common-event text only.
4. No game `data/*.json` file is permanently modified.

---

## 1. Summary

Add a translation workflow to the launcher:

1. User selects an MV/MZ game.
2. Launcher exports all standard translatable text to a flat JSON dictionary.
3. User translates the values with any external editor/tool.
4. Launcher loads and validates the translated JSON.
5. On the next game launch, the launcher installs a small runtime JS plugin and dictionary into the game.
6. The plugin translates display strings while the game runs, without rewriting the game's event data.

This is the macOS-friendly equivalent of MTool's runtime translation. It preserves the original game files and save compatibility.

**Important design change from revision 1:** event command lists, actor names, and `$dataSystem.gameTitle` are **not** rewritten in memory. Dialogue is translated when the message window reads it; choices/speaker names are translated at the `Game_Message` boundary; actor names are translated through display getters while the raw save data stays untouched. Standard Name Input is wrapped so confirming an unchanged translated display does not write the translation back into raw actor state. This avoids breaking event conditionals and save serialization.

---

## 2. Goals and non-goals

### Goals

- Export raw text as MTool-compatible flat JSON: `{"source text": "translated text"}`.
- Load user-supplied translated JSON, including files produced by MTool or similar tools.
- Translate standard MV/MZ database, system, map, and common-event text.
- Handle MZ speaker names, choices, scrolling text, and common text codes.
- Validate translations and warn about missing control codes/placeholders.
- Keep original `data/*.json` files untouched.
- Keep actor names and event conditionals working.
- Support per-game enabled/disabled state.
- Store per-game dictionary/config/state inside the game folder so translations travel with the game.
- Include the active translation in exported standalone apps.
- Keep Cheat Menu and protection patches working.

### Non-goals for v1

- Translating JavaScript/script text (`355` / `655`).
- Translating plugin command text (`356` / `357`).
- Translating plugin parameters, note tags, or metadata.
- Translating images, audio, or video assets.
- Live hot-reload into an already-running game.
- Multiple simultaneous translations/languages per game.
- Decrypting packed or non-standard game data.
- Full font/layout parity with MTool. Basic wrapping and an optional font override are included, but pixel-perfect layout is not guaranteed.
- Translating plugin UI strings that are not backed by standard game data. The v1 fallback is intentionally conservative.

---

## 3. Current launcher integration points

Relevant existing behavior in `main.py`:

- MV/MZ games are launched through NW.js by passing the game folder to `launch_nwjs_game()`.
- `start_game()` performs `chown`/`chmod`, optional `game_en.exe` extraction, optional Cheat Menu installation, protection scripts, and the `getPixel` patch before launching.
- Cheat Menu support patches `js/main.js`:
  - MV: inserts `PluginManager.loadScript('Cheat_Menu.js')` after `PluginManager.setup($plugins);`.
  - MZ: inserts a Cheat_Menu URL into the `scriptUrls` array.
- `export_standalone_app()` copies the game folder into an exported `.app`, so installed translation files would be included.
- Settings are stored in `~/Library/Application Support/RPGM-Launcher/settings.json`.

**Existing-code issue:** `modify_MV_main_js()` returns `False` when the Cheat Menu lines are already present, and `modify_MZ_main_js()` treats the Cheat Menu URL as the only patch. The translation feature therefore has its own injection path and does **not** modify `main.js` in v1. Cheat Menu and translation must be independently addable/removable.

---

## 4. Architecture

```text
Launcher (Python)                              Game (NW.js)
-----------------                              -------------
scan data/*.json  ──►  raw_text.json
                       │ user translates values
                       ▼
load + validate  ──►  write dictionary/config/state
                       into <js>/plugins/ (inside game folder)
                       │
                       ▼
on game launch:
  ensure plugin JS + dictionary
  ensure plugin entry in plugins.js
                                             │
                                             ▼
                                   translator plugin loads at boot
                                   Game_Message.allText → dialogue
                                   Game_Message choices/speaker → choices/names
                                   Game_Actor getters → actor names
                                   DataManager.onLoad → DB/system/map display fields
                                   Scene_Title → game title
```

### Components

| Component | Location | Purpose |
|---|---|---|
| `translation.py` | Repo root | Engine detection, scanning, export, validation, install/remove helpers |
| `RPGM_Launcher_Translate.js` | Repo root, copied to game `js/plugins/` | Runtime dictionary loader and display-time translation |
| `RPGM_Launcher_Translate.json` | Game `js/plugins/` | Active translation dictionary |
| `RPGM_Launcher_Translate.config.json` | Game `js/plugins/` | Runtime settings: enabled, font face, wrap toggle, debug |
| `RPGM_Launcher_Translate.state.json` | Game `js/plugins/` | Per-game launcher metadata: source path, counts, timestamp |
| Translation dialog | `main.py` or `translation_dialog.py` | Export/load/status UI |

All per-game translation state lives inside the game folder so it travels with the game. The launcher keeps only a small path-only cleanup index for Uninstall All; it contains no translation text or per-game status.

No new Python package dependencies are required. Standard library plus the existing PySide6/chardet stack is enough.

---

## 5. Translation dictionary format

### 5.1 File shape

Flat JSON object. Keys are original text; values are translated text.

```json
{
  "ぎんたま": "金球",
  "こんにちは": "你好",
  "こんにちは\n今日はいい天気ですね": "你好。\n今天天气真好。"
}
```

Rules:

- Keys are matched exactly against the game's source strings after normalization.
- Empty string values are treated as untranslated and ignored by the plugin.
- The launcher exports empty values initially.
- MTool-compatible flat dictionaries are accepted.
- Non-string values are rejected or skipped with a warning.
- Existing metadata wrappers are not required. V1 expects a flat object.
- UTF-8 output, no BOM. Input may have a BOM.
- Normalize CRLF to LF and Unicode to NFC both at export and at runtime lookup.

### 5.2 Message grouping

For event commands `101`/`401` and `105`/`405`, consecutive text lines are exported as one block joined with `\n`:

```json
{
  "第一行\n第二行": "First line\nSecond line"
}
```

This gives translators complete dialogue instead of isolated fragments.

At runtime, `Game_Message.prototype.allText()` joins the raw `_texts` with `\n`. The plugin looks up the full block first:

1. If a full-block key exists, return its translation.
2. Otherwise, translate each line individually and rejoin with `\n`.

This means:

- Full-block keys are preferred.
- MTool line-key dictionaries still work.
- The event command list is never modified, so saves never contain rewritten commands.
- The original lines remain available for context and fallback.

### 5.3 Control codes and placeholders

The validator must be engine-aware and parse escaped backslashes first.

Common codes: `\C[n]`, `\I[n]`, `\N[n]`, `\V[n]`, `\P[n]`, `\G`, `\!`, `\.`, `\|`, `\^`, `\<`, `\>`, `\$`, `\\`  
MV additional: `\S[n]` (text speed)  
MZ additional: `\FS[n]`, `\PX[n]`, `\PY[n]`

`\f` (form feed / page break) and actual newlines are layout characters, not code tokens; do not require the same count in the translation.

`%1`, `%2`, `%3` placeholders appear not only in `System.json` messages but also in `Skills.message1/2` and `States.message1–4`. Validate them in every exported message field.

Validation compares source and translation token multisets and reports:

- missing control codes
- extra control codes
- missing `%n` placeholders
- extra `%n` placeholders

Warnings do not block installation, but they are shown in the load summary. The launcher does not silently auto-fix translations.

Do not list or validate `\_`; it is not a standard MV/MZ code.

---

## 6. Export specification

### 6.1 Engine and data-folder detection

Detect the engine by core JS file:

- MV: `rpg_core.js`
- MZ: `rmmz_core.js`

Search order:

1. `<game>/www/js/...` (MV usually, possible MZ repack)
2. `<game>/js/...` (MZ usually)

If both exist, prefer the folder containing `package.json`'s `main` file, then prefer `www`.

The detected `js` directory is authoritative for the whole feature:

- `plugins.js` location
- `main.js` location (Cheat Menu only)
- plugin JS output
- dictionary/config output

Data folder is the sibling of the detected `js` directory:

- `<game>/www/data`
- `<game>/data`

If no core JS or data folder is found, show an unsupported-game message. Do not attempt to guess or decrypt.

### 6.2 Extraction scope

#### Database files

| File | Fields | Runtime translation |
|---|---|---|
| `Actors.json` | `name`, `nickname`, `profile` | `Game_Actor` display getters; raw values never changed |
| `Classes.json` | `name` | `$dataClasses` after load |
| `Skills.json` | `name`, `description`, `message1`, `message2` | `$dataSkills` after load |
| `Items.json` | `name`, `description` | `$dataItems` after load |
| `Weapons.json` | `name`, `description` | `$dataWeapons` after load |
| `Armors.json` | `name`, `description` | `$dataArmors` after load |
| `Enemies.json` | `name` | `$dataEnemies` after load |
| `States.json` | `name`, `message1`–`message4` | `$dataStates` after load |
| `Troops.json` | event command text | `Game_Message.allText()` at runtime |
| `CommonEvents.json` | event command text | `Game_Message.allText()` at runtime |
| `MapInfos.json` | `name` | `$dataMapInfos` after load |
| `Map###.json` | `displayName`, event command text | `displayName` after load; dialogue at runtime |
| `System.json` | see below | `$dataSystem` after load, except `gameTitle` |

Do not export Troops/CommonEvents `name` fields; they are editor-only for standard games and are not displayed.

#### System fields

- `gameTitle` (displayed on the title screen only; raw value retained for save compatibility)
- `currencyUnit`
- `elements`
- `skillTypes`
- `weaponTypes`
- `armorTypes`
- `equipTypes`
- `terms.basic`
- `terms.commands`
- `terms.params`
- `terms.messages` values

Skip `null` and non-string entries in these arrays/objects.

Do **not** export:

- `note` / metadata
- `meta`
- `switches` / `variables` names
- animation/tileset names
- audio/image file names
- plugin parameters
- `355`/`655` scripts
- `356`/`357` plugin commands
- `108`/`408` comments

#### Event command rules

| Code | Handling |
|---|---|
| `101` | MZ only: export `parameters[4]` speaker name if present and non-empty. Never touch face image fields. Runtime translates via `Game_Message.setSpeakerName`. |
| `401` | Group consecutive lines, join with `\n`, export as one block. Runtime translates via `Game_Message.allText`. |
| `102` | Export each string in `parameters[0]` choices array. Runtime translates via `Game_Message.setChoices`. |
| `402` | Not exported. It is an editor-only choice label; the game uses the choice index. |
| `105` | No text itself; following `405` lines are handled as a group. |
| `405` | Group consecutive scrolling-text lines, join with `\n`, export as one block. Runtime via `Game_Message.allText`. |
| `320` | Export `parameters[1]` name. Raw value remains in game state; display translated by `Game_Actor.name`. |
| `324` | Export `parameters[1]` nickname. Raw value retained; display translated by `Game_Actor.nickname`. |
| `325` | Export `parameters[1]` profile. Raw value retained; display translated by `Game_Actor.profile`. |
| All others | Ignore. |

### 6.3 Extraction algorithm

1. Parse all target JSON files with `utf-8-sig`, falling back to `chardet` if needed.
2. Walk the known structures only; do not recursively translate arbitrary strings.
3. For event lists, apply the command rules above.
4. Collect **both** full-block keys and individual line keys for validation.
   - Export only full-block keys to keep the raw file clean.
   - Keep a `line_keys` set in `ScanResult` so MTool line-key dictionaries can be validated correctly.
5. Normalize CRLF and Unicode NFC for matching.
6. Discard empty/whitespace-only strings.
7. Write a flat JSON object with empty values.

### 6.4 Output

- Suggested filename: `<sanitized game title>_raw_text.json`.
- UTF-8, no BOM, 2-space indent, `ensure_ascii=False`.
- Include no metadata header, to stay MTool-compatible.
- Show a summary with unique string count and output path.

---

## 7. Runtime plugin specification

### 7.1 Installation layout

Per-game translation artifacts are stored in the game folder's detected plugin directory:

```text
<detected js dir>/plugins/RPGM_Launcher_Translate.js
<detected js dir>/plugins/RPGM_Launcher_Translate.json
<detected js dir>/plugins/RPGM_Launcher_Translate.config.json
<detected js dir>/plugins/RPGM_Launcher_Translate.state.json
```

For a standard MV game with `www`, these become:

```text
<game>/www/js/plugins/RPGM_Launcher_Translate.js
<game>/www/js/plugins/RPGM_Launcher_Translate.json
<game>/www/js/plugins/RPGM_Launcher_Translate.config.json
<game>/www/js/plugins/RPGM_Launcher_Translate.state.json
```

For a standard MZ game:

```text
<game>/js/plugins/RPGM_Launcher_Translate.js
<game>/js/plugins/RPGM_Launcher_Translate.json
<game>/js/plugins/RPGM_Launcher_Translate.config.json
<game>/js/plugins/RPGM_Launcher_Translate.state.json
```

The dictionary, config, and state are written when the user loads a translation, not only at launch. The launcher ensures the files and loader entry are present/correct at every launch.

### 7.2 Loader injection

**Only approach in v1:** register a marked entry at the end of `$plugins` in the detected `plugins.js`:

```js
{"name":"RPGM_Launcher_Translate","status":true,"description":"RPGM Launcher runtime translation injector","parameters":{}}
```

Why:

- Standard RPG Maker plugin loading.
- Loads after game plugins, so the translator's `DataManager.onLoad` wrapper is installed last.
- Works the same on MV and MZ.
- Does not conflict with the existing Cheat Menu `main.js` patches.
- No duplicate-loader paths.

Implementation requirements:

- Use a bracket-aware scanner that ignores normal strings and comments to find the `$plugins` array end.
- Support template literals or reject them: the RPG Maker-generated `plugins.js` is a simple array of object literals. If the file contains template literals or regex literals in the `$plugins` assignment, reject it rather than risk finding the wrong array end.
- Remove any existing entry with the same plugin name before inserting, to keep it idempotent.
- Write to a temporary file, validate the result by re-reading it (balanced brackets, entry exists exactly once), then atomically replace `plugins.js`.
- Preserve the file's original encoding/BOM and newline style where possible.
- Back up the current `plugins.js` to `js/plugins.js.rpgm-launcher.bak` only on the first install, when no translator entry exists. Do not overwrite this clean backup during entry updates or removal. If a clean backup already exists, preserve it; if a timestamped backup is preferred, use a unique name rather than clobbering the existing one.
- If `plugins.js` cannot be parsed safely, **do not guess and do not fall back to `main.js` in v1**. Report the game as unsupported for translation.

### 7.3 Dictionary and config loading

- Locate the dictionary relative to the game's `index.html`:
  - `js/plugins/RPGM_Launcher_Translate.json`
  - `js/plugins/RPGM_Launcher_Translate.config.json`
- Resolve URL path to a filesystem path with `decodeURIComponent()` inside `try/catch` before `fs` operations. This is required for game paths containing spaces or CJK characters.
- Prefer Node `fs.readFileSync`; fall back to synchronous `XMLHttpRequest` if `require`/`fs` is unavailable.
- If the dictionary is missing or invalid, log a warning and leave the game untouched.
- Store entries in an object with a null prototype or use `Object.prototype.hasOwnProperty.call()`; skip empty translation values.
- Normalize dictionary keys/values and runtime source strings with the same `NFC` + LF normalizer.
- If `String.prototype.normalize` is unavailable on an old NW.js build, still normalize CRLF to LF, but document that NFD source/dictionary keys may not match until a newer NW.js runtime is used.
- Do not use `eval` or execute translation content.

Config schema:

```json
{
  "enabled": true,
  "fontFace": "",
  "wrap": true,
  "debug": false
}
```

The launcher writes this config and the separate state file into the game's plugin directory. The config is not part of the translation dictionary, so the dictionary remains flat/MTool-compatible. The plugin reads the config and must no-op when `enabled` is false; the state file is launcher-only metadata. The launcher removes both when translation is removed.

### 7.4 In-memory data translation

Hook `DataManager.onLoad` and translate **after** calling the original:

```js
var _onLoad = DataManager.onLoad;
DataManager.onLoad = function(object) {
    _onLoad.call(this, object);
    translateDataObject(object);
};
```

Translating after the original means game plugins' `onLoad` handlers see the original data. Standard engine windows read the live data objects afterward, so display is still translated.

Use identity-based dispatch instead of generic array walking:

- `$dataActors`: **skip**; actor names are handled by display getters.
- `$dataClasses`, `$dataSkills`, `$dataItems`, `$dataWeapons`, `$dataArmors`, `$dataEnemies`, `$dataStates`: translate only the fields listed in §6.2.
- `$dataTilesets`, `$dataAnimations`, `$dataTroops`, `$dataCommonEvents`: **skip**. Their event text is handled at runtime, and their names are editor-only/not displayed.
- `$dataMapInfos`: translate `name`.
- `$dataMap`: translate `displayName` only. Do not modify event command lists, event names, or notes.
- `$dataSystem`: translate `currencyUnit`, type/element arrays, and `terms.*` as listed in §6.2. Do **not** translate `gameTitle`, `switches`, `variables`, or file names.
- Any unknown data object: ignore.

This avoids translating technical strings, file names, notes, and plugin data.

**Save-critical exceptions:**

- `$dataActors` is never mutated.
- `$dataSystem.gameTitle` is never mutated. In MV, `DataManager.isThisGameFile()` compares a saved `title` against `$dataSystem.gameTitle`; in MZ, save metadata also relies on the raw title/global ID. Changing it would hide or misidentify existing saves.
- Event command lists are never modified. `Game_Interpreter.setup()` stores the list by reference and `$gameMap` is serialized in saves; modifying event lists would leak translated commands into save files.

### 7.5 Runtime dialogue, choices, and speaker names

These texts are translated at display/consumption boundaries, not by rewriting game data.

**Patch policy:** every runtime hook must wrap the previously installed method, not replace it. The translation plugin loads last, and earlier game plugins may have customized the same methods. Use the pattern:

```js
var _original = Game_Message.prototype.allText;
Game_Message.prototype.allText = function() {
    var value = _original.apply(this, arguments);
    return translateBlock(value);
};
```

This preserves plugin extensions and lets the translation layer transform only the final result.

**Dialogue and scrolling text:** wrap `Game_Message.prototype.allText()` after the original returns:

```js
var _originalAllText = Game_Message.prototype.allText;
Game_Message.prototype.allText = function() {
    var raw = _originalAllText.apply(this, arguments);
    return translateBlock(raw); // full-block first, then per-line
};
```

- `Window_Message` and `Window_ScrollText` both call `allText()`, so this covers standard dialogue and scrolling text.
- `$gameMessage` is not saved, so this is safe.
- If no dictionary entry exists, return the original joined text.
- Translation happens before wrapping. Wrapping is a separate step described in §7.8; it must not pre-wrap raw source text or make the full-block key miss.

**Choices:** wrap `Game_Message.prototype.setChoices`; translate the choice strings and call the original with the translated array. Preserve all original arguments and call the original method rather than reimplementing its storage logic. Choices are not serialized in saves; only indices are used for logic.

**MZ speaker name:** wrap `Game_Message.prototype.setSpeakerName` if it exists, translate the name, and call the original.

Do **not** translate `402` labels; they are not displayed.

### 7.6 Actor names, conditionals, name input, and saves

Actor names need special handling because standard event conditionals compare actor names and because the standard Name Input scene writes the edited value back into raw actor state.

- Do **not** translate `$dataActors.name/nickname/profile`.
- Do **not** translate `320`/`324`/`325` parameters in game state.
- Wrap `Game_Actor.prototype.name`, `nickname`, and `profile`: call the original method, then translate its return value. Do not replace the original method.
- Wrap `Game_Interpreter.prototype.command111` so actor-name comparisons run with raw names:
  - Set a plugin-level `rawNamesForConditional` flag.
  - While the flag is set, the `Game_Actor.name` wrapper returns `this._name` instead of translating.
  - Call the original `command111`; restore the flag in `finally`.
- Handle standard Name Input Processing:
  - Wrap `Window_NameEdit.prototype.initialize` (MV) and `Window_NameEdit.prototype.setup` (MZ), or a shared helper, to remember both the raw actor name and the displayed translated name:
    - `_rpgmRawName = actor._name`
    - `_rpgmDisplayName = this._name` (the exact value the window actually shows)
  - Note: MV's `Window_NameEdit.initialize` slices the initial name with `this._maxLength` before assigning `this._maxLength = maxLength`, so MV may not truncate the initial display. MZ's `setup` does truncate. Capture the actual `this._name`; do not assume truncation behavior is identical. Optionally clamp the MV initial display for consistent input behavior, but do not rely on it for correctness.
  - Wrap `Scene_Name.prototype.onInputOk` with this exact sequence:
    1. Capture `edit = this._editWindow` and `actor = this._actor`.
    2. Determine `unchanged = edit && actor && typeof edit._rpgmRawName === "string" && typeof edit._rpgmDisplayName === "string" && edit.name() === edit._rpgmDisplayName`.
    3. Call the original `onInputOk` first. It stores `edit.name()` via `actor.setName(...)` and calls `popScene()`.
    4. In a `finally` block, if `unchanged` is true, call `actor.setName(edit._rpgmRawName)` to correct the raw state back to the original name. If `edit`, `actor`, or the guard fields are missing, or `unchanged` is false, do not modify the result.
    5. Never call the original `onInputOk` after setting the raw name; the original would overwrite it with the translated display.
  - This ordering is important: the wrapper must correct after the original, not before.
  - If a game plugin replaces `Window_NameEdit` or `Scene_Name`, the wrapper may not see it; test standard scenes and document plugin-scene incompatibility as out of scope.

This gives:

- New games: raw `_name` from `$dataActors`, translated display in menus, `\N`, and battle log.
- `320 Change Name`: raw new name stored; display translated if the dictionary has it.
- Actor-name conditionals: compare raw names, so they still work.
- Name Input: translated display while editing, raw name preserved if unchanged.
- Existing saves: raw `_name`/`_nickname`/`_profile` restored from save; display translated; no save migration.

Add tests for:
- New game + `111` actor-name conditional.
- `320` followed by an actor-name conditional.
- Continue save with `\N` dialogue.
- Battle-log actor names.
- Custom player-entered actor name not present in the dictionary.
- Name Input opened and confirmed unchanged; raw `_name` must remain original.
- Name Input opened, edited, confirmed; edited value must be stored raw and display correctly.
- The same Name Input tests followed by an actor-name conditional and a save/reload.

### 7.7 Game title

- Do **not** translate `$dataSystem.gameTitle` in memory.
- Wrap `Scene_Title.prototype.drawGameTitle`: call the original method with `$dataSystem.gameTitle` temporarily substituted with the translated title, then restore the raw value in `finally`. This preserves any game-plugin title customizations.
- Keep save metadata (`title`) raw so MV `DataManager.isThisGameFile()` and MZ save metadata continue to recognize existing saves.
- If the title screen font needs the translation font, apply the font override there too.

### 7.8 Word wrapping and fonts

These are layout-support items, not text extraction.

**Wrapping (important for Japanese → English):**

- Add a basic dialogue wrapper in `Window_Message`/`Window_ScrollText`; wrap the existing `startMessage` methods rather than replacing them, so earlier plugin customizations survive.
- Translation and wrapping must happen in this order:
  1. Call `$gameMessage.allText()` (which includes translation) to get the translated display text.
  2. If the result differs from the raw text, wrap that translated text for the actual window width.
  3. Try to temporarily shadow the current `$gameMessage.allText` with a function returning the wrapped text for the entire duration of the original `startMessage` call. Do not mutate `$gameMessage._texts`. If the shadow cannot be installed (frozen/sealed object, non-writable property, or assignment exception), skip wrapping and call the previous `startMessage` normally; normal translation via the `allText` wrapper must still work.
  4. Call the previous `startMessage` method, then restore the original `allText` property in `finally` (delete the shadow if it was not an own property). This must survive exceptions and plugin wrappers that call `allText` zero, one, or multiple times.
- Do not use a one-shot bypass flag. A scoped override that is always restored in `finally` is required so a stale wrapped message cannot leak into the next message.
- Do not pre-wrap raw source text before `allText()`; that would make the full-block key miss.
- Do not permanently rewrite `$gameMessage` state; the temporary wrapped text exists only for the duration of `startMessage`.
- Available width is based on `contentsWidth()` and the actual text origin/face offset (`textState.left`/`textState.startX`), **not** on `newLineX()` as a width value.
- Preserve manual `\n`, `\f`, and text codes.
- Do not break inside `\C[...]`, `\I[...]`, `\N[...]`, `\V[...]`, `\P[...]`, `\FS[...]`, `\PX[...]`, `\PY[...]`.
- Use word boundaries for Latin text; allow character boundaries for CJK.
- Do not reflow database item names, menu labels, or system terms.
- Make wrapping configurable (`wrap` in config), default on for dialogue, and test with/without a face image on MV and MZ.
- Add a test with a long multi-line full-block translation and wrap on; it must not fall back to untranslated fragments.

**Fonts (important for English → Chinese):**

- Add an optional `fontFace` config value (for example `PingFang SC`).
- When set, wrap `Window_Base.prototype.standardFontFace()` (or the MZ equivalent) and return the configured font **followed by the original font fallback chain**, not the configured font alone.
- Apply the same fallback-aware font to the translated title screen bitmap.
- Default is off; the user enables it when target glyphs render as boxes.
- Never bundle or download fonts automatically.
- Test a missing glyph and Latin text with the override on; the original fallback must still cover unsupported glyphs.

### 7.9 Logging and safety

- Log with `[RPGM Launcher][Translation]` prefix. NW.js stdout/stderr is already redirected to the launcher log.
- Wrap all runtime hooks in `try/catch`; a translation failure must not crash the game.
- If dictionary/config loading fails, log and continue with original text.
- Never modify `data/*.json` at runtime.
- No network access required.
- Write plugin code in ES5-compatible syntax (`var`, functions, plain objects, no `Map`/`WeakSet`/arrow functions) because the launcher lets users select older NW.js versions. Guard `String.prototype.normalize` with a feature check.
- Do not use a global `Window_Base.drawText`/`drawTextEx` hook. Standard UI text is covered by data fields, actor getters, system terms, and `Game_Message`; a global draw hook can corrupt numeric UI and is unnecessary.

---

## 8. Launcher UI and state

### 8.1 Main window

- Add a **Translate Game...** button, enabled only when the selected folder is detected as MV/MZ.
- Keep the existing layout style. The detailed controls live in a dialog.

### 8.2 Translation dialog

Fields/actions:

- Game title, engine, detected data path.
- Current status:
  - Not configured
  - Loaded N entries, M matched, X empty, Y unknown
  - Enabled/disabled
- **Export Raw Text JSON...**
- **Load Translated JSON...**
- **Enable translation on launch** checkbox.
- **Remove installed translation**
- **Open translation folder** — opens the game's `js/plugins` directory where the dictionary/config/state live.
- **Advanced** section:
  - Optional font face override.
  - Wrap toggle (default on).
  - Debug logging toggle.
- Close button.

Use `QFileDialog` for save/load. Use `QProgressDialog` or a busy cursor for large exports/loads. Show validation warnings in a scrollable text area, not a giant message box.

### 8.3 Per-game state (inside the game folder)

All per-game translation state lives inside the game folder, under the detected `js/plugins` directory:

```text
<js>/plugins/RPGM_Launcher_Translate.js
<js>/plugins/RPGM_Launcher_Translate.json
<js>/plugins/RPGM_Launcher_Translate.config.json
<js>/plugins/RPGM_Launcher_Translate.state.json
```

- `RPGM_Launcher_Translate.json` is the active dictionary.
- `RPGM_Launcher_Translate.config.json` contains runtime settings: `enabled`, `fontFace`, `wrap`, `debug`.
- `RPGM_Launcher_Translate.state.json` contains launcher metadata only.

State file example:

```json
{
  "schema_version": 1,
  "source_path": "/path/to/translated.json",
  "entry_count": 2456,
  "matched_count": 2400,
  "empty_count": 56,
  "unknown_count": 20,
  "updated_at": "2026-10-26T00:00:00Z"
}
```

Notes:

- The installed dictionary is the source of truth for what the game displays.
- Loading writes directly into the game folder; there is **no Application Support translation cache**.
- If the game folder is not writable, show a clear error. Suggest starting the game once (the existing launch flow fixes ownership/permissions) or fixing the folder permissions; do not silently cache the translation elsewhere.
- Re-loading a file replaces the installed dictionary, config, and state atomically: stage all files as temporary files in the same directory, validate them, back up the previous set, swap them in, update the cleanup index only after success, and roll back on failure. A disk-full/permission/interrupted write must not leave a new dictionary with an old config/state.
- `source_path` is informational for display/reload convenience. The installed files work even if the original translated JSON is moved or deleted.
- `enabled` lives in the config. The plugin must independently check `config.enabled` and no-op when false, so a failed loader-entry removal cannot keep a disabled translation active.
- Disabling translation removes the plugin loader entry but keeps the dictionary/config/state in the game folder so it can be re-enabled later. **Remove installed translation** deletes all four files and the loader entry.
- Removal order: remove the loader entry first, then the dictionary/config/plugin, and delete the state file last so a failure can be retried. Removing the cleanup-index entry is the final step; if it fails, a stale index entry is harmless and should be treated as a no-op if all translation files are already gone.
- Moving or copying the game folder keeps the translation with it.
- The state file is optional launcher metadata. If only `RPGM_Launcher_Translate.state.json` is missing while the dictionary/config/plugin are valid, the translation is still active: rebuild a minimal state record or show “translation active, metadata missing” and allow disable/remove. Report translation loss only when the dictionary/config/plugin itself is missing.
- If a game update deletes `js/plugins` (dictionary/config/plugin), the per-game translation is lost. Re-load the translated JSON and re-export after the update; there is no central dictionary backup.
- The launcher keeps a minimal cleanup index at `~/Library/Application Support/RPGM-Launcher/translation_cleanup_index.json` mapping game path to plugin directory. It contains **paths only** — no translation text or per-game status — and exists solely so Uninstall All can find installed translations to clean up.
  - Update/upsert the index when a translation is installed, when a game with translation state is selected or launched (covers copied/moved games), and remove the entry after successful removal.
  - Prune/repair the index when a game is selected or launched: missing game paths or missing translation files are stale entries and should be removed without treating them as errors.
  - On Uninstall All, iterate the index and remove the installed translator files/entries.
  - If a game path is missing or its translation files are already gone, treat it as stale: remove the index entry and continue; do not abort the whole uninstall.
  - If a game folder exists but cleanup fails (read-only, unparseable `plugins.js`, etc.), abort the Application Support deletion or preserve the cleanup index and show the affected games. Do not delete the only record that can find the leftover files.
  - If the index is missing, warn that per-game translation files may remain and offer a repair action: a folder picker or a scan of recent game folders to locate and clean installed translations. Do not silently claim a complete uninstall.
  - If writing the cleanup index fails after a successful game-folder install, warn the user and retry; the translation still works, but Uninstall All may not discover it.

### 8.4 Launch flow

In `start_game()` for MV/MZ, after `chown`/`chmod` and after optional `game_en.exe` extraction/Cheat Menu preparation, before launching:

1. Read `RPGM_Launcher_Translate.config.json` and `RPGM_Launcher_Translate.state.json` from the detected `js/plugins/` directory.
2. If `config.enabled` is true and the dictionary exists and parses:
   - Ensure `RPGM_Launcher_Translate.js`, dictionary, and config are present/correct in `js/plugins/`.
   - If state is missing, rebuild a minimal state record or mark the UI as “translation active, metadata missing”; do not treat this as translation loss.
   - Add/ensure the marked plugin entry.
   - Install files first, then modify `plugins.js`; if anything fails, roll back and log.
   - Update/upsert the cleanup index now that the game-folder install is valid.
   - If installation fails or the dictionary is missing/corrupt, show a non-blocking warning and launch the game untranslated rather than refusing to start.
3. If `config.enabled` is false:
   - Remove the translator loader entry if present.
   - Keep the dictionary/config/state in the game folder so the user can re-enable later without re-loading the file.
   - Keep/upsert the cleanup-index entry so Uninstall All can still find and remove the disabled translation files.
   - The plugin itself must also check `config.enabled` and no-op when false, as a second line of defense if entry removal fails.
4. If config is missing/invalid or the dictionary is missing/invalid, remove any translator loader entry regardless of whether the other file exists.
5. Launch NW.js as before.

Also call the same prepare/cleanup step in `export_standalone_app()` before copying the game folder:

- If translation is enabled, preparation must succeed before export. If it fails, abort the export with a clear error; do not silently create an untranslated app.
- If translation is disabled, remove the loader entry and exclude/remove the four translator artifacts from the **exported app copy** so a stale translation is not shipped. The source game folder keeps its disabled files/state for re-enable.
- If cleanup/removal for the disabled export fails, abort the export rather than copying stale translation files.

### 8.5 Removal and uninstall

- Remove the translator entry from `plugins.js`.
- Remove `RPGM_Launcher_Translate.js`, `.json`, and `.config.json`.
- Delete `RPGM_Launcher_Translate.state.json` last, after installed-file removal succeeds, so a failure can be retried.
- Remove the game path from the launcher's `translation_cleanup_index.json` after successful removal. If this final index update fails, a stale path is harmless: cleanup treats missing files/entries as a no-op.
- If the game is running, tell the user a restart is needed to see original text.
- Never touch Cheat Menu files, protection scripts, or data JSON.
- Update `uninstall_all()` so that before deleting `~/Library/Application Support/RPGM-Launcher`, it reads `translation_cleanup_index.json` and removes translator files/entries from each listed game folder.
  - Missing game path or already-absent translation files: prune the stale index entry and continue; do not abort the whole uninstall.
  - Existing game folder but cleanup failure (read-only, unparseable `plugins.js`, etc.): **abort the Application Support deletion** or preserve the cleanup index, show the affected games, and require the user to resolve it. Do not delete the only record that can find the leftover files.
  - Missing cleanup index: warn that per-game translation files may remain and offer a repair action (folder picker or recent-game scan) before deleting Application Support. Do not silently claim a complete uninstall.
  - For standalone export with translation disabled, abort the export if stale translator files/entry cannot be cleaned up in the exported copy, rather than copying a stale translation into the app.

---

## 9. Code changes by file

### `translation.py` (new)

- `detect_game_layout(game_path) -> GameLayout`
- `scan_game(game_path) -> ScanResult` with `block_keys`, `line_keys`, and `all_source_keys`
- `export_raw_text(scan, output_path) -> ExportResult`
- `load_translation(path) -> TranslationData`
- `validate_translation(scan, data) -> ValidationResult`
- `install_translation(game_path, layout, dictionary_path, config, state) -> InstallResult` (atomic staging/rollback)
- `read_translation_state(game_path, layout) -> TranslationState | None`
- `remove_translation(game_path, layout) -> RemoveResult`
- Game-folder state helpers, atomic file staging, and cleanup-index upsert/prune helpers.

### `RPGM_Launcher_Translate.js` (new)

- ES5-compatible dictionary/config loader.
- Normalization.
- `DataManager.onLoad` display-field translation.
- Wrapped `Game_Message.allText`, `setChoices`, `setSpeakerName`.
- Wrapped `Game_Actor` display getters + `command111` raw-name guard.
- Name Input raw-name preservation (`Window_NameEdit` + `Scene_Name.onInputOk`).
- Wrapped `Scene_Title.drawGameTitle`.
- Optional wrapping/font support with original fallback chains.
- Logging and guards.

### `main.py`

- Add import and translation button/dialog.
- Update `check_start_game_button()` to enable the Translate button for MV/MZ.
- Update `start_game()` to read game-folder translation state and prepare/disable translation.
- Update `export_standalone_app()` to prepare/verify translation before copying.
- Update `uninstall_all()` to clean installed translations via the path-only cleanup index.
- Keep `remove_configs()` from touching per-game translation files or the cleanup index.
- Add instruction/help text.
- Do not modify existing Cheat Menu patchers unless a shared helper is useful; translation uses its own `plugins.js` path.

### Spec files

Add `RPGM_Launcher_Translate.js` to `datas` in all three specs:

- `RPG-Maker-Launcher-arm64.spec`
- `RPG-Maker-Launcher-Universal2.spec`
- `RPG-Maker-Launcher-x64.spec`

Update `BUNDLE(version=...)` in all three specs and `current_version` in `main.py`. `translation.py` is included automatically by PyInstaller because it is imported.

### Documentation

- Update `README.md` and the in-app instructions/credits.
- Bump launcher version.

---

## 10. Risks and limitations

| Risk | Mitigation |
|---|---|
| Long translations overflow message/menu windows | Basic dialogue wrapping; keep UI strings short; document. |
| Target language glyphs missing from game font | Optional font override (PingFang SC etc.); apply to windows and title; user-controlled. |
| Script/plugin-generated text not exported | Out of scope for v1; conservative. Future runtime-capture mode. |
| Game uses custom data loading that bypasses standard data objects | Standard database/system fields may still be translated if objects are loaded via `DataManager.onLoad`; otherwise out of scope. |
| Game plugin caches original names during `onLoad` | Translation happens after `onLoad`; standard UI reads live data. Plugin-specific cached UI is out of scope. |
| Packed/encrypted/non-standard data | Detect and report unsupported; do not guess. |
| Same source string needs different translations in different contexts | Flat dictionary limitation; document. |
| Translation file made for a different game/version | Coverage/missing/unknown warnings; keep unknown keys. |
| Control codes lost or changed | Engine-aware validation warnings; never silently auto-fix. |
| Cheat Menu / protection / game update conflicts | Translation uses independent `plugins.js` entry; reapply on every launch. |
| Editing `plugins.js` corrupts plugin list | Bracket-aware edit, temp-file validation, atomic replace, backup; if unsupported, report. |
| Actor names break conditionals | Raw names retained; `command111` raw-name guard; tests. |
| Name Input writes translated name into raw state | Wrap `Window_NameEdit`/`Scene_Name.onInputOk`; store raw if unchanged; tests. |
| Game title translation breaks save detection | `$dataSystem.gameTitle` never mutated; title screen display only. |
| Event list translation leaks into saves | Event lists never modified; `Game_Message.allText` display-time translation. |
| Game running while translation changes | Require restart; show clear message. |
| Game folder not writable when loading translation | Write directly to game folder; on permission error, show clear retry guidance (start game once / fix permissions); no hidden App Support cache. |
| Partial/interrupted load leaves mismatched dictionary/config/state | Stage all files, validate, back up previous set, swap, roll back on failure; tests for read-only/disk-full/interruption. |
| Game update deletes `js/plugins` (dictionary/config/plugin) | Translation is lost; re-load the translated JSON and re-export after the update. Document. |
| Game update deletes only the state file | State is optional metadata; rebuild it or show “translation active, metadata missing”; translation still works. |
| Game folder moved/copied | Translation travels with the folder; cleanup index may be stale. Upsert index when a translated game is selected/launched; prune missing paths. |
| Uninstall All cannot find old games | Use the path-only cleanup index; stale/missing paths are pruned as no-ops. If index missing, warn and offer a repair folder-picker/recent-game scan. Never delete the index before cleanup. |
| Non-ASCII/space paths | `decodeURIComponent` before filesystem access; test. |
| Translation JSON is huge | Object lookup is O(1); synchronous load is acceptable for local files; show progress during export. |
| JSON prototype pollution | Null-prototype dictionary / `hasOwnProperty`; never merge into `Object.prototype`. |
| Malformed translation values | Ignore non-strings; log/validate; no code execution. |
| Older NW.js versions | Plugin uses ES5 syntax; `normalize` feature guard; test at least one old MV-compatible build. |
| `game_en.exe` extraction changes data after export | Warn if coverage drops; re-export after extraction/update. |
| Global draw hooks corrupt numeric UI | No global `Window_Base.drawText`/`drawTextEx` hook; translate known data/message boundaries only. |

---

## 11. Test plan

### Unit tests (Python)

Create synthetic MV and MZ data folders with:

- Actors, Classes, Skills, Items, Weapons, Armors, Enemies, States
- Troops and CommonEvents
- System with terms and null entries
- MapInfos and Map001 with `101`/`401`, `102`, `105`/`405`, `320`/`324`/`325`
- MZ speaker name in `101`
- Text codes, `%1` placeholders, embedded newlines, empty strings, duplicates
- Both `www/data` and `data` layouts
- Paths containing spaces and CJK characters

Assert:

- Expected unique full-block keys are exported.
- `line_keys` contains individual `401`/`405` lines for validation.
- No notes, file names, scripts, plugin commands, comments, or editor-only `402` labels.
- Message blocks are joined correctly.
- Output is valid UTF-8 JSON with empty values.
- MV/MZ text-code validation handles `\\`, `\S`, `\FS`, `\PX`, `\PY` correctly.
- `%n` placeholders are validated in Skills/States as well as System messages.
- MTool line-key dictionaries count as matched coverage.
- `install_translation` writes dictionary/config/state/plugin into the detected `js/plugins` and creates no Application Support translation cache.
- `read_translation_state` reads the game-folder state.
- `remove_translation` deletes the loader entry, dictionary/config/plugin, then state last, and updates the cleanup index.
- Cleanup index records only game path/plugin directory and contains no translation text.

### Runtime/integration tests

Use at least one real MV game and one real MZ game:

- Export → translate a few strings → load → restart → verify dialogue.
- Verify item/skill/state/weapon/armor/enemy/class names and descriptions.
- Verify menu commands and system terms.
- Verify choices, scrolling text, MZ speaker names.
- Verify actor names in menu, `\N`/`\P` dialogue, and battle log.
- Verify `111` actor-name conditions still work on new game.
- Verify `320` change name followed by an actor-name conditional.
- Verify Continue save with custom/raw actor names displays translated names.
- Verify standard Name Input:
  - open and confirm unchanged; raw `_name` stays original;
  - open, edit, confirm; edited value is stored raw and displays correctly;
  - use `restoreDefault` and confirm; raw `_name` stays original;
  - use a translated name longer than `maxLength`; compare against the exact value the window displays, tested separately for MV and MZ;
  - simulate missing `edit`, missing/null `actor`, and missing `_rpgmRawName`/`_rpgmDisplayName`; original behavior is preserved;
  - simulate an original `onInputOk` that throws; the wrapper must not corrupt raw state;
  - run an actor-name conditional and save/reload afterward.
- Verify a test plugin that overrides `Game_Actor.name`, `Game_Message.allText`, and `Scene_Title.drawGameTitle` still works after translation is installed (methods are wrapped, not replaced).
- Verify saves made before and after translation load correctly.
- Verify MV `DataManager.isThisGameFile()` and MZ save metadata still recognize saves after title translation.
- Verify text codes and placeholders survive.
- Verify no event command list rewriting in a mid-event save.
- Verify matching MTool line-key dictionary.
- Verify full-block translation with more/fewer lines than the source.
- Verify `setChoices` preserves original arguments and choice behavior.
- Verify `plugins.js` reinstallation/idempotence and no double plugin load.
- Verify `plugins.js` backup/restore and malformed `plugins.js` reports unsupported.
- Verify `plugins.js` containing BOM/CRLF and a simple template-literal assignment is either handled or rejected cleanly.
- Verify Cheat Menu still works.
- Verify protection enabled/disabled still works.
- Verify standalone app export includes translation and config; launch the exported `.app`.
- Verify disabled translation is excluded from the standalone app copy while the source game folder keeps its files/state for re-enable.
- Verify disabled export aborts if the exported copy cannot be cleaned.
- Verify remove translation restores original text and removes files.
- Verify per-game state is stored only inside the game folder:
  - dictionary/config/state/plugin appear under the detected `js/plugins`;
  - no `translations.json` or translation dictionary exists in Application Support;
  - copying/moving the game folder keeps the translation working;
  - loading into a read-only game folder shows a clear permission error and does not silently cache elsewhere.
- Verify atomic load: simulate failure after dictionary/config/state staging and confirm rollback leaves the previous translation intact.
- Verify disable removes the loader entry but keeps dictionary/config/state for re-enable.
- Verify the plugin itself no-ops when `config.enabled` is false, even if the loader entry removal failed.
- Verify re-enable after a disable works without re-loading the JSON.
- Verify a missing config with a surviving dictionary removes the loader entry and launches untranslated.
- Verify remove deletes the loader entry and all four translation artifacts, with state deleted last; missing files are treated as already removed.
- Verify state-only loss is reported as “translation active, metadata missing” and can be rebuilt/removed; dictionary/config/plugin loss is reported as translation loss.
- Verify `uninstall_all()` cleans installed translations using the path-only cleanup index and aborts safely if an existing game cleanup fails.
- Verify stale cleanup-index entries (deleted game folder, already-removed translation) are pruned and do not block uninstall.
- Verify `uninstall_all()` with a missing cleanup index warns and offers a repair action.
- Verify a cleanup-index write failure after a successful game-folder install warns the user; translation still works and the index is retried later.
- Verify a moved/copied game with translation state is upserted into the cleanup index when selected/launched.
- Verify game update scenarios: deleting dictionary/config/plugin = translation lost; deleting only state = translation still active/metadata rebuild.
- Verify invalid/missing dictionary does not break the game.
- Verify JP→EN long text wrapping, with and without face image, including a long full-block translation.
- Verify wrapping scoped override is restored after:
  - a plugin `startMessage` wrapper that calls `allText` twice;
  - a plugin `startMessage` wrapper that never calls `allText`;
  - a `startMessage` exception, followed by the next message displaying correctly;
  - a non-extensible/frozen `$gameMessage` where the shadow cannot be installed; game must display normally, with translation but without wrapping;
  - the same cases through `Window_ScrollText`.
- Verify `plugins.js` backup is preserved when a clean `.bak` already exists and during removal/reinstall.
- Verify `String.prototype.normalize` unavailable: CRLF still normalizes, and NFD limitation is documented.
- Verify EN→ZH font override on message window and title screen, including fallback for a missing glyph and Latin text.
- Verify game update with missing/new keys.
- Verify paths with spaces/CJK.
- Verify a dictionary containing numeric/short keys does not corrupt numeric UI (no global draw hook).
- Verify old NW.js build if practical.

### Acceptance criteria

- [ ] MV and MZ export produces valid, MTool-compatible flat JSON.
- [ ] Original `data/*.json` hashes are unchanged after export/load/play.
- [ ] Loading a valid file translates the next game launch.
- [ ] Loading requires a restart, and the UI says so clearly.
- [ ] Empty values are ignored; unknown keys do not crash anything.
- [ ] Control-code mismatches are reported before install.
- [ ] Actor-name conditionals and saves continue to work.
- [ ] Name Input preserves raw names when the player confirms without editing.
- [ ] Data `gameTitle` remains raw for save recognition; title screen can still be translated.
- [ ] Per-game dictionary/config/state live inside the game folder and travel with it.
- [ ] Loading is atomic; a failed/interrupted load keeps the previous working translation.
- [ ] Disabling translation leaves no active loader entry, and the plugin no-ops when disabled; files remain for re-enable.
- [ ] Removing translation deletes the loader entry and all translation artifacts, with state deleted last.
- [ ] Missing state alone is recoverable; the translation remains active and metadata can be rebuilt.
- [ ] Uninstall All handles stale index entries as no-ops and warns/offers repair if the cleanup index is missing.
- [ ] Cheat Menu and protection remain functional.
- [ ] Standalone app export contains the active translation; a disabled translation is excluded from the exported copy.
- [ ] No network access is required.
- [ ] Failure paths log and fall back to original game text.

---

## 12. Implementation phases

1. **Backend scanner/exporter**
   - `translation.py`: layout detection, extraction (block + line keys), export, validation.
   - Synthetic MV/MZ tests.
2. **Runtime plugin core**
   - Dictionary/config loading and normalization.
   - `Game_Message.allText`, choices, speaker.
   - `Game_Actor` getters + `command111` guard.
   - `DataManager.onLoad` safe display fields.
   - `Scene_Title` title.
   - Manual test against sample games.
3. **Launcher integration**
   - Translate button/dialog.
   - Per-game state stored inside the game folder.
   - Install/disable/remove and standalone export.
   - `plugins.js` entry with atomic edit/backup.
   - Path-only cleanup index and `uninstall_all()` cleanup.
4. **Layout polish**
   - Basic dialogue wrapping.
   - Optional font-face override (windows + title).
   - Warnings/coverage UI.
5. **Validation and release**
   - Real MV/MZ game tests.
   - Docs/instructions/credits.
   - Version bump and spec updates.

---

## 13. Review checklist

- Is the MV/MZ data-field and event-command scope correct and safe?
- Is display-time translation sufficient for all standard text paths?
- Are actor-name getters, `command111` guard, Name Input raw-name preservation, and save behavior correct?
- Is `Game_Message.allText` the right place for block/line translation, and is the wrap order safe?
- Is the `plugins.js`-only injection the safest choice in this codebase?
- Are the message, choice, speaker, scrolling-text, and title algorithms correct?
- Is install/disable/removal idempotent, transactional, and safe with Cheat Menu/protection?
- Is the game-folder state portable and is the path-only cleanup index sufficient for Uninstall All?
- Are there critical gaps in export/validation/state handling?
- Are the layout/font limitations acceptable for the locked language pairs?
- Is anything in the test plan missing that could break a real game?
