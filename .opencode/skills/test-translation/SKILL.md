---
name: Test Translation
description: Run and extend the Python and JavaScript tests for MV/MZ runtime translation, then verify the Translate Game dialog and in-game behavior.
---

# Test the translation feature

Use this workflow when changing `translation.py`, `translation_dialog.py`,
`RPGM_Launcher_Translate.js`, or the translation integration in `main.py`.
Read `AGENTS.md` first for architecture and invariants.

## 1. Run the automated suite

```sh
./scripts/test.sh
```

This runs the Python `unittest` suite and both Node plugin harnesses. If the
venv is missing, create it first:

```sh
./scripts/setup_translation_test_env.sh
```

Focused Python tests:

```sh
.venv/bin/python -m unittest tests.test_translation.ScanTests -v
.venv/bin/python -m unittest tests.test_translation.ExportSafetyTests -v
.venv/bin/python -m unittest tests.test_translation.CleanExportCompilerTests -v
.venv/bin/python -m unittest tests.test_translation -k clean_export -v
```

Focused plugin tests:

```sh
node tests/test_translate_plugin.js
node tests/test_translate_plugin_scenarios.js
```

When adding a runtime scenario, register it in the `tests` array at the bottom
of `tests/test_translate_plugin_scenarios.js`, use `makeGame()` and
`makeContext()`, and assert both the translated display value and the unchanged
raw value.

## 2. Check the required coverage

For a backend change, verify tests cover:

- MV (`www/js/rpg_core.js`, `www/data`) and MZ (`js/rmmz_core.js`, `data`)
  layouts.
- Clean export, identity-value handling, merge-on-load, missing-only export, and
  unknown/empty/invalid entries.
- Control codes, YEP name tags, `%n` placeholders, `\f` page separators, CJK
  paths, BOM/CRLF, and one-character database names.
- Install/reload/remove, interrupted transactions and rollback, stale loader
  entries, read-only destinations, and symlinked layouts.
- Disabled/missing/corrupt dictionary and config behavior: the game must still
  launch untranslated.

For a runtime plugin change, verify tests cover:

- Dialogue blocks and line fallback; choices, scrolling text, speaker names.
- MV and MZ speaker/name boxes; name input preserving raw names.
- Actor-name conditionals (`command111`) still see raw names.
- Window/document title, save-slot titles, menu/UI labels, and the optional
  image-title overlay.
- Text wrapping, font fallback, page separators, and exception fallback.
- Plugin coexistence: pre-existing wrappers remain installed and are restored.

For a dialog/UI change, verify buttons enable/disable correctly for unsupported
games, empty dictionaries, pending transactions, and missing files.

## 3. Manual app check

Use a disposable copy of a test game, never a real game folder or saves.

```sh
./scripts/run_translation_test_app.sh
```

1. Select an MV or MZ game. Confirm **Translate Game...** is enabled and the
   dialog shows the detected engine and plugin path.
2. Export `ManualTransFile.json` into a temp directory. Confirm it is a flat
   object with identity values and no recognized control codes in the default
   clean mode.
3. Translate two or three values with recognizable text, load the file, and
   choose **Merge**. Confirm the status becomes **Active** and the entry count
   updates.
4. Start the game from the launcher. Verify dialogue, choices, speaker names,
   menu terms, and the window title. Confirm saves and actor-name conditionals
   still work.
5. Reopen the dialog: check **Coverage Report...** and
   **Export Missing Text JSON...**. Load the partial file with **Merge**.
6. If the game uses an image-only title, enable the title overlay under
   **Advanced**, set the font/position, restart, and verify placement.
7. Click **Remove installed translation** and confirm the game starts
   untranslated and the loader entry/artifacts are gone.

For debugging, enable **Write translation debug logs** in Advanced, open the
NW.js console, and inspect:

```js
window.RPGMLauncherTranslate.getStats()
```

The result includes `entryCount`, `hits`, `misses`, `blockHits`, `lineHits`,
`pageHits`, `speakerNames`, `uiLabels`, `titleOverlay`, and installed hook names.

## 4. Safety checks

- Do not commit `.venv`, `__pycache__`, `.ruff_cache`, `.DS_Store`, exported
  `ManualTransFile*.json`, game data, logs, or screenshots with personal paths.
- Do not test by editing `data/*.json`, `plugins.js` by hand, or save files. Use
  the launcher UI.
- If a test leaves a game in a pending transaction state, keep the game copy and
  inspect the dialog status; do not delete transaction files manually.
- Finish with `./scripts/test.sh` and `git status` before reporting success.
