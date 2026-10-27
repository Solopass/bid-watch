const fs = require("fs");
const path = require("path");
const vm = require("vm");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "Parse.gs"), "utf8"), sandbox);

const dir = process.argv[2];
if (!dir) {
  console.error("Usage: node apps-script/parse_cli.js apps-script/fixtures");
  process.exit(2);
}
const files = fs.readdirSync(dir).filter((name) => name.endsWith(".eml")).sort();
const parsed = files.map((name) => {
  const email = sandbox.splitEmail(fs.readFileSync(path.join(dir, name), "utf8"));
  const row = sandbox.parseInvite(email);
  row.file = name;
  return row;
});
process.stdout.write(JSON.stringify(parsed));
