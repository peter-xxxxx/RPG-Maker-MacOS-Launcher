<img src="icon.png" height="200">

# RPG-Maker-MacOS-Launcher

> [!NOTE]
> Development for this launcher has been completed! Only bugfixes will be released if needed.<br>
> Check out [Xenolauncher](https://github.com/m5kro/Xenolauncher) for a more versatile launcher!

A launcher to natively play RPG Maker MV, MZ, XP, VX, VX Ace, 2000, and 2003 games on macOS!<br>
Custom MKXP-Z fork: https://github.com/m5kro/mkxp-z/tree/RPGM-Launcher<br>

## Features

- Launch RPG Maker MV/MZ games with NW.js and older games with MKXP-Z or EasyRPG.
- Download and manage runtime versions, extract `game_en.exe`, optimize game folders, and install optional patches.
- Add an in-game cheat menu, disable protection/network access, and patch `Bitmap.getPixel` crashes.
- Export a game and its active configuration as a standalone macOS app.
- Translate standard MV/MZ text at runtime without modifying the game's data files.

## Translation (RPG Maker MV/MZ)

The launcher can inject a flat JSON translation dictionary into MV/MZ games
without modifying the game's `data/*.json` files. Translations are stored inside
the game folder, so they travel with the game and are included when you export a
standalone app while translation is active.

### What gets translated

- Database names, descriptions, and standard messages: actors, classes, skills,
  items, weapons, armors, enemies, and states.
- System text: game title, currency unit, element/skill/weapon/armor/equip type
  names, and standard terms.
- Map names, map display names, event dialogue, choices, scrolling text, MZ
  speaker names, and YEP name tags.
- Display-time UI: speaker name boxes, menu command labels, save-slot titles,
  the window/document title, and plugin-parameter labels that look like display
  text.
- An optional text overlay for image-only title screens.

### Translate a game

1. Select an MV/MZ game and click **Translate Game...**.
2. Click **Export ManualTransFile.json...** and save the file. The default clean
   export uses line/fragment keys without RPG Maker control codes or YEP name
   tags.
3. Translate the JSON values with any external editor or translation tool. The
   default export uses identity values (`source: source`) for untranslated
   entries, which tools such as MTool understand. The launcher treats identity
   and empty values as untranslated.
4. Click **Load Translated JSON...**, select the translated file, and restart the
   game from the launcher. If a translation is already installed, choose
   **Merge** to keep existing entries or **Replace** to use only the loaded file.
5. Use **Coverage Report...** to see translated/missing database display names,
   and **Export Missing Text JSON...** to export only the untranslated strings.
   Translate that partial file, then load it with **Merge**.

When loading, the launcher reinserts the original control codes, name tags, and
placeholders at the exact runtime keys. Empty values are reported and ignored;
invalid entries are skipped with warnings.

### Advanced options

- **Use identity values for untranslated keys (MTool mode)** — on by default.
  Turn it off if your tool expects blank values instead of `source: source`.
- **Export exact block keys (legacy/troubleshooting)** — keeps full dialogue
  blocks and control codes in the keys. If you use this mode, do not change the
  keys or any codes inside them.
- **Font face** — optional font override for translated text, for example
  `PingFang SC`. Useful when the game font lacks target-language glyphs.
- **Wrap long dialogue text** — wraps translated dialogue to the message window
  width. On by default.
- **Write translation debug logs** — logs lookup details to the NW.js console
  and exposes `window.RPGMLauncherTranslate.getStats()` for hit/miss counters.
- **Image title overlay** — draws the translated game title over an image-only
  title screen. `X offset`/`Y position` of `0` use automatic placement;
  background opacity `0` is transparent.
- **Export Metadata JSON...** — exports editor-only names (events, switches,
  variables, troops, animations, tilesets) as an identity-value reference file.
  It is not a runtime translation and should not be installed.

### Files and safety

Translation files are stored in the game's detected plugin folder:

- MV: `<game>/www/js/plugins`
- MZ: `<game>/js/plugins`

Installed files:

```text
RPGM_Launcher_Translate.js
RPGM_Launcher_Translate.json
RPGM_Launcher_Translate.config.json
RPGM_Launcher_Translate.state.json
```

The launcher also adds one `RPGM_Launcher_Translate` loader entry to
`js/plugins.js` and keeps a `plugins.js.rpgm-launcher.bak` backup. Installs are
staged and committed atomically; an interrupted install is rolled back on the
next launch. For translation, the launcher's Application Support folder contains
only a path-only cleanup index, never dictionary text.

**Remove installed translation** deletes the plugin, dictionary, config, state,
and loader entry. Removing or updating the game may also remove them; re-export
and re-load the translation after a game update if needed.

### Supported scope and limitations

- Standard MV/MZ layouts only. Games with symlinked `js`, `data`, or `plugins`
  folders are rejected for safety.
- Script text (`355`/`655`), plugin commands (`356`/`357`), note tags, and
  arbitrary plugin parameters are not translated. Plugin parameters are only
  translated when they look like display labels.
- Images, audio, and video are not translated. Use the title overlay for
  image-only title screens.
- The dictionary is flat, so the same source string gets the same translation in
  every context.
- Translation happens at display time. Custom plugins that read raw data
  directly or cache text before the hooks run may bypass it.
- Standard event conditionals (`command111`) compare raw names. Script commands
  see translated display text; custom scripts should compare actor IDs or
  `_name`.
- A game restart is required after loading or changing a translation. There is
  no live hot-reload.
- Packed, encrypted, or non-standard data layouts are not supported.
- Long translations may overflow custom UI windows. Enable wrapping, shorten UI
  strings, or set a smaller font/title overlay size.

### Troubleshooting

- **Translate Game... is disabled** — the selected folder is not a standard
  MV/MZ game, or its `js`/`data`/`plugins` path contains a symlink.
- **Translation did not apply** — open the dialog and confirm the status is
  **Active**, the enable checkbox is checked, and the dictionary has entries.
  Restart the game from the launcher, not from an already-running process.
- **Some text is still untranslated** — use **Export Missing Text JSON...**,
  translate it, and load with **Merge**. Script/plugin-command text and custom
  UI are expected to remain untranslated.
- **Speaker names or menu labels remain raw** — export missing text and merge it.
  Custom name-box/menu plugins may need a targeted compatibility patch.
- **The title screen still shows an image** — enable the image title overlay
  under **Advanced**.
- **Text overflows a window** — enable wrapping, use a shorter translation, or
  set a smaller font/title overlay size.
- **The game fails to start after enabling translation** — open the dialog,
  click **Remove installed translation**, and start the game untranslated. The
  launcher also disables a translation it cannot validate and shows a warning
  instead of blocking the launch.
- **Conditionals or scripts broke** — do not compare translated display names;
  use actor IDs or `_name`. Report the game/plugin if standard conditionals fail.
- **An export overwrote your work** — look for a timestamped
  `ManualTransFile.*.json.bak` backup next to the export.
- **The translation file was rejected** — it must be a flat JSON object with
  string keys and string values. Arrays and nested objects are not supported.

## Test the launcher from source (macOS)

Use the bundled test environment to try the translation feature against a real
game without building the app:

1. Install [mise](https://mise.jdx.dev/getting-started.html).
2. Run `./scripts/setup_translation_test_env.sh` to install the pinned Python
   3.11 version, create `.venv`, and install PySide6 plus the launcher
   dependencies.
3. Run `./scripts/run_translation_test_app.sh`.
4. Select an MV/MZ game, click **Translate Game...**, export
   `ManualTransFile.json`, translate the JSON values, load it, and restart the
   game.
5. Run `./scripts/test.sh` to run the Python backend tests and the Node.js
   runtime-plugin tests (Node.js 18+ required).

On macOS you can also double-click `scripts/Run Translation Test App.command`
from Finder. The `.venv` folder is local-only and ignored by Git.

## Development

See [`AGENTS.md`](AGENTS.md) for architecture, invariants, test commands, and
the definition of done. Coding agents should also load the
[`test-translation`](.opencode/skills/test-translation/SKILL.md) skill when
working on translation code.

Translation design background: [original design](docs/translation/translation-plan-v1.md)
and [follow-up plan](docs/translation/translation-followup-plan-v2.md).

> [!TIP]
> Instructions video by [xem](https://forums.rpgmakerweb.com/index.php?members/xem.159435/)/[pgm](https://www.youtube.com/channel/UCWTsqPj8SD8rHL9gsBZI59Q): https://www.youtube.com/watch?v=NIRiKIjhXHk

# Donations

Ethereum Wallet:<br>
0x60F040444876EB5996DfA04CB8d8fc8D2aB96CF7

# How it works

1. Downloads NW.js, MKXP-Z, and EasyRPG.
2. Checks `package.json`, `Game.ini`, or `RTP_RT`.
3. Unpacks `game_en.exe` if it exists.
4. Points NW.js, MKXP-Z, or EasyRPG to the game directory.
5. Launches the game.

# How to build

If the [prebuilt releases](https://github.com/m5kro/RPG-Maker-MacOS-Launcher/releases/latest) don't work, or you'd like to add a feature, here are the build instructions. Recommended Python version is 3.11.

### Native version (x64 or arm64, matching your system)

1. Clone the repo:

   ```sh
   git clone https://github.com/m5kro/RPG-Maker-MacOS-Launcher
   cd RPG-Maker-MacOS-Launcher
   ```

2. Create and activate a virtual environment:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install packages:

   ```sh
   pip install PySide6-Essentials evbunpack requests chardet PyInstaller
   ```

4. Build the app with your architecture's spec file:

   ```sh
   pyinstaller RPG-Maker-Launcher-arm64.spec   # Apple Silicon
   # or
   pyinstaller RPG-Maker-Launcher-x64.spec     # Intel
   ```

5. Find the app in the `dist` folder.

6. Deactivate and remove the build environment if you no longer need it:

   ```sh
   deactivate
   rm -rf .venv
   ```

### Universal2 version (works on both x64 and arm64)

1. Download and install the Universal2 version of Python: https://www.python.org/downloads/macos/
2. Clone the repo and create a virtual environment:

   ```sh
   git clone https://github.com/m5kro/RPG-Maker-MacOS-Launcher
   cd RPG-Maker-MacOS-Launcher
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install packages:

   ```sh
   pip install PySide6-Essentials evbunpack requests chardet PyInstaller
   ```

4. Build the app:

   ```sh
   pyinstaller RPG-Maker-Launcher-Universal2.spec
   ```

5. Find the app in the `dist` folder.

6. Deactivate and clean up:

   ```sh
   deactivate
   rm -rf .venv
   ```

# Credits and Special Thanks

Donators:<br>
lecrolonk <br>
athanadoc05 <br>
<br>
Credit to mkxp-z maintainers for mkxp-z <br>
https://github.com/mkxp-z/mkxp-z <br>
<br>
Credit to emerladCoder for cheat menu <br>
https://github.com/emerladCoder/RPG-Maker-MV-Cheat-Menu-Plugin <br>
<br>
Credit to SaveEditorOnline for the save editor <br>
https://saveeditor.online <br>
<br>
Credit to SynthFont developers for the Soundfont <br>
https://musical-artifacts.com/artifacts/841 <br>
<br>
Credit to Orochimarufan for the Kawariki Patches <br>
https://github.com/Orochimarufan/Kawariki <br>
<br>
Credit to EasyRPG team for EasyRPG Player<br>
https://easyrpg.org/player/downloads/#release-macos<br>
<br>
