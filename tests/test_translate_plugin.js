// Minimal Node harness for RPGM_Launcher_Translate.js.
// It mocks the small part of the RPG Maker/NW.js runtime the plugin touches.
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { pathToFileURL } = require("url");

const repo = path.resolve(__dirname, "..");
const pluginPath = path.join(repo, "RPGM_Launcher_Translate.js");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rpgm-translate-"));
const game = path.join(tmp, "game");
const plugins = path.join(game, "js", "plugins");
fs.mkdirSync(plugins, { recursive: true });
fs.writeFileSync(path.join(game, "index.html"), "<html></html>");

const dictionary = {
  "こんにちは": "Hello",
  "Hello": "Greeting",
  "世界": "World",
  "こんにちは\n世界": "Hello\nWorld",
  "薬": "Potion",
  "回復": "Restore",
  "はい": "Yes",
  "いいえ": "No",
  "太郎": "Taro",
  "戦士": "Warrior",
  "炎": "Fire",
  "レベル": "Level",
  "戦う": "Fight",
  "最大HP": "Max HP",
  "%1は%2ダメージ": "%1 took %2 damage",
  "ゲーム": "Game",
  "マップ": "Map",
  "G": "Gold",
  "これはとても長い文章です": "This is a very long sentence that should be wrapped.",
  "\\C[1]赤": "\\C[1]Red"
};
fs.writeFileSync(
  path.join(plugins, "RPGM_Launcher_Translate.json"),
  JSON.stringify(dictionary),
  "utf8"
);
fs.writeFileSync(
  path.join(plugins, "RPGM_Launcher_Translate.config.json"),
  JSON.stringify({ enabled: true, fontFace: "", wrap: true, debug: false }),
  "utf8"
);

global.window = global;
global.location = {
  href: pathToFileURL(path.join(game, "index.html")).href,
  pathname: path.join(game, "index.html")
};
global.document = {};
Object.defineProperty(globalThis, "navigator", {
  value: {},
  configurable: true,
  writable: true
});
global.require = require;
global.XMLHttpRequest = function () {
  throw new Error("XHR should not be used when fs is available");
};

function DataManager() {}
DataManager.onLoad = function () {};
global.DataManager = DataManager;

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
global.Game_Message = Game_Message;
global.$gameMessage = new Game_Message();

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
global.Game_Actor = Game_Actor;

function Game_Interpreter() {}
Game_Interpreter.prototype.command111 = function () {
  this.seenName = this.actor ? this.actor.name() : null;
  return true;
};
global.Game_Interpreter = Game_Interpreter;

function Window_Base() {}
Window_Base.prototype.standardFontFace = function () {
  return "GameFont";
};
Window_Base._faceWidth = 144;
global.Window_Base = Window_Base;

function Game_System() {}
Game_System.prototype.mainFontFace = function () {
  return "GameFont, sans-serif";
};
global.Game_System = Game_System;

function Window_Message() {}
Window_Message.prototype.startMessage = function () {
  this.seen = $gameMessage.allText();
};
global.Window_Message = Window_Message;

function Window_ScrollText() {}
Window_ScrollText.prototype.startMessage = function () {
  this.seen = $gameMessage.allText();
};
global.Window_ScrollText = Window_ScrollText;

function Window_NameEdit(actor, maxLength) {
  this.initialize({});
  this.setup(actor, maxLength);
}
Window_NameEdit.prototype.initialize = function (rect) {
  this._actor = null;
  this._maxLength = 0;
  this._name = "";
};
Window_NameEdit.prototype.setup = function (actor, maxLength) {
  this._actor = actor;
  this._maxLength = maxLength;
  this._name = actor.name().slice(0, maxLength);
};
global.Window_NameEdit = Window_NameEdit;

function Scene_Name() {}
Scene_Name.prototype.onInputOk = function () {
  this._actor.setName(this._editWindow.name());
  this.popped = true;
};
global.Scene_Name = Scene_Name;

function Scene_Title() {}
Scene_Title.prototype.start = function () {
  global.document.title = $dataSystem.gameTitle;
};
Scene_Title.prototype.drawGameTitle = function () {
  this.drawnTitle = $dataSystem.gameTitle;
};
global.Scene_Title = Scene_Title;

function Scene_Boot() {}
Scene_Boot.prototype.updateDocumentTitle = function () {
  global.document.title = $dataSystem.gameTitle;
};
global.Scene_Boot = Scene_Boot;

function Window_NameBox() {
  this._name = "";
}
Window_NameBox.prototype.setName = function (name) {
  this._name = name;
};
global.Window_NameBox = Window_NameBox;

global.ImageManager = { faceWidth: 144, iconWidth: 32 };
global.Graphics = { boxWidth: 816, boxHeight: 624 };
global.Utils = { RPGMAKER_NAME: "MZ" };
global.PluginManager = { parameters: function () { return {}; } };
global.TextManager = { currencyUnit: "G" };
global.ColorManager = {};

global.$dataActors = [null, { name: "太郎", nickname: "", profile: "" }];
global.$dataClasses = [null, { name: "戦士" }];
global.$dataSkills = [null, { name: "炎", description: "", message1: "", message2: "" }];
global.$dataItems = [null, { name: "薬", description: "回復" }];
global.$dataWeapons = [null, { name: "剣", description: "" }];
global.$dataArmors = [null, { name: "鎧", description: "" }];
global.$dataEnemies = [null, { name: "スライム" }];
global.$dataStates = [null, { name: "毒", message1: "" }];
global.$dataTroops = [];
global.$dataCommonEvents = [];
global.$dataTilesets = [];
global.$dataAnimations = [];
global.$dataMapInfos = [null, { name: "マップ" }];
global.$dataMap = { displayName: "マップ", events: [] };
global.$dataSystem = {
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
global.$gameSystem = new Game_System();

vm.runInThisContext(fs.readFileSync(pluginPath, "utf8"), {
  filename: pluginPath
});

const api = window.RPGMLauncherTranslate;
assert(api, "plugin should expose window.RPGMLauncherTranslate");
assert.strictEqual(api.enabled, true);
assert.strictEqual(api.entryCount, Object.keys(dictionary).length);

assert.strictEqual(api.translateText("こんにちは"), "Hello");
assert.strictEqual(api.translateText("Hello"), "Greeting");
assert.strictEqual(api.translateText("World"), "World");
assert.strictEqual(api.translateBlock("こんにちは\n世界"), "Hello\nWorld");
// A block that is itself another entry's translation value must still use
// MTool-style line keys when no full-block key exists.
assert.strictEqual(api.translateBlock("Hello\nWorld"), "Greeting\nWorld");
assert.strictEqual(api.translateBlock("こんにちは\nunknown"), "Hello\nunknown");

DataManager.onLoad($dataItems);
assert.strictEqual($dataItems[1].name, "Potion");
assert.strictEqual($dataItems[1].description, "Restore");

DataManager.onLoad($dataActors);
assert.strictEqual($dataActors[1].name, "太郎", "actor database must stay raw");

DataManager.onLoad($dataSystem);
assert.strictEqual($dataSystem.gameTitle, "ゲーム", "gameTitle must stay raw");
assert.strictEqual($dataSystem.currencyUnit, "Gold");
assert.strictEqual($dataSystem.elements[1], "Fire");
assert.strictEqual($dataSystem.terms.basic[0], "Level");
assert.strictEqual($dataSystem.terms.commands[0], "Fight");
assert.strictEqual($dataSystem.terms.params[0], "Max HP");
assert.strictEqual($dataSystem.terms.messages.actorDamage, "%1 took %2 damage");

DataManager.onLoad($dataMap);
assert.strictEqual($dataMap.displayName, "Map");

var message = new Game_Message();
message._texts = ["こんにちは", "世界"];
assert.strictEqual(message.allText(), "Hello\nWorld");
message.setChoices(["はい", "いいえ"]);
assert.deepStrictEqual(message._choices, ["Yes", "No"]);
message.setSpeakerName("太郎");
assert.strictEqual(message._speakerName, "Taro");

var statsAfterMessage = api.getStats();
assert(statsAfterMessage.blockHits >= 1, "exact block hit should increment blockHits");
assert.strictEqual(api.translateName("\\C[1]太郎\\C[0]"), "\\C[1]Taro\\C[0]");

var nameBox = new Window_NameBox();
nameBox.setName("太郎");
assert.strictEqual(nameBox._name, "Taro");

var boot = new Scene_Boot();
boot.updateDocumentTitle();
assert.strictEqual(document.title, "Game");
assert.strictEqual($dataSystem.gameTitle, "ゲーム", "document title uses a display value");

document.title = "";
var titleStart = new Scene_Title();
titleStart.start();
assert.strictEqual(document.title, "Game");

var actor = new Game_Actor();
actor._name = "太郎";
assert.strictEqual(actor.name(), "Taro");
var interpreter = new Game_Interpreter();
interpreter.actor = actor;
interpreter.command111();
assert.strictEqual(interpreter.seenName, "太郎", "conditionals must see raw names");
assert.strictEqual(actor.name(), "Taro");

var actor2 = new Game_Actor();
actor2._name = "太郎";
var edit = new Window_NameEdit(actor2, 10);
assert.strictEqual(edit._name, "Taro");
assert.strictEqual(edit._rpgmRawName, "太郎");
var scene = new Scene_Name();
scene._actor = actor2;
scene._editWindow = edit;
scene.onInputOk();
assert.strictEqual(actor2._name, "太郎", "unchanged name input must keep raw name");
assert(scene.popped, true);

var actor3 = new Game_Actor();
actor3._name = "太郎";
var edit3 = new Window_NameEdit(actor3, 10);
edit3._name = "NewName";
var scene3 = new Scene_Name();
scene3._actor = actor3;
scene3._editWindow = edit3;
scene3.onInputOk();
assert.strictEqual(actor3._name, "NewName", "edited name input must be stored");

var title = new Scene_Title();
title.drawGameTitle();
assert.strictEqual(title.drawnTitle, "Game");
assert.strictEqual($dataSystem.gameTitle, "ゲーム", "title substitution must restore raw value");

var wrapWindow = {
  innerWidth: 140,
  contentsWidth: function () { return 140; },
  newLineX: function () { return 0; },
  textWidth: function (text) { return String(text).length * 10; }
};
var wrapped = api.wrapText("This is a very long sentence that should be wrapped.", wrapWindow);
assert(wrapped.indexOf("\n") !== -1, "long text should wrap");

var message2 = new Game_Message();
message2._texts = ["これはとても長い文章です"];
global.$gameMessage = message2;
var messageWindow = new Window_Message();
messageWindow.innerWidth = 140;
messageWindow.contentsWidth = function () { return 140; };
messageWindow.newLineX = function () { return 0; };
messageWindow.textWidth = function (text) { return String(text).length * 10; };
messageWindow.startMessage();
assert(messageWindow.seen.indexOf("\n") !== -1, "Window_Message should receive wrapped translation");
assert.strictEqual(message2._texts[0], "これはとても長い文章です", "message source must stay unchanged");
assert.strictEqual(message2.allText(), "This is a very long sentence that should be wrapped.", "allText must be restored");

console.log("translate plugin harness: OK");
