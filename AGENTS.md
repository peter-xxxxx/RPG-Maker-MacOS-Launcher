# AGENTS.md

Instructions for coding agents and contributors. Read this before changing the
repository. For translation test work, also load the
[`test-translation`](.opencode/skills/test-translation/SKILL.md) skill.

## Project overview

`RPG-Maker-MacOS-Launcher` is a macOS PySide6 desktop app that launches RPG
Maker games without requiring the original Windows runtime:

- **MV/MZ** games run under NW.js.
- **XP/VX/VX Ace** games run under the bundled MKXP-Z fork.
- **2000/2003** games run under EasyRPG Player.

The launcher also downloads runtimes, patches optional features (Cheat Menu,
protection scripts, `Bitmap.getPixel`), exports standalone apps, and installs a
runtime translation plugin for MV/MZ games.

The translation feature is display-time only. It installs a JavaScript plugin
and a flat JSON dictionary into the game's `js/plugins` folder. It must never
rewrite `data/*.json`, event command lists, save data, or raw actor names.

## Quick start

Requirements: macOS, [mise](https://mise.jdx.dev/getting-started.html), and
Node.js 18+ for the JavaScript tests.

```sh
./scripts/setup_translation_test_env.sh   # Python 3.11 venv + launcher deps
./scripts/run_translation_test_app.sh      # run the app from source
./scripts/test.sh                          # Python + JavaScript tests
```

Build instructions are in [`README.md`](README.md#how-to-build). The PyInstaller
specs are macOS-specific.

## Repository map

| Path | Purpose |
| --- | --- |
| `main.py` | Main launcher window, runtime downloads, game launch, patching, standalone export. Calls into the translation backend before launch/export. |
| `translation.py` | Standard-library translation backend: layout detection, scanning, clean export/compile, validation, install/remove, launch preparation, cleanup. No Qt imports. |
| `translation_dialog.py` | PySide6 "Translate Game" dialog and its export/load/status controls. |
| `RPGM_Launcher_Translate.js` | Runtime translation plugin copied into each game. ES5 because it must run on older NW.js builds. |
| `tests/test_translation.py` | Python `unittest` suite for the backend, including install/rollback and path-safety tests. |
| `tests/test_translate_plugin.js` | Minimal Node VM harness for the runtime plugin. |
| `tests/test_translate_plugin_scenarios.js` | Scenario harness covering MV/MZ runtime hooks, titles, speaker boxes, wrapping, and fallbacks. |
| `scripts/` | Source-run setup, test app launcher, and the unified test script. |
| `*.spec` | PyInstaller build definitions. Runtime assets must be listed in all three variants. |
| `.opencode/skills/test-translation/` | Agent workflow for running and extending translation tests. |
| `docs/translation/` | Translation design and implementation history. Useful context, not current instructions. |

Other top-level JavaScript files (`Cheat_Menu.js`, `bg.js`, `disable-child.js`,
`disable-net.js`) are launcher patch assets.

## Translation runtime flow

1. `translation.detect_game_layout()` finds the MV/MZ app root (`www` for MV,
   root for MZ), `js`, `data`, and plugin directory.
2. `translation.scan_game()` reads standard database, system, map, common-event,
   troop, and selected plugin-label strings.
3. The dialog exports a flat `{source: translation}` JSON file. The default
   "clean" export removes recognized control codes and splits text fragments;
   the compiler reconstructs exact runtime keys when the file is loaded.
4. `translation.load_translation()` loads the flat dictionary; validation
   reports empty, identity, invalid, and unknown entries.
5. `translation.install_translation()` atomically writes the plugin, dictionary,
   config, and state into `<js>/plugins`, then adds one loader entry to
   `plugins.js` with a backup and transaction manifest.
6. `translation.prepare_for_launch()` runs before every launch and standalone
   export. It repairs or rolls back interrupted installs, removes stale loader
   entries, and disables translation if the dictionary/config is invalid.
7. The plugin reads the dictionary at runtime and translates display text while
   preserving raw values for saves and conditionals.
8. `translation.remove_translation()` removes the loader entry and artifacts;
   the Application Support cleanup index stores paths only, never game text.

Artifacts inside the game:

```text
js/plugins/RPGM_Launcher_Translate.js
js/plugins/RPGM_Launcher_Translate.json
js/plugins/RPGM_Launcher_Translate.config.json
js/plugins/RPGM_Launcher_Translate.state.json
js/plugins/plugins.js.rpgm-launcher.bak
```

## Invariants: do not break these

- **Never modify game data permanently.** Translation is display-time only.
  Raw actor names, `$dataSystem.gameTitle`, event lists, and save data stay
  untouched. Standard `command111` conditionals compare raw names, and scripts
  should use actor IDs or `_name`.
- **Keep `translation.py` standard-library-only.** `chardet` is optional. Qt
  belongs in `translation_dialog.py`/`main.py`, never in the backend.
- **Keep the runtime plugin ES5 and defensive.** Use `var` and `function`, no
  `let`/`const`/arrow functions/template literals. Every hook must fail open:
  if loading or translating fails, the game must keep running with raw text.
- **Preserve the export/compile contract.** Clean export strips recognized
  control codes and YEP name tags; the compiler reinserts them. Identity values
  (`key == value`) are not effective translations. Preserve placeholders such
  as `%1`, page separators (`\f`), and MV/MZ-specific control codes.
- **Keep all translation state in the game folder.** For translation, the
  launcher's Application Support directory may contain only the path-only
  cleanup index. Do not add a dictionary/text cache there.
- **Do not weaken filesystem safety.** Writes are staged and atomically swapped;
  the transaction manifest, target allowlist, symlink checks, and path checks
  are security-critical. Games with symlinked `js`, `data`, or `plugins` are
  intentionally unsupported.
- **Use targeted display hooks.** Do not add a global `Window_Base.drawText`
  replacement. It corrupts numeric UI and breaks plugins.
- **Update all three PyInstaller specs** (`arm64`, `x64`, `Universal2`) when
  adding a runtime data file.
- **Preserve compatibility.** Loading is merge-friendly. Unknown entries are not
  installed, but they must be surfaced as warnings rather than dropped silently.
  Existing dictionaries and MTool-style line keys must keep working.

## Testing

Run everything before committing:

```sh
./scripts/test.sh
```

Focused commands:

```sh
# Python backend
.venv/bin/python -m unittest tests.test_translation.ScanTests -v
.venv/bin/python -m unittest tests.test_translation -k clean_export -v

# Runtime plugin (Node)
node tests/test_translate_plugin.js
node tests/test_translate_plugin_scenarios.js
```

Guidelines:

- Add a regression test for every behavior change. Python tests build synthetic
  MV/MZ games in temporary directories; JS scenarios run the real plugin in a
  fresh VM context.
- Test both MV and MZ when touching runtime hooks, control codes, or plugin
  paths.
- Test failure paths: invalid/missing dictionary, disabled config, missing
  files, interrupted transactions, symlinked layouts, and read-only folders.
- For manual UI checks, use a **copy** of a test game. Never experiment on a
  user's real game folder or saves.
- Run the full suite again after focused tests pass.

The `test-translation` skill contains the detailed manual verification
checklist and debugging commands.

## Coding conventions

- Python 3.11, four-space indentation, type hints on public functions. Follow
  the surrounding style; do not reformat unrelated code.
- Keep public backend functions usable without Qt so tests can call them on any
  platform.
- User-facing errors should state what failed and what the user can do next.
- JavaScript in `RPGM_Launcher_Translate.js` must remain ES5-compatible. The
  Node harnesses may use modern JavaScript because they never ship in a game.
- Update `README.md` for user-visible changes and this file (plus the skill) for
  workflow changes.

## Definition of done

- The change is focused and does not include unrelated reformatting.
- Tests cover the new behavior and the full `./scripts/test.sh` suite passes.
- Manual UI/game checks are done for runtime or dialog changes.
- No `.venv`, caches, exported game JSON, game data, logs, or secrets are
  staged.
- README/AGENTS/skill documentation is updated when commands, behavior, or
  limitations change.
- Commit messages use an imperative subject (for example, "Add translation
  coverage report").
