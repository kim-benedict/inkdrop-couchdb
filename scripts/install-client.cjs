"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { configDir } = require("../client/runtime.cjs");

const start = "// BEGIN INKDROP IMAGE SAFETY";
const end = "// END INKDROP IMAGE SAFETY";
const legacyStart = "// BEGIN PERSONAL IMAGE SAFETY";
const legacyEnd = "// END PERSONAL IMAGE SAFETY";

function removeBlock(text, a, b) {
  const first = text.indexOf(a);
  if (first < 0) return text;
  const last = text.indexOf(b, first);
  if (last < 0 || text.indexOf(a, first + a.length) >= 0)
    throw new Error("Unexpected init.js markers; no files changed.");
  return (
    text.slice(0, first) + text.slice(last + b.length).replace(/^\r?\n/, "")
  );
}

function install(uninstall = false) {
  const dir = configDir();
  if (!fs.existsSync(path.join(dir, "config.json")))
    throw new Error("Open Inkdrop once before installing.");
  const init = path.join(dir, "init.js");
  const before = fs.existsSync(init) ? fs.readFileSync(init, "utf8") : "";
  const clean = removeBlock(
    removeBlock(before, start, end),
    legacyStart,
    legacyEnd,
  );
  const state = path.join(dir, "image-safety");
  fs.mkdirSync(state, { recursive: true, mode: 0o700 });
  let after = clean;
  if (!uninstall) {
    require("sharp");
    const modulePath = path.resolve(__dirname, "../client/inkdrop-init.cjs");
    const runtime = {
      node: process.execPath,
      installedAt: new Date().toISOString(),
    };
    fs.writeFileSync(
      path.join(state, "runtime.json"),
      JSON.stringify(runtime),
      { mode: 0o600 },
    );
    after =
      clean +
      (clean.endsWith("\n") || !clean ? "" : "\n") +
      start +
      "\n" +
      "try { require(" +
      JSON.stringify(modulePath) +
      ")(inkdrop); }\n" +
      'catch (error) { inkdrop.notifications.addError("Image safety initialization failed", { detail: error.message, dismissable: true }); }\n' +
      end +
      "\n";
  }
  if (after !== before) {
    const backup = path.join(
      state,
      "init-" +
        crypto.createHash("sha256").update(before).digest("hex") +
        ".js",
    );
    if (!fs.existsSync(backup))
      fs.writeFileSync(backup, before, { mode: 0o600, flag: "wx" });
    const temp = init + ".image-safety.tmp";
    fs.writeFileSync(temp, after, { mode: 0o600, flag: "wx" });
    fs.renameSync(temp, init);
  }
  console.log(
    uninstall
      ? "Removed image safety. Reload Inkdrop. Originals were kept."
      : "Installed image safety. Keep this checkout in place and reload Inkdrop.",
  );
}

if (require.main === module) {
  try {
    install(process.argv.includes("--uninstall"));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
module.exports = { install, removeBlock };
