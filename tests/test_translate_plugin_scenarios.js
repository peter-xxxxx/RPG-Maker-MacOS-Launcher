// Scenario harness for RPGM_Launcher_Translate.js.
// Each scenario runs the plugin in a fresh VM context with a fresh game dir.
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { pathToFileURL } = require("url");

const repo = path.resolve(__dirname, "..");
const pluginPath = path.join(repo, "RPGM_Launcher_Translate.js");
const pluginCode = fs.readFileSync(pluginPath, "utf8");
const roots = [];

process.on("exit", function () {
  for (const root of roots) {
    try {
      fs.rmSync(root, { recursive: true, force: true });
    } catch (error) {
      // Best-effort cleanup.
    }
  }
});

function makeGame(options) {
  const settings = options || {};
  const engine = settings.engine || "MZ";
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rpgm translate scenario "));
  roots.push(root);
  const gameName = settings.gameName || "ゲーム タイトル";
  const gameDir = path.join(root, gameName);
  const appRoot = engine === "MV" ? path.join(gameDir, "www") : gameDir;
  fs.mkdirSync(appRoot, { recursive: true });
  const plugins = path.join(appRoot, "js", "plugins");
  fs.mkdirSync(plugins, { recursive: true });
  fs.mkdirSync(path.join(appRoot, "data"), { recursive: true });
  fs.writeFileSync(path.join(appRoot, "index.html"), "<html></html>", "utf8");
  fs.writeFileSync(
    path.join(appRoot, "js", engine === "MZ" ? "rmmz_core.js" : "rpg_core.js"),
    "// core\n",
    "utf8"
  );
  const config = settings.config !== undefined
    ? settings.config
    : { enabled: true, fontFace: "", wrap: true, debug: false };
  const dictionary = settings.dictionary !== undefined
    ? settings.dictionary
    : {};
  if (settings.configText !== undefined) {
    fs.writeFileSync(
      path.join(plugins, "RPGM_Launcher_Translate.config.json"),
      settings.configText,
      "utf8"
    );
  } else if (config !== null) {
    fs.writeFileSync(
      path.join(plugins, "RPGM_Launcher_Translate.config.json"),
      JSON.stringify(config, null, 2),
      "utf8"
    );
  }
  if (settings.dictionaryText !== undefined) {
    fs.writeFileSync(
      path.join(plugins, "RPGM_Launcher_Translate.json"),
      settings.dictionaryText,
      "utf8"
    );
  } else if (dictionary !== null) {
    fs.writeFileSync(
      path.join(plugins, "RPGM_Launcher_Translate.json"),
      JSON.stringify(dictionary, null, 2),
      "utf8"
    );
  }
  return { root, appRoot, plugins, engine };
}

function makeContext(game, options) {
  const settings = options || {};
  const context = {};
  context.window = context;
  context.globalThis = context;
  context.console = settings.console || console;
  context.document = {};
  context.location = {
    href: pathToFileURL(path.join(game.appRoot, "index.html")).href,
    pathname: path.join(game.appRoot, "index.html"),
    protocol: "file:"
  };
  context.Utils = { RPGMAKER_NAME: game.engine };
  context.PluginManager = { parameters: function () { return {}; } };
  context.ImageManager = { faceWidth: 144, iconWidth: 32 };
  context.Graphics = { boxWidth: 816, boxHeight: 624 };
  context.TextManager = { currencyUnit: "G" };
  context.ColorManager = {};

  if (settings.xhr) {
    context.require = undefined;
    context.XMLHttpRequest = function () {
      this.status = 0;
      this.responseText = "";
    };
    context.XMLHttpRequest.prototype.open = function (method, url) {
      this.url = url;
    };
    context.XMLHttpRequest.prototype.overrideMimeType = function () {};
    context.XMLHttpRequest.prototype.send = function () {
      try {
        this.responseText = fs.readFileSync(path.join(game.appRoot, this.url), "utf8");
        this.status = 200;
      } catch (error) {
        this.status = 404;
        this.responseText = "";
      }
    };
  } else {
    context.require = require;
  }

  function DataManager() {}
  DataManager.onLoad = function () {};
  context.DataManager = DataManager;

  function Game_Message() {
    this._texts = [];
    this._choices = [];
    this._speakerName = "";
  }
  Game_Message.prototype.add = function (text) {
    this._texts.push(text);
  };
  Game_Message.prototype.allText = function () {
    return this._texts.join("\n");
  };
  Game_Message.prototype.setChoices = function (choices) {
    this._choices = choices;
  };
  Game_Message.prototype.setSpeakerName = function (name) {
    this._speakerName = name;
  };
  context.Game_Message = Game_Message;

  function Game_Actor() {}
  Game_Actor.prototype.name = function () {
    return this._name;
  };
  Game_Actor.prototype.setName = function (name) {
    this._name = name;
  };
  Game_Actor.prototype.nickname = function () {
    return this._nickname || "";
  };
  Game_Actor.prototype.profile = function () {
    return this._profile || "";
  };
  context.Game_Actor = Game_Actor;

  function Game_Interpreter() {}
  Game_Interpreter.prototype.command111 = function () {
    this.seenName = this.actor ? this.actor.name() : null;
    this.seenNickname = this.actor ? this.actor.nickname() : null;
    this.seenProfile = this.actor ? this.actor.profile() : null;
    this.seenItem = context.$dataItems && context.$dataItems[1] ?
      context.$dataItems[1].name : null;
    return true;
  };
  Game_Interpreter.prototype.command355 = function () {
    this.seenName = this.actor ? this.actor.name() : null;
    this.seenItem = context.$dataItems && context.$dataItems[1] ?
      context.$dataItems[1].name : null;
    return true;
  };
  context.Game_Interpreter = Game_Interpreter;

  function Window_Base() {}
  Window_Base.prototype.standardFontFace = function () {
    return "GameFont";
  };
  Window_Base.prototype.drawText = function (text) {
    this.drawnText = text;
  };
  Window_Base.prototype.drawTextEx = function (text) {
    this.drawnText = text;
  };
  Window_Base._faceWidth = 144;
  context.Window_Base = Window_Base;

  function Bitmap(width, height) {
    this.width = width;
    this.height = height;
    this.drawn = [];
  }
  Bitmap.prototype.measureTextWidth = function (text) {
    return String(text).length * 12;
  };
  Bitmap.prototype.drawText = function (text) {
    this.drawn.push(text);
  };
  Bitmap.prototype.fillRect = function () {};
  context.Bitmap = Bitmap;

  function Sprite(bitmap) {
    this.bitmap = bitmap;
  }
  context.Sprite = Sprite;

  function Window_Command() {
    this._list = [];
  }
  Window_Command.prototype.drawItem = function (index) {
    this.drawnName = this._list[index].name;
  };
  context.Window_Command = Window_Command;

  function Game_System() {}
  Game_System.prototype.mainFontFace = function () {
    return "GameFont, sans-serif";
  };
  context.Game_System = Game_System;

  function Window_Message() {}
  Window_Message.prototype.startMessage = function () {
    this.seen = context.$gameMessage.allText();
  };
  context.Window_Message = Window_Message;

  function Window_ScrollText() {}
  Window_ScrollText.prototype.startMessage = function () {
    this.seen = context.$gameMessage.allText();
  };
  Window_ScrollText.prototype.baseTextRect = function () {
    return { x: 8, y: 0, width: 140, height: 200 };
  };
  context.Window_ScrollText = Window_ScrollText;

  function Window_Ticker() {}
  Window_Ticker.prototype.drawTextEx = function (text) {
    this.drawnText = text;
  };
  context.Window_Ticker = Window_Ticker;

  context.TickerManager = {
    show: function (text) {
      this.drawnText = text;
    },
  };

  if (game.engine === "MV") {
    function Window_NameEdit(actor, maxLength) {
      this.initialize(actor, maxLength);
    }
    Window_NameEdit.prototype.initialize = function (actor, maxLength) {
      this._actor = actor;
      this._maxLength = maxLength;
      this._name = actor.name().slice(0, maxLength);
    };
    Window_NameEdit.prototype.name = function () {
      return this._name;
    };
    context.Window_NameEdit = Window_NameEdit;
  } else {
    function Window_NameEdit(actor, maxLength) {
      this.initialize({});
      this.setup(actor, maxLength);
    }
    Window_NameEdit.prototype.initialize = function () {
      this._actor = null;
      this._maxLength = 0;
      this._name = "";
    };
    Window_NameEdit.prototype.setup = function (actor, maxLength) {
      this._actor = actor;
      this._maxLength = maxLength;
      this._name = actor.name().slice(0, maxLength);
    };
    Window_NameEdit.prototype.name = function () {
      return this._name;
    };
    context.Window_NameEdit = Window_NameEdit;
  }

  function Scene_Name() {}
  Scene_Name.prototype.onInputOk = function () {
    this._actor.setName(this._editWindow.name());
    this.popped = true;
  };
  context.Scene_Name = Scene_Name;

  function Scene_Title() {}
  Scene_Title.prototype.start = function () {
    context.document.title = context.$dataSystem.gameTitle;
  };
  Scene_Title.prototype.drawGameTitle = function () {
    this.drawnTitle = context.$dataSystem.gameTitle;
  };
  context.Scene_Title = Scene_Title;
  Scene_Title.prototype.createForeground = function () {
    this.created = true;
  };
  Scene_Title.prototype.addChild = function (child) {
    this.childAdded = child;
  };

  function Scene_Boot() {}
  Scene_Boot.prototype.updateDocumentTitle = function () {
    context.document.title = context.$dataSystem.gameTitle;
  };
  context.Scene_Boot = Scene_Boot;

  function Window_SavefileStatus() {}
  Window_SavefileStatus.prototype.drawContents = function (info) {
    this.drawnTitle = info.title;
  };
  context.Window_SavefileStatus = Window_SavefileStatus;

  function Window_SavefileList() {}
  Window_SavefileList.prototype.drawContents = function (info) {
    this.drawnTitle = info.title;
  };
  context.Window_SavefileList = Window_SavefileList;

  function Window_SaveFileList() {}
  Window_SaveFileList.prototype.drawFileInfo = function (info) {
    this.drawnTitle = info.title;
  };
  context.Window_SaveFileList = Window_SaveFileList;

  function Window_SaveInfo() {
    this._info = null;
    this._saveContents = null;
  }
  Window_SaveInfo.prototype.drawGameTitle = function (dy) {
    this.drawnTitle = this._info ? this._info.title : null;
    return dy;
  };
  Window_SaveInfo.prototype.drawPartyNames = function (dy) {
    this.drawnNames = [];
    var actors = this._saveContents && this._saveContents.actors._data;
    if (actors) {
      for (var id in actors) {
        if (Object.prototype.hasOwnProperty.call(actors, id)) {
          this.drawnNames.push(actors[id]._name);
        }
      }
    }
    return dy;
  };
  context.Window_SaveInfo = Window_SaveInfo;

  if (game.engine === "MV") {
    function Window_NameBox() {}
    Window_NameBox.prototype.refresh = function (text, position) {
      this.refreshed = text;
      this.position = position;
    };
    context.Window_NameBox = Window_NameBox;
  } else {
    function Window_NameBox() {
      this._name = "";
    }
    Window_NameBox.prototype.setName = function (name) {
      this._name = name;
    };
    Window_NameBox.prototype.refresh = function () {
      this.refreshed = this._name;
    };
    context.Window_NameBox = Window_NameBox;
  }

  context.$gameMessage = new Game_Message();
  context.$gameSystem = new Game_System();
  context.$dataActors = [null, { name: "太郎", nickname: "", profile: "" }];
  context.$dataClasses = [null, { name: "戦士" }];
  context.$dataSkills = [null, { name: "炎", description: "", message1: "", message2: "" }];
  context.$dataItems = [null, { name: "薬", description: "回復" }];
  context.$dataWeapons = [null, { name: "剣", description: "" }];
  context.$dataArmors = [null, { name: "鎧", description: "" }];
  context.$dataEnemies = [null, { name: "スライム" }];
  context.$dataStates = [null, { name: "毒", message1: "" }];
  context.$dataTroops = [];
  context.$dataCommonEvents = [];
  context.$dataTilesets = [];
  context.$dataAnimations = [];
  context.$dataMapInfos = [null, { name: "マップ" }];
  context.$dataMap = { displayName: "マップ", events: [] };
  context.$dataSystem = {
    gameTitle: "ゲーム",
    currencyUnit: "G",
    elements: ["", "炎"],
    skillTypes: [],
    weaponTypes: [],
    armorTypes: [],
    equipTypes: [],
    terms: {
      basic: ["レベル"],
      commands: ["戦う", null],
      params: ["最大HP"],
      messages: { actorDamage: "%1は%2ダメージ" }
    }
  };

  if (settings.beforeLoad) {
    settings.beforeLoad(context);
  }
  context.vm = vm.createContext(context);
  vm.runInContext(pluginCode, context.vm, { filename: pluginPath });
  return context;
}

function baseDictionary() {
  return {
    "こんにちは": "Hello",
    "世界": "World",
    "こんにちは\n世界": "Hello\nWorld",
    "薬": "Potion",
    "回復": "Restore",
    "はい": "Yes",
    "いいえ": "No",
    "太郎": "Taro",
    "ボブ": "Bob",
    "戦士": "Warrior",
    "炎": "Fire",
    "レベル": "Level",
    "戦う": "Fight",
    "最大HP": "Max HP",
    "%1は%2ダメージ": "%1 took %2 damage",
    "ゲーム": "Game",
    "マップ": "Map",
    "G": "Gold",
    "これはとても長い文章です": "This is a very long sentence that should be wrapped."
  };
}

function testDisabledConfig() {
  const game = makeGame({
    config: { enabled: false, fontFace: "", wrap: true, debug: false },
    dictionary: baseDictionary()
  });
  const context = makeContext(game);
  assert.strictEqual(context.RPGMLauncherTranslate, undefined);
  const items = context.$dataItems;
  context.DataManager.onLoad(items);
  assert.strictEqual(items[1].name, "薬");
}

function testMissingDictionary() {
  const game = makeGame({
    config: { enabled: true, fontFace: "", wrap: true, debug: false },
    dictionary: null
  });
  const context = makeContext(game);
  assert.strictEqual(context.RPGMLauncherTranslate, undefined);
}

function testMvNameInput() {
  const game = makeGame({ engine: "MV", dictionary: baseDictionary() });
  const context = makeContext(game);
  const actor = new context.Game_Actor();
  actor._name = "太郎";
  const edit = new context.Window_NameEdit(actor, 10);
  assert.strictEqual(edit._name, "Taro");
  const scene = new context.Scene_Name();
  scene._actor = actor;
  scene._editWindow = edit;
  scene.onInputOk();
  assert.strictEqual(actor._name, "太郎");
  assert.strictEqual(scene.popped, true);

  const actor2 = new context.Game_Actor();
  actor2._name = "太郎";
  const edit2 = new context.Window_NameEdit(actor2, 10);
  edit2._name = "NewName";
  const scene2 = new context.Scene_Name();
  scene2._actor = actor2;
  scene2._editWindow = edit2;
  scene2.onInputOk();
  assert.strictEqual(actor2._name, "NewName");
}

function testFrozenMessageFallsBack() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);
  const message = new context.Game_Message();
  message._texts = ["これはとても長い文章です"];
  context.$gameMessage = message;
  Object.freeze(message);
  const windowMessage = new context.Window_Message();
  windowMessage.innerWidth = 140;
  windowMessage.textWidth = function (text) {
    return String(text).length * 10;
  };
  windowMessage.startMessage();
  assert.strictEqual(
    windowMessage.seen,
    "This is a very long sentence that should be wrapped."
  );
  assert.strictEqual(message._texts[0], "これはとても長い文章です");
}

function testPriorWrappersPreserved() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game, {
    beforeLoad: function (ctx) {
      const originalAllText = ctx.Game_Message.prototype.allText;
      ctx.Game_Message.prototype.allText = function () {
        return originalAllText.apply(this, arguments) + " [prior]";
      };
      const originalName = ctx.Game_Actor.prototype.name;
      ctx.Game_Actor.prototype.name = function () {
        return "Mr. " + originalName.apply(this, arguments);
      };
      let titleCalls = 0;
      const originalTitle = ctx.Scene_Title.prototype.drawGameTitle;
      ctx.Scene_Title.prototype.drawGameTitle = function () {
        titleCalls++;
        return originalTitle.apply(this, arguments);
      };
      ctx.__titleCalls = function () {
        return titleCalls;
      };
    }
  });
  const message = new context.Game_Message();
  message._texts = ["こんにちは"];
  assert(message.allText().indexOf("[prior]") >= 0);
  const actor = new context.Game_Actor();
  actor._name = "太郎";
  assert.strictEqual(actor.name(), "Mr. 太郎");
  const title = new context.Scene_Title();
  title.drawGameTitle();
  assert.strictEqual(context.__titleCalls(), 1);
  assert.strictEqual(context.$dataSystem.gameTitle, "ゲーム");
}

function testScrollTextWrapping() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);
  const message = new context.Game_Message();
  message._texts = ["これはとても長い文章です"];
  context.$gameMessage = message;
  const scroll = new context.Window_ScrollText();
  scroll.textWidth = function (text) {
    return String(text).length * 10;
  };
  scroll.startMessage();
  assert(scroll.seen.indexOf("\n") >= 0);
  assert.strictEqual(message._texts[0], "これはとても長い文章です");
}

function testFontFallbackChain() {
  const game = makeGame({
    config: {
      enabled: true,
      fontFace: "GameFont, PingFang SC",
      wrap: true,
      debug: false
    },
    dictionary: baseDictionary()
  });
  const context = makeContext(game);
  const face = context.Window_Base.prototype.standardFontFace.call({});
  assert.strictEqual(face, "GameFont, PingFang SC");
  const mainFace = context.Game_System.prototype.mainFontFace.call({});
  assert.strictEqual(mainFace, "GameFont, PingFang SC, sans-serif");
}

function testBomCrlfNfcAndPath() {
  const dictionary = {
    "こんにちは": "Hello",
    "が": "GA"
  };
  const game = makeGame({
    gameName: "ゲーム タイトル #1",
    dictionary: null,
    configText: "\ufeff" + JSON.stringify({
      enabled: true,
      fontFace: "",
      wrap: true,
      debug: false
    }).replace(/,/g, ",\r\n")
  });
  const nfdKey = "か\u3099";
  const dictText = JSON.stringify({ [nfdKey]: "GA" }, null, 2);
  fs.writeFileSync(
    path.join(game.plugins, "RPGM_Launcher_Translate.json"),
    dictText,
    "utf8"
  );
  const context = makeContext(game);
  assert.strictEqual(context.RPGMLauncherTranslate.translateText("が"), "GA");
  assert.strictEqual(context.RPGMLauncherTranslate.translateBlock("こんにちは"), "こんにちは");
}

function testXhrFallback() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game, { xhr: true });
  assert(context.RPGMLauncherTranslate, "XHR fallback should load the plugin");
  assert.strictEqual(context.RPGMLauncherTranslate.translateText("こんにちは"), "Hello");
}

function testDoubleLoadGuard() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);
  const first = context.RPGMLauncherTranslate;
  const hooksBefore = first.getStats().hooks.length;
  vm.runInContext(pluginCode, context.vm, { filename: pluginPath });
  assert.strictEqual(context.RPGMLauncherTranslate, first);
  assert.strictEqual(context.RPGMLauncherTranslate.getStats().hooks.length, hooksBefore);
}

function testSaveMetadataAndChangeNameConditional() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game, {
    beforeLoad: function (ctx) {
      ctx.DataManager.isThisGameFile = function (info) {
        return info && info.title === ctx.$dataSystem.gameTitle;
      };
    }
  });
  const actor = new context.Game_Actor();
  actor._name = "太郎";
  actor.setName("ボブ");
  const interpreter = new context.Game_Interpreter();
  interpreter.actor = actor;
  interpreter.command111();
  assert.strictEqual(interpreter.seenName, "ボブ");
  assert.strictEqual(actor.name(), "Bob");

  const title = new context.Scene_Title();
  title.drawGameTitle();
  assert.strictEqual(context.$dataSystem.gameTitle, "ゲーム");
  assert.strictEqual(
    context.DataManager.isThisGameFile({ title: "ゲーム" }),
    true
  );
}

function testNameInputSurvivesSaveReloadShape() {
  const game = makeGame({ engine: "MV", dictionary: baseDictionary() });
  const context = makeContext(game);
  const actor = new context.Game_Actor();
  actor._name = "太郎";
  const edit = new context.Window_NameEdit(actor, 10);
  const scene = new context.Scene_Name();
  scene._actor = actor;
  scene._editWindow = edit;
  scene.onInputOk();
  const reloaded = new context.Game_Actor();
  reloaded._name = actor._name;
  assert.strictEqual(reloaded.name(), "Taro");
  assert.strictEqual(actor._name, "太郎");
}

function testInvalidJsonFilesNoop() {
  const invalidConfig = makeGame({
    dictionary: baseDictionary(),
    configText: "{invalid config"
  });
  assert.strictEqual(makeContext(invalidConfig).RPGMLauncherTranslate, undefined);
  const invalidDict = makeGame({
    config: { enabled: true, fontFace: "", wrap: true, debug: false },
    dictionaryText: "{invalid dictionary"
  });
  assert.strictEqual(makeContext(invalidDict).RPGMLauncherTranslate, undefined);
}

function testZeroAllTextCallsRestoresShadow() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game, {
    beforeLoad: function (ctx) {
      ctx.Window_Message.prototype.startMessage = function () {
        this.calledWithoutAllText = true;
      };
    }
  });
  const message = new context.Game_Message();
  message._texts = ["これはとても長い文章です"];
  context.$gameMessage = message;
  const windowMessage = new context.Window_Message();
  windowMessage.innerWidth = 140;
  windowMessage.textWidth = function (text) {
    return String(text).length * 10;
  };
  windowMessage.startMessage();
  assert.strictEqual(windowMessage.calledWithoutAllText, true);
  assert.strictEqual(
    message.allText(),
    "This is a very long sentence that should be wrapped."
  );
}

function testMultipleAllTextCalls() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game, {
    beforeLoad: function (ctx) {
      const original = ctx.Window_Message.prototype.startMessage;
      ctx.Window_Message.prototype.startMessage = function () {
        this.allTextCalls = [ctx.$gameMessage.allText(), ctx.$gameMessage.allText()];
        return original.apply(this, arguments);
      };
    }
  });
  const message = new context.Game_Message();
  message._texts = ["これはとても長い文章です"];
  context.$gameMessage = message;
  const windowMessage = new context.Window_Message();
  windowMessage.innerWidth = 140;
  windowMessage.textWidth = function (text) {
    return String(text).length * 10;
  };
  windowMessage.startMessage();
  assert.strictEqual(windowMessage.allTextCalls[0], windowMessage.allTextCalls[1]);
  assert(windowMessage.allTextCalls[0].indexOf("\n") >= 0);
  assert.strictEqual(
    message.allText(),
    "This is a very long sentence that should be wrapped."
  );
}

function testWrappingRestoresAfterException() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game, {
    beforeLoad: function (ctx) {
      ctx.Window_Message.prototype.startMessage = function () {
        ctx.$gameMessage.allText();
        throw new Error("forced startMessage failure");
      };
    }
  });
  const message = new context.Game_Message();
  message._texts = ["これはとても長い文章です"];
  context.$gameMessage = message;
  const windowMessage = new context.Window_Message();
  windowMessage.innerWidth = 140;
  windowMessage.textWidth = function (text) {
    return String(text).length * 10;
  };
  assert.throws(function () {
    windowMessage.startMessage();
  });
  assert.strictEqual(
    message.allText(),
    "This is a very long sentence that should be wrapped."
  );
}

function testMzFaceMessageWidth() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);
  const message = new context.Game_Message();
  message._texts = ["これはとても長い文章です"];
  context.$gameMessage = message;
  const windowMessage = new context.Window_Message();
  windowMessage.innerWidth = 200;
  windowMessage.newLineX = function () {
    return 164;
  };
  windowMessage.baseTextRect = function () {
    return { width: 184 };
  };
  windowMessage.textWidth = function (value) {
    return String(value).length * 10;
  };
  windowMessage.startMessage();
  const lines = windowMessage.seen.split("\n");
  for (const line of lines) {
    assert(line.length * 10 <= 28, "line exceeds face-aware width: " + line);
  }
}

function testControlsSurviveWrapping() {
  const dictionary = baseDictionary();
  dictionary["\\C[1]これはとても長い文章です"] =
    "\\C[1]This is a very long sentence that should be wrapped.";
  const game = makeGame({ dictionary: dictionary });
  const context = makeContext(game);
  const message = new context.Game_Message();
  message._texts = ["\\C[1]これはとても長い文章です"];
  context.$gameMessage = message;
  const windowMessage = new context.Window_Message();
  windowMessage.innerWidth = 140;
  windowMessage.textWidth = function (text) {
    return String(text).length * 10;
  };
  windowMessage.startMessage();
  assert(windowMessage.seen.indexOf("\n") >= 0);
  assert.strictEqual(windowMessage.seen.indexOf("\\C[1]"), 0);
  assert.strictEqual(windowMessage.seen.split("\\C[1]").length - 1, 1);
}

function testSaveWindowTitlesAndNames() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);

  const altInfo = { title: "ゲーム" };
  const alt = new context.Window_SavefileStatus();
  alt.drawContents(altInfo, {}, true);
  assert.strictEqual(alt.drawnTitle, "Game");
  assert.strictEqual(altInfo.title, "ゲーム");

  const bbInfo = { title: "ゲーム" };
  const bb = new context.Window_SavefileList();
  bb.drawContents(bbInfo, {}, true);
  assert.strictEqual(bb.drawnTitle, "Game");
  assert.strictEqual(bbInfo.title, "ゲーム");

  const mzInfo = { title: "ゲーム" };
  const mz = new context.Window_SaveFileList();
  mz.drawFileInfo(mzInfo, 0, 0, 200);
  assert.strictEqual(mz.drawnTitle, "Game");
  assert.strictEqual(mzInfo.title, "ゲーム");

  const yep = new context.Window_SaveInfo();
  yep._info = { title: "ゲーム" };
  yep.drawGameTitle(0);
  assert.strictEqual(yep.drawnTitle, "Game");
  assert.strictEqual(yep._info.title, "ゲーム");

  const member = { _name: "太郎" };
  yep._saveContents = { actors: { _data: { 1: member } } };
  yep.drawPartyNames(0);
  assert.deepStrictEqual(yep.drawnNames, ["Taro"]);
  assert.strictEqual(member._name, "太郎");
}

function testUiLabelBitmapTranslation() {
  const dictionary = baseDictionary();
  dictionary["装備"] = "Equipment";
  dictionary["上"] = "Up";
  const game = makeGame({
    dictionary: dictionary,
    config: {
      enabled: true,
      fontFace: "",
      wrap: true,
      debug: false,
      uiLabels: ["装備", "上"],
    },
  });
  const context = makeContext(game);
  const bitmap = new context.Bitmap(400, 100);
  bitmap.drawText("装備");
  bitmap.drawText("上");
  bitmap.drawText("未登録");
  assert.deepStrictEqual(bitmap.drawn, ["Equipment", "上", "未登録"]);
  const base = new context.Window_Base();
  base.drawTextEx("装備");
  assert.strictEqual(base.drawnText, "Equipment");
  base.drawTextEx("上");
  assert.strictEqual(base.drawnText, "上");
  assert.strictEqual(context.RPGMLauncherTranslate.getStats().uiLabels, 2);
}

function testTickerTextTranslation() {
  const dictionary = baseDictionary();
  dictionary["Ticker text"] = "Ticker translated";
  const game = makeGame({ dictionary: dictionary });
  const context = makeContext(game);
  const ticker = new context.Window_Ticker();
  ticker.drawTextEx("Ticker text");
  assert.strictEqual(ticker.drawnText, "Ticker translated");
  context.TickerManager.show("Ticker text");
  assert.strictEqual(context.TickerManager.drawnText, "Ticker translated");
  assert(
    context.RPGMLauncherTranslate.getStats().hooks.indexOf("TickerManager.show") >= 0
  );
}

function testScriptDisplayPathUsesTranslatedNames() {
  const dictionary = baseDictionary();
  dictionary["ポーション"] = "Potion";
  const game = makeGame({ dictionary: dictionary });
  const context = makeContext(game);
  const actor = new context.Game_Actor();
  actor._name = "太郎";
  context.$dataItems[1].name = "ポーション";
  const interpreter = new context.Game_Interpreter();
  interpreter.actor = actor;
  context.DataManager.onLoad(context.$dataItems);
  assert.strictEqual(context.$dataItems[1].name, "Potion");
  interpreter.command355();
  assert.strictEqual(interpreter.seenName, "Taro");
  assert.strictEqual(interpreter.seenItem, "Potion");
  assert.strictEqual(context.$dataItems[1].name, "Potion");
  assert.strictEqual(actor.name(), "Taro");
}

function testConditionalBranchSeesRawNames() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);
  const actor = new context.Game_Actor();
  actor._name = "太郎";
  actor._nickname = "アリ";
  actor._profile = "プロフィール";
  const conditional = new context.Game_Interpreter();
  conditional.actor = actor;
  context.DataManager.onLoad(context.$dataItems);
  assert.strictEqual(context.$dataItems[1].name, "Potion");
  conditional.command111();
  assert.strictEqual(conditional.seenName, "太郎");
  assert.strictEqual(conditional.seenNickname, "アリ");
  assert.strictEqual(conditional.seenProfile, "プロフィール");
  assert.strictEqual(conditional.seenItem, "薬");
  assert.strictEqual(actor.name(), "Taro");
}

function testTitleOverlay() {
  const game = makeGame({
    dictionary: baseDictionary(),
    config: {
      enabled: true,
      fontFace: "",
      wrap: true,
      debug: false,
      titleOverlay: true,
      titleOverlayFontSize: 32,
      titleOverlayBackground: 100,
    },
  });
  const context = makeContext(game);
  const title = new context.Scene_Title();
  title.createForeground();
  assert(title.created, true);
  assert(title.childAdded, "overlay sprite should be added");
  assert.deepStrictEqual(title.childAdded.bitmap.drawn, ["Game"]);
  assert.strictEqual(context.$dataSystem.gameTitle, "ゲーム");
}

function testCommandLabelTranslation() {
  const dictionary = baseDictionary();
  dictionary["装備"] = "Equipment";
  dictionary["ロード"] = "Load";
  const game = makeGame({ dictionary: dictionary });
  const context = makeContext(game);
  const command = new context.Window_Command();
  command._list = [{ name: "装備" }, { name: "ロード" }];
  command.drawItem(0);
  assert.strictEqual(command.drawnName, "Equipment");
  command.drawItem(1);
  assert.strictEqual(command.drawnName, "Load");
  assert.strictEqual(command._list[0].name, "装備");
  assert.strictEqual(command._list[1].name, "ロード");
  assert(context.RPGMLauncherTranslate.getStats().hooks.indexOf("Window_Command.drawItem") >= 0);
}

function testPageSeparatorsPreserved() {
  const dictionary = baseDictionary();
  dictionary["A"] = "甲";
  dictionary["B"] = "乙";
  dictionary["C"] = "丙";
  dictionary["A\f\nB"] = "X\fY";
  const game = makeGame({ dictionary: dictionary });
  const context = makeContext(game);
  const api = context.RPGMLauncherTranslate;
  assert.strictEqual(api.translateBlock("A\f\nB"), "甲\f\n乙");
  assert.strictEqual(api.translateBlock("A\fB"), "甲\f乙");
  assert.strictEqual(api.translateBlock("A\f\nB\f\nC"), "甲\f\n乙\f\n丙");
  assert.strictEqual(api.translateBlock("A\nB"), "甲\n乙");
  assert(api.getStats().pageHits >= 3, "pageHits should count multi-page blocks");
}

function testDerivedSpeakerNameMap() {
  const dictionary = baseDictionary();
  dictionary["こんにちは\n\\n<キモオジ>"] = "Hello\n\\n<Weird Old Man>";
  const game = makeGame({ dictionary: dictionary });
  const context = makeContext(game);
  assert.strictEqual(
    context.RPGMLauncherTranslate.translateName("\\C[27]キモオジ\\C[0]"),
    "\\C[27]Weird Old Man\\C[0]"
  );
  assert.strictEqual(context.RPGMLauncherTranslate.getStats().speakerNames, 1);
}

function testMvSpeakerNameBox() {
  const game = makeGame({ engine: "MV", dictionary: baseDictionary() });
  const context = makeContext(game);
  const box = new context.Window_NameBox();
  box.refresh("\\C[27]太郎\\C[0]", 2);
  assert.strictEqual(box.refreshed, "\\C[27]Taro\\C[0]");
  assert.strictEqual(box.position, 2);
}

function testMzSpeakerNameBox() {
  const game = makeGame({ engine: "MZ", dictionary: baseDictionary() });
  const context = makeContext(game);
  const box = new context.Window_NameBox();
  box.setName("太郎");
  assert.strictEqual(box._name, "Taro");
  box.refresh();
  assert.strictEqual(box.refreshed, "Taro");
}

function testDocumentTitleTranslation() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);
  const boot = new context.Scene_Boot();
  boot.updateDocumentTitle();
  assert.strictEqual(context.document.title, "Game");
  assert.strictEqual(context.$dataSystem.gameTitle, "ゲーム");
  context.document.title = "";
  const titleStart = new context.Scene_Title();
  titleStart.start();
  assert.strictEqual(context.document.title, "Game");
}

function testRuntimeHitCounters() {
  const game = makeGame({ dictionary: baseDictionary() });
  const context = makeContext(game);
  const message = new context.Game_Message();
  message._texts = ["こんにちは", "世界"];
  assert.strictEqual(message.allText(), "Hello\nWorld");
  let stats = context.RPGMLauncherTranslate.getStats();
  assert(stats.blockHits >= 1, "blockHits should count exact block hits");
  assert.strictEqual(stats.lineHits, 0);

  const lineMessage = new context.Game_Message();
  lineMessage._texts = ["こんにちは\nunknown"];
  assert.strictEqual(lineMessage.allText(), "Hello\nunknown");
  stats = context.RPGMLauncherTranslate.getStats();
  assert(stats.lineHits >= 1, "lineHits should count line-key fallback");
}

const tests = [
  testPageSeparatorsPreserved,
  testCommandLabelTranslation,
  testUiLabelBitmapTranslation,
  testTickerTextTranslation,
  testScriptDisplayPathUsesTranslatedNames,
  testConditionalBranchSeesRawNames,
  testTitleOverlay,
  testSaveWindowTitlesAndNames,
  testDerivedSpeakerNameMap,
  testMvSpeakerNameBox,
  testMzSpeakerNameBox,
  testDocumentTitleTranslation,
  testRuntimeHitCounters,
  testSaveMetadataAndChangeNameConditional,
  testNameInputSurvivesSaveReloadShape,
  testInvalidJsonFilesNoop,
  testZeroAllTextCallsRestoresShadow,
  testMultipleAllTextCalls,
  testWrappingRestoresAfterException,
  testMzFaceMessageWidth,
  testControlsSurviveWrapping,
  testDisabledConfig,
  testMissingDictionary,
  testMvNameInput,
  testFrozenMessageFallsBack,
  testPriorWrappersPreserved,
  testScrollTextWrapping,
  testFontFallbackChain,
  testBomCrlfNfcAndPath,
  testXhrFallback,
  testDoubleLoadGuard
];

let failures = 0;
for (const test of tests) {
  try {
    test();
    console.log("ok - " + test.name);
  } catch (error) {
    failures++;
    console.error("FAIL - " + test.name);
    console.error(error && error.stack ? error.stack : error);
  }
}
if (failures > 0) {
  process.exitCode = 1;
} else {
  console.log("translate plugin scenarios: OK");
}
