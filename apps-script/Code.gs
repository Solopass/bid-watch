/**
 * Gmail → private Google Sheet. Runs in the owner's Apps Script project.
 * This does not log into a plan room and does not write to GitHub.
 *
 * setup() once: create the sheet, the labels, and an hourly trigger.
 * processInvites() is what the trigger calls.
 */

var LABEL_IN = "bid-invites";
var LABEL_DONE = "bid-invites-processed";
var SHEET_NAME = "Invites";
var HEADERS = [
  "Message ID",
  "Received",
  "Project",
  "GC",
  "Due",
  "Location",
  "Scope",
  "Link",
  "Subject",
  "Status",
];

var SEARCH_QUERY = [
  "(label:" + LABEL_IN,
  "OR from:buildingconnected.com",
  "OR from:planhub.com",
  'OR subject:"invitation to bid"',
  'OR subject:"bid invite"',
  "OR subject:ITB)",
  "-label:" + LABEL_DONE,
  "newer_than:21d",
].join(" ");

function setup() {
  var sheet = getOrCreateSheet_();
  ensureLabel_(LABEL_IN);
  ensureLabel_(LABEL_DONE);
  installTrigger_();
  Logger.log("Private sheet (do not publish this link): " + sheet.getParent().getUrl());
  Logger.log("Hourly trigger installed for processInvites.");
}

function processInvites() {
  var sheet = getOrCreateSheet_();
  var seen = existingIds_(sheet);
  var done = ensureLabel_(LABEL_DONE);
  var incoming = ensureLabel_(LABEL_IN);
  var threads = GmailApp.search(SEARCH_QUERY, 0, 30);
  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (message) {
      rememberMessage_(sheet, seen, message);
    });
    thread.addLabel(incoming);
    thread.addLabel(done);
  });
}

function rememberMessage_(sheet, seen, message) {
  var id = message.getId();
  if (isDuplicate(id, seen)) return;
  var parsed;
  try {
    parsed = parseInvite({
      messageId: id,
      from: message.getFrom(),
      subject: message.getSubject(),
      body: message.getPlainBody() || message.getBody(),
    });
  } catch (err) {
    parsed = {
      project: message.getSubject() || "Plan room invite",
      gc: "",
      dueText: "",
      location: "",
      scope: "",
      link: "",
      subject: message.getSubject() || "",
      parseStatus: "partial",
    };
  }
  var link = parsed.link || gmailMessageUrl_(id);
  sheet.appendRow([
    id,
    message.getDate(),
    parsed.project || parsed.subject || "Plan room invite",
    parsed.gc || "",
    parsed.dueText || "",
    parsed.location || "",
    parsed.scope || "",
    link,
    parsed.subject || message.getSubject() || "",
    parsed.parseStatus || "partial",
  ]);
  seen[id] = true;
}

function getOrCreateSheet_() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty("SPREADSHEET_ID");
  var ss = id ? SpreadsheetApp.openById(id) : SpreadsheetApp.create("Bid Watch plan-room invites");
  if (!id) props.setProperty("SPREADSHEET_ID", ss.getId());
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.getSheets()[0];
    if (sheet.getLastRow() === 0) sheet.setName(SHEET_NAME);
    else sheet = ss.insertSheet(SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function existingIds_(sheet) {
  var seen = {};
  var last = sheet.getLastRow();
  if (last < 2) return seen;
  var values = sheet.getRange(2, 1, last - 1, 1).getValues();
  values.forEach(function (row) {
    if (row[0]) seen[String(row[0])] = true;
  });
  return seen;
}

function ensureLabel_(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

function installTrigger_() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === "processInvites") ScriptApp.deleteTrigger(trigger);
  });
  ScriptApp.newTrigger("processInvites").timeBased().everyHours(1).create();
}

function gmailMessageUrl_(id) {
  return "https://mail.google.com/mail/u/0/#all/" + id;
}
