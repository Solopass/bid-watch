/**
 * Pure parser for plan-room invite emails. No Gmail or Sheet calls.
 * Apps Script loads this file beside Code.gs. Node tests load it too.
 *
 * Fixtures in apps-script/fixtures are synthetic samples, not real invites.
 */

function splitEmail(raw) {
  var text = String(raw || "").replace(/\r\n/g, "\n");
  var splitAt = text.indexOf("\n\n");
  var header = splitAt === -1 ? text : text.slice(0, splitAt);
  var body = splitAt === -1 ? "" : text.slice(splitAt + 2);
  return {
    messageId: headerValue_(header, "Message-ID") || headerValue_(header, "Message-Id"),
    from: headerValue_(header, "From"),
    subject: headerValue_(header, "Subject"),
    body: body,
  };
}

function parseInvite(message) {
  var subject = cleanLine_(message && message.subject);
  var from = cleanLine_(message && message.from);
  var plain = toPlainText_(message && message.body);
  var fields = labeledFields_(plain);
  var project = fields.project || projectFromSubject_(subject);
  var gc = fields.gc || gcFromProse_(plain) || gcFromFrom_(from);
  var link = pickLink_(plain);
  var parsed = {
    messageId: cleanLine_(message && message.messageId),
    subject: subject,
    project: project,
    gc: gc,
    dueText: fields.due,
    location: fields.location,
    scope: fields.scope,
    link: link,
    parseStatus: "parsed",
  };
  if (!project) {
    parsed.project = subject;
    parsed.parseStatus = "partial";
  }
  if (!parsed.project && !link) {
    parsed.parseStatus = "partial";
  } else if (!fields.project && !fields.due && !fields.location && !fields.scope && !gc) {
    parsed.parseStatus = "partial";
  }
  return parsed;
}

function isDuplicate(messageId, seen) {
  if (!messageId || !seen) return false;
  return !!seen[messageId];
}

function toPlainText_(body) {
  var text = String(body || "");
  text = text.replace(/<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>/gi, " $1 ");
  text = text.replace(/<br\s*\/?>/gi, "\n");
  text = text.replace(/<\/p>/gi, "\n");
  text = text.replace(/<\/div>/gi, "\n");
  text = text.replace(/<\/tr>/gi, "\n");
  text = text.replace(/<[^>]+>/g, " ");
  text = text.replace(/&nbsp;/gi, " ");
  text = text.replace(/&amp;/gi, "&");
  text = text.replace(/&lt;/gi, "<");
  text = text.replace(/&gt;/gi, ">");
  text = text.replace(/&#160;/g, " ");
  return text;
}

function labeledFields_(plain) {
  var found = { project: "", gc: "", due: "", location: "", scope: "" };
  var lines = String(plain || "").split(/\n/);
  for (var i = 0; i < lines.length; i++) {
    var line = cleanLine_(lines[i]);
    if (!line) continue;
    assignLabel_(found, "project", line, /^(?:project(?:\s+name)?|job(?:\s+name)?)\s*[:\-]\s*(.+)$/i);
    assignLabel_(found, "gc", line, /^(?:general\s+contractor|gc|client|inviting\s+company)\s*[:\-]\s*(.+)$/i);
    assignLabel_(found, "due", line, /^(?:bid\s+due(?:\s+date)?|bids?\s+due|due\s+date|due|deadline|bid\s+deadline)\s*[:\-]\s*(.+)$/i);
    assignLabel_(found, "location", line, /^(?:project\s+)?(?:location|address)\s*[:\-]\s*(.+)$/i);
    assignLabel_(found, "scope", line, /^(?:trades?|scope(?:\s+of\s+work)?|package)\s*[:\-]\s*(.+)$/i);
  }
  return found;
}

function assignLabel_(found, key, line, pattern) {
  if (found[key]) return;
  var match = line.match(pattern);
  if (match && cleanLine_(match[1])) found[key] = cleanLine_(match[1]);
}

function projectFromSubject_(subject) {
  var patterns = [
    /^(?:invitation\s+to\s+bid|itb|bid\s+invite)\s*[:\-–—]\s*(.+)$/i,
    /^(.+?)\s*[:\-–—]\s*(?:invitation\s+to\s+bid|itb)\s*$/i,
  ];
  for (var i = 0; i < patterns.length; i++) {
    var match = subject.match(patterns[i]);
    if (match && cleanLine_(match[1]) && !/^invitation to bid$/i.test(cleanLine_(match[1]))) {
      return cleanLine_(match[1]);
    }
  }
  return "";
}

function gcFromProse_(plain) {
  var match = String(plain || "").match(/([A-Z][\w&.,'’ -]{2,80}?)\s+invited you to bid/i);
  return match ? cleanLine_(match[1]) : "";
}

function gcFromFrom_(from) {
  var name = String(from || "").split("<")[0].replace(/"/g, "");
  name = cleanLine_(name);
  if (!name) return "";
  if (/buildingconnected|planhub|noreply|no-reply|notifications|mailer-daemon/i.test(name)) return "";
  if (/@/.test(name)) return "";
  return name;
}

function pickLink_(plain) {
  var matches = String(plain || "").match(/https?:\/\/[^\s<>"')]+/gi) || [];
  var best = "";
  var bestRank = 0;
  for (var i = 0; i < matches.length; i++) {
    var url = matches[i].replace(/[.,;]+$/, "");
    var rank = linkRank_(url);
    if (rank > bestRank) {
      best = url;
      bestRank = rank;
    }
  }
  return best;
}

function linkRank_(url) {
  var value = url.toLowerCase();
  if (/unsubscribe|list-manage|email-preferences|optout|privacy|mailto:/.test(value)) return 0;
  if (/app\.buildingconnected\.com|buildingconnected\.com\/opportunit/.test(value)) return 3;
  if (/planhub\.com\/leads|subcontractor\.planhub\.com/.test(value)) return 3;
  if (/buildingconnected\.com|planhub\.com/.test(value)) return 2;
  return 1;
}

function headerValue_(header, name) {
  var match = String(header || "").match(new RegExp("^" + name + ":\\s*(.*)$", "im"));
  return match ? cleanLine_(match[1]) : "";
}

function cleanLine_(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}
