# Translation Follow-up Plan v2 — MTool-compatible export, speaker/title coverage, and exact injection

**Status:** P0–P3 implemented and reviewed; final critical-only verdict CLEAN
**Builds on:** `translation-plan-v1.md` and the v1 implementation
**Trigger:** Real-game feedback from an MV game using YEP MessageCore, custom menus, and an image title
**Supersedes:** the first follow-up draft, which incorrectly treated a boot-time `blockHits=0` as proof of a page-separator bug.

**P0 implemented:** `ManualTransFile.json` default name with preflight/backup, coverage report, missing-only export, merge-on-load prompt, YEP/MZ speaker-name boxes, derived speaker-name map, window/document title translation, and runtime hit counters.

**P1 implemented:** clean fragment export with no recognized RPG Maker codes or YEP tags, exact-key compiler at load/install time, page-separator preservation, and targeted save-slot title/party-name display hooks.

**P2 implemented:** identity-value export default, plugin-parameter UI text extraction, exact plugin-label rendering hook, menu-command label translation, Ticker/FTKR targeted hooks, optional image-title overlay, and editor-metadata export. Version bumped to 3.5.0.

---

## 1. Verified findings

### 1.1 The two “ManualTransFile” files are not the same corpus

| File | Keys | Values | Backslash keys | What it is |
|---|---:|---|---:|---|
| Game-local `ManualTransFile.json` | 2,723 | all empty | 1,741 | our current block-level raw export |
| Game-local `ManualTransFile_result.json` | 2,723 | 2,519 translated, 204 empty | 1,741 | result loaded into the launcher |
| Installed `RPGM_Launcher_Translate.json` | 2,519 | all translated | — | exactly the non-empty result entries |
| External `translate_workdir/ManualTransFile.json` | 17,462 | identity values | 0 | broader MTool corpus used for format comparison |

Only **687 of our 2,723 keys** match the external MTool file exactly. Our scan has **4,692 individual 401/405 lines**, of which **1,761** match MTool exactly. The external file contains much more than this game’s display text: plugin/UI/help strings, event metadata, and fragments. It is a useful format reference, but it is not a safe merge fixture.

### 1.2 Why our export feels noisy

- Our export is **block-level**: 1,983 joined message blocks plus 740 standalone strings.
- 1,741 of 2,723 keys contain RPG Maker control codes or YEP tags; 1,672 blocks contain `\n<...>` speaker tags.
- MTool’s file has **zero** backslashes because it stores cleaned text runs, not code-bearing blocks.
- The translation tool still translated 2,519 entries, including 1,951 full blocks, so exact block injection works. The remaining gaps are not caused by escaping.

### 1.3 Runtime facts, corrected

- `blockHits=0` was measured at boot, before any dialogue was displayed.
- The installed dictionary contains **1,951 of 1,983 exact block keys**. A normal dialogue display should hit them.
- The reported 671 hits are database/system translations performed during `DataManager.onLoad` (566 database fields + 77 System + 28 MapInfos), not dialogue hits.
- `Game_Message.newPage()` appends `\f` only for level-up/drop-item messages and plugins; ordinary 101/401 dialogue does not. The current line fallback still mishandles `\f`, so it should be fixed, but it is not the explanation for `blockHits=0`.
- **Speaker names:** 39 unique YEP `\n<...>` names; the raw export does not include the inner names as standalone keys, and `Window_NameBox` is not hooked. Dialogue blocks may be translated while the name box remains Japanese.
- **Title:** `$dataSystem.optDrawTitle` is false. The title screen is an image (`title_1.rpgmvp`), so `Scene_Title.drawGameTitle` never draws text. `document.title` still contains the raw Japanese title.
- **Item names:** all 46 actual `Items.json` names (49 entries including 3 separators) are translated and present. The missing names are:
  - Weapons: 26 / 51 entries (unique display names in the coverage panel: 22 / 30)
  - Armors: 7 / 34 entries (unique: 4 / 13)
  - Skills: 151 / 171 entries (unique: 144 / 160)
  - States: 26 / 47 entries (unique: 24 / 43)
  - Enemies: 39 / 91 entries (unique: 25 / 54)
  These are part of the **204 empty translations** produced by the translation tool, not an extraction or runtime-lookup bug. The empty set also contains separators (`---武器---`), single-character element names (`風`, `火`), formulas, chapter labels, and unused strings.
- **Actor names:** standard `Game_Actor.name()` works (`伊莉丝`). Custom plugins that read `$dataActors[...].name` or `_name` directly can bypass it. A global `Window_Base.drawText` hook is unsafe and was already rejected in v1; targeted compatibility patches are the safe path.
- `ManualTransFile_unsured_result.json` adds **8** translated keys that are not in the result file. They can be merged optionally.

---

## 2. Goals

1. Default export name `ManualTransFile.json`.
2. Export clean, MTool-style keys with no recognized RPG Maker codes or YEP name tags.
3. Keep enough information to inject translations safely; compile cleaned translations back to exact runtime keys at install/load time.
4. Export speaker/name-tag names and line-level text, not only joined blocks.
5. Fix YEP/MZ speaker-name boxes.
6. Translate the document/window title and save-slot title display; make an image-title overlay optional.
7. Make the 204 empty DB translations visible and easy to re-export.
8. Fix `\f` page handling for battle/reward messages without changing normal dialogue flow.
9. Keep saves, game-title save detection, event conditionals, Cheat Menu, and protection working.

### Non-goals

- Translating image assets automatically.
- Injecting JS/CSS/plugin-command/note-tag strings into the runtime dictionary.
- A global draw-text replacement hook.
- Automatic translation of the 204 empty entries (the launcher cannot invent translations).
- Full OCR/vision translation.

---

## 3. Export format v2

### 3.1 File name and overwrite safety

- Default filename: **`ManualTransFile.json`**.
- Runtime dictionary remains `RPGM_Launcher_Translate.json`.
- Before overwriting an existing file:
  - show path, total keys, non-empty values, size, and modification time;
  - if it contains non-empty values, require a second confirmation because exporting empty values would erase them;
  - create a unique timestamped backup (`ManualTransFile.YYYYMMDD-HHMMSS[-N].json.bak`) with exclusive creation; abort if the backup cannot be created;
  - write atomically via temp file + rename;
  - re-check the target hash/mtime before replacing to avoid clobbering a concurrent MTool write.
- Reserve, case-insensitively: `ManualTransFile_result.json`, `ManualTransFile_skipped.json`, `ManualTransFile_unsured_result.json`. Never write or modify these.
- If the selected folder looks like an MTool workspace (contains `ManualTransFile_result.json`), default to a per-game subfolder or require an explicit “overwrite MTool workspace” confirmation.
- Remember the last export directory.

### 3.2 Clean key rules

Normalization applies **only to the export file**, never to exact runtime keys.

1. Normalize NFC and CRLF → LF.
2. Tokenize the source left-to-right into text runs and control/tag tokens. Do not remove codes before tokenizing; token boundaries are needed for later reconstruction.
3. Recognize at least:
   - `\C[n]`, `\CE[n]`, `\I[n]`, `\V[n]`, `\N[n]`, `\P[n]`, `\G`
   - `\FS[n]`, `\PX[n]`, `\PY[n]`, `\S[n]`
   - `\SE[...]`, `\SW[...]`
   - lowercase `\v[n]`
   - `\!`, `\.`, `\|`, `\^`, `\<`, `\>`, `\$`, `\{`, `\}`, `\\`
   - YEP name tags `\n<...>`, `\nc<...>`, `\nr<...>` (require `<`; uppercase `\N[n]` is an actor-name placeholder, not a tag)
   - Other `\X[...]` or `\X<...>` tokens: treat as opaque tokens and report them.
4. Split only the **text runs** at actual newlines and `\f`. Do not split at punctuation or inside words.
5. Trim outer whitespace from each fragment.
6. Drop empty and code-only fragments.
7. Preserve source punctuation, including trailing `...`, `…`, `。`, `！？`, `？！`, `♪`, and `～`. The plan does not apply MTool’s optional trailing-period stripping because it loses information; the compiler matches the exported key directly.
8. Export the inner text of static YEP name tags as a separate name key. Strip codes from code-wrapped names. Skip purely dynamic names (`\v[n]`, `\N[n]`, `\P[n]`) at export and resolve them at runtime.
9. Keep single-character **database/name strings** such as `風`; do not emit every one-character dialogue fragment (`は`, `っ`, `）`, `♥`).
10. Use parsed newline characters for multi-line keys, never the literal two-character sequence `\n` as a name tag.
11. Deduplicate keys. One cleaned fragment may serve multiple exact sources; the compiler applies the same translation consistently. Duplicate normalized keys are reported by validation.

Example:

```json
{
  "ここがあのゴミが言ってたフェーアト草原ね…": "",
  "イリス": "",
  "キモオジ": "",
  "んぉッ": "",
  "もッ、ふざけんなッ": "",
  "こらぁッ": "",
  "風": "",
  "快感薬": ""
}
```

### 3.3 What is exported by default

- Cleaned 401/405 text fragments.
- Choices (`102`), MZ speaker names, `320`/`324`/`325`.
- Static YEP/MZ speaker/name-tag names.
- All current standalone display fields: Actors name/nickname/profile; Classes; Skills; Items; Weapons; Armors; Enemies; States; System title/currency/types/terms/messages; MapInfos names; map display names.
- Code-free full message blocks (only 263 in this game) as optional context.
- **Not** code-bearing blocks by default. A legacy “exact blocks” export mode remains available for troubleshooting.

Optional metadata export (separate file, never injected):
- Event/CommonEvent/Troop names, animation/tileset names, switch/variable names, event comments, plugin descriptions/parameters, JS/CSS/UI strings.

Additional export mode:
- **Missing only**: export just the 204 currently empty values, with category comments or a sidecar report, so they can be translated without redoing existing work. The flat JSON itself stays `{source: ""}`; category context can live in the coverage report.

### 3.4 Value convention

Use **identity values** as the default for `ManualTransFile.json` (`"source": "source"`). This matches the user’s MTool translation prompt and makes untranslated entries explicit. An **empty values** option remains available in Advanced for tools that prefer `"source": ""`. Untranslated identity entries are ignored by the compiler when loading.

### 3.5 Exact dictionary compiler

Cleaned keys alone cannot preserve `\V`, `\N`, `\I`, name tags, or `%n` positions. Add an install/load-time compiler:

1. Scan the game’s exact sources using the existing `scan_game()`.
2. For each exact source, tokenize into text runs + ordered tokens.
3. Look up each text run in the cleaned translated dictionary.
4. If all runs are translated, rebuild an exact translation by reinserting the source tokens in their original order.
5. If some runs are missing, keep the source for those runs and log a partial-coverage warning.
6. For standalone/code-free strings, use the direct translation if present.
7. Precedence: exact source translation (if the user provided one) > compiled from cleaned fragments > source.
8. Validate ordered control-token and placeholder sequences (`%1`, `%2`, not just counts) before writing.
9. Write **exact keys only** to `RPGM_Launcher_Translate.json`. Do not install cleaned keys.
10. Report unknown keys and unmatched fragments; exact overrides win. Per-source collisions are not materialized because the same fragment translation is reused deterministically.

No sidecar is required at runtime. An optional report can list alias→source mappings, but the runtime dictionary remains self-contained.

---

## 4. Runtime changes

### 4.1 Measure first, then handle page separators

- Counters distinguish exact block hits, page hits, line hits, and misses.
- Measured behavior with the real game: `blockHits=0` at boot was a pre-dialogue snapshot; a normal message hits exact blocks. Page separators affect battle/reward messages, not ordinary 101/401 dialogue.
- Implemented captured page splitting:

```js
var parts = text.split(/(\f\n?)/); // content, separator, content, ...
```

Translate only the content segments, leave separators byte-for-byte unchanged, and rejoin. Never change the number of pages. Keep exact whole-text lookup first so keys that intentionally contain `\f` still win. If a translation changes the page count, log and keep the source page.

### 4.2 Lookup priority

1. Exact whole text (with codes/tags/`\f`).
2. Exact content page after separator handling.
3. Exact line/block key.
4. Exact compiled dictionary entry (fragments already compiled at install time).
5. Otherwise return the source unchanged.

No runtime substring matching and no arbitrary fragment reassembly. Choices use exact/alias lookup only. `\C`, `\CE`, `\I`, `\V`, `\N`, `\P`, `\G`, `\FS`, `\PX`, `\PY`, `\!`, `\.`, `\|`, `\^`, and `%n` are preserved or the source is left unchanged; never “drop cosmetic codes when necessary.”

### 4.3 Speaker names

- Extract static and dynamic name tags from 401 text into the export, and provide a standalone name key for translation tools.
- At runtime, derive a **speaker-name map** from existing translated blocks: if a dictionary key contains `\n<raw>` and its value contains `\n<translated>`, map `raw → translated`. This fixes names that were translated inside dialogue blocks but never exported as standalone keys (33 of 39 names in the test game). Do not apply this map globally; only use it in name-box translation.
- **YEP MessageCore (MV):**
  - Hook `Window_NameBox.prototype.refresh(text, position)`.
  - `text` is already escape-converted; preserve `\C`/`\I` tokens and translate complete text runs.
  - Translate before calling the original; return the original result (`''`), because YEP uses that result as the replacement text.
- **MZ:**
  - Hook `Window_NameBox.prototype.setName(name)`, not `refresh()`.
  - MZ stores `_name`, and `start()` measures width before `refresh()`; translating only in `refresh()` gives wrong placement.
  - `Game_Message.setSpeakerName` remains the path for static MZ speaker names.
- Handle static names, `\v[n]` after expansion, `\C[...]`-wrapped names, and nested tags. Do not translate tag syntax itself. Rely on the existing translated-value guard to avoid double translation.

### 4.4 Title

- Hook `Scene_Boot.prototype.updateDocumentTitle` after the original and set the translated `$dataSystem.gameTitle`. This fixes the NW.js window title for this game.
- Keep the existing `Scene_Title.drawGameTitle` hook for games where `optDrawTitle` is true.
- Save slots/custom save windows: translate only the display copy of `info.title`/`_info.title`; never mutate `DataManager.makeSavefileInfo()` or the save file. Targeted support for AltSaveScreen, BB_CustomSaveWindow, and YEP SaveCore.
- Image title: optional translated-text overlay, default off. Implement via `Scene_Title.createForeground` (or `create`), not `drawGameTitle`, with configurable position, font, size, outline, and background opacity. Never mutate `$dataSystem.gameTitle`; save detection must stay raw.
- Asset replacement of `title_1.rpgmvp` remains an explicit advanced/manual operation.

### 4.5 Actor names and custom UI

- Keep `Game_Actor.name`/`nickname`/`profile` and `command111` raw-name protection.
- Do **not** add a global `Window_Base.drawText`/`drawTextEx` hook.
- Add an optional helper API (`RPGMLauncherTranslate.actorName(...)`) and targeted, opt-in compatibility patches for known plugins:
  - CBR_eroStatus: translate its `\N`/`\P` expansion path (it draws via `contents.drawText`).
  - YEP SaveCore: translate the display copy of party names and `_info.title`.
  - AltSaveScreen / BB_CustomSaveWindow: translate `info.title` at draw time.
  - Ticker / FTKR skill tree: patch their text assembly or leave unsupported; concatenated strings cannot be fixed safely by a generic hook.
- Never mutate `$dataActors`, `_name`, save data, or image filenames.

### 4.6 Coverage and the 204 empty translations

- Add a read-only coverage report with counts by category: translated, empty, identity, missing, separator-only.
- Include Items, Weapons, Armors, Skills, States, Enemies, Actors, Classes, System, MapInfos.
- Exclude separator/header strings from “missing” counts.
- Add “Export missing only” so the 204 empty values can be sent back through the translation tool.
- Optionally merge the 8 extra entries from `ManualTransFile_unsured_result.json`.
- Do not add a global glossary backfill (`風` → `风`). It would also translate unrelated dialogue/UI occurrences. If the user wants a glossary, make it an explicit, reviewable merge step scoped to DB fields.
- Import merge rules:
  - accept only keys present in this game’s exact source set;
  - fill missing entries only by default;
  - skip empty and identity values;
  - validate ordered token/placeholder sequences;
  - report unknown keys and noise;
  - back up the installed dictionary and write atomically;
  - never modify `_result`, `_skipped`, or `_unsured_result` files.

---

## 5. Launcher UI changes

Translation dialog:

- Default export name `ManualTransFile.json`.
- Overwrite preflight with key count, non-empty count, hash/mtime, and unique backup.
- Export mode:
  - **MTool-compatible clean text** (default)
  - **Exact blocks (legacy/troubleshooting)**
  - **Missing only**
  - **Metadata/full corpus** (separate file, never injected)
- Coverage report after export/load:
  - translated / empty / identity / missing by category;
  - speaker-name count;
  - control-code and placeholder warnings;
  - unknown/ignored key count.
- Optional “Merge unsure results” and “Merge existing dictionary” with fill-missing default.
- Title overlay toggle (default off) with position/size/font preview.
- Advanced compatibility toggles for known custom plugins; default off.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Cleaned aliases are ambiguous (one fragment from many exact sources) | exact-key priority; deterministic fragment translation; unknown-key report |
| Fragment compilation produces awkward grammar in English | preserve token order; document limitation; exact block overrides win; keep fragment compilation optional if needed |
| Token/placeholder loss | ordered token validation; source fallback; never drop `\V`/`\N`/`%n` silently |
| Overwriting a valuable `ManualTransFile.json` | non-empty-value warning; unique backup; atomic write; reserved-name checks; per-game folder |
| MZ vs YEP name-box differences | engine-specific hooks; test both |
| Title overlay covers artwork | default off; configurable position/background; preview |
| Global actor-name replacement corrupts numbers/images/name entry | no global draw hook; targeted patches only |
| Importing a 17k MTool corpus injects noise | exact-source filtering; ignore unknown keys; report; fill-missing default |
| Page separator changes message flow | captured separators; preserve page count; tests |
| Custom menus cache raw names | coverage report; targeted patches only after evidence |
| Script 355/plugin commands compare actor/database names | standard conditional branch (`command111`) sees raw names; scripts/plugin commands see translated display data. Scripts that compare names should use `_name`/IDs. This is an explicit scope limit, not a standard event-conditional regression. |

---

## 7. Tests

### Export

- Parsed keys contain no recognized RPG Maker code sequences and no YEP name tags.
- No U+000C remains; actual newlines only; CRLF normalized.
- Speaker names are separate keys; dynamic names are skipped at export and resolved at runtime.
- Dialogue-only trailing `...` stripping; single `.` and DB/System punctuation preserved.
- Quotes, literal backslashes, tabs, and unusual control characters round-trip or are reported, never silently dropped.
- Duplicate-key detection in JSON input; deterministic deduplication.
- Reserved names are refused case-insensitively.
- Backup is unique on same-second exports; atomic write; concurrent target change aborts.
- Existing `_result`, `_skipped`, `_unsured_result` hashes are unchanged.
- Missing-only export contains exactly the 204 empty keys (plus any newly empty entries).

### Compiler

- Exact block keys are regenerated from cleaned fragment translations with tokens in original order.
- `\C`, `\CE`, `\I`, `\V`, `\N`, `\P`, `\G`, `\FS`, `\PX`, `\PY`, `\!`, `\.`, `\|`, `\^`, `%1`, `%2` order matches the source.
- `%1`/`%2` swap is rejected.
- Missing fragments fall back to source and are reported.
- Unknown/unmatched keys are reported; there is no silent cross-game injection.
- Exact user translations override compiled fragments.

### Runtime

- After a normal two-line message, `blockHits` increases and the dialogue is translated.
- `page1\f\npage2`, `page1\fpage2`, `page1\f\n`, and three-page messages preserve page count and separators.
- YEP `Window_NameBox.refresh`: static `イリス`, dynamic `\v[2]`, code-wrapped `\C[27]...\C[0]`.
- MZ `Window_NameBox.setName`: static and dynamic names; width/placement correct.
- `document.title` translated; save metadata stays raw; save detection still works.
- Title overlay off/on; image title unchanged; `$dataSystem.gameTitle` raw.
- Standard actor menus, name entry, conditionals, saves, Cheat Menu, and protection unchanged.
- All actual item names remain translated (46 unique names plus 3 separators); coverage report shows weapon/armor/skill/state/enemy empties.
- No global draw hook; targeted custom-plugin patches are opt-in and reversible.

### Real-game validation

- Use NW.js devtools to assert hit counters after dialogue, `Window_NameBox` visible text, `document.title`, raw `$dataSystem.gameTitle`, and installed dictionary coverage.
- Screenshot the title screen and one dialogue with speaker names.

---

## 8. Implementation phases

### P0 — Immediate feedback fixes (implemented)

1. Default export filename `ManualTransFile.json` with overwrite preflight/backup.
2. YEP and MZ speaker-name box hooks, plus a speaker-name map derived from translated blocks.
3. `document.title` translation via `Scene_Boot.updateDocumentTitle` and `Scene_Title.start`.
4. Read-only coverage report; verify all actual item names and list the 204 empties by category.
5. “Export missing only” for the 204 empty values, with merge-on-load support.
6. Re-measure `blockHits` after a normal dialogue; add `blockHits`/`lineHits`/`speakerNames` counters.

Success: speaker names and window title translate; the user can see exactly which DB names are missing and re-export them; no save/conditional regression.

### P1 — Clean export and exact compiler (implemented)

1. Tokenize-first clean export of line/fragment keys, speaker names, and standalone fields.
2. Exact dictionary compiler at install/load time.
3. Page-separator handling for battle/reward messages.
4. Merge the 8 extra unsure results by loading them with the Merge option.
5. Save-slot title display for AltSave/BB/YEP SaveCore.

Success: `ManualTransFile.json` has no recognized codes/tags; the translation tool can load it; runtime still gets exact, code-preserving translations.

### P2 — Title overlay and custom UI (implemented)

1. Optional image-title overlay with configuration fields and default off.
2. Plugin-parameter UI text extraction and exact-label rendering for custom menus/CBR-style windows.
3. Targeted Ticker/FTKR and save-window hooks.
4. Metadata/full-corpus reference export.
5. Version bump to 3.5.0.

P3 validation remains: real-game screenshots, save-screen check, final review, and release notes.

### P3 — Validation and release (complete)

- Real-game checks: title, speaker box, dialogue, menu labels, save-title hook installation, and title overlay screenshot.
- Whole-repository review: critical-only verdict CLEAN from two independent reviewers.
- Version 3.5.0.
- Scope limit documented: standard `command111` conditionals use raw names; scripts/plugin commands see translated display text and should use IDs or `_name`.

---

## 9. Open decisions

1. **Image title:** do you want a translated text overlay on the image title screen, or is translating the window/document title and save-slot title enough for now? Recommended: P0 document.title + save slots; P1 opt-in overlay.
2. **Export values:** keep empty values (proven with the current tool) or match MTool’s identity convention? Recommended: empty default, identity option.
3. **Export granularity:** cleaned fragments + code-free blocks (recommended) or fragments only? Fragments maximize MTool compatibility; code-free blocks preserve context for the 263 blocks that have none.
4. **Merge unsure results:** automatically merge the 8 extra entries from `_unsured_result.json`, or show them for approval? Recommended: show for approval.
5. **Custom plugin patches:** which games/plugins matter most? Recommended: only add targeted patches after reproducing a failure in a real game.
