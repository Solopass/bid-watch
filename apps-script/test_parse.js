const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "Parse.gs"), "utf8"), sandbox);

function load(name) {
  const raw = fs.readFileSync(path.join(__dirname, "fixtures", name), "utf8");
  return sandbox.parseInvite(sandbox.splitEmail(raw));
}

const building = load("buildingconnected-framingham.eml");
assert.strictEqual(building.project, "Framingham Office Curtain Wall");
assert.strictEqual(building.gc, "Harborline Construction");
assert.strictEqual(building.dueText, "November 12, 2026 at 2:00 PM EST");
assert.ok(building.location.includes("Framingham"));
assert.ok(/curtain wall/i.test(building.scope));
assert.strictEqual(
  building.link,
  "https://app.buildingconnected.com/opportunities/sample-framingham"
);
assert.strictEqual(building.parseStatus, "parsed");
assert.ok(!/unsubscribe/.test(building.link));

const planhub = load("planhub-natick.eml");
assert.strictEqual(planhub.project, "Natick Lab Fit-Out");
assert.strictEqual(planhub.gc, "Ridgeline Builders");
assert.strictEqual(planhub.dueText, "12/09/2026 10:00 AM");
assert.ok(planhub.location.includes("Natick"));
assert.ok(/electrical panel/i.test(planhub.scope));
assert.strictEqual(planhub.link, "https://subcontractor.planhub.com/leads/sample-natick");

const generic = load("generic-wellesley.eml");
assert.strictEqual(generic.project, "Wellesley Library Window Replacement");
assert.strictEqual(generic.gc, "Oak and Pine Estimating");
assert.ok(/window replacement/i.test(generic.scope));
assert.strictEqual(generic.link, "https://files.example.com/sample-wellesley-itb");
assert.ok(generic.location.includes("Wellesley"));

const partial = load("partial-invite.eml");
assert.strictEqual(partial.parseStatus, "partial");
assert.strictEqual(partial.project, "Invitation to Bid");
assert.strictEqual(partial.link, "");
assert.strictEqual(partial.gc, "");
assert.strictEqual(partial.dueText, "");
assert.strictEqual(partial.location, "");
assert.strictEqual(partial.scope, "");

assert.strictEqual(sandbox.isDuplicate("abc", { abc: true }), true);
assert.strictEqual(sandbox.isDuplicate("new", { abc: true }), false);
assert.strictEqual(sandbox.isDuplicate("", {}), false);

console.log("parse fixtures ok");
