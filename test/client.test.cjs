"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const sharp = require("sharp");
const { install, removeBlock } = require("../scripts/install-client.cjs");

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "inkdrop-client-"));
  const before = process.env.INKDROP_CONFIG_DIR;
  process.env.INKDROP_CONFIG_DIR = dir;
  fs.writeFileSync(path.join(dir, "config.json"), "{}");
  t.after(() => {
    globalThis.__inkdropImageSafety?.restore();
    if (before === undefined) delete process.env.INKDROP_CONFIG_DIR;
    else process.env.INKDROP_CONFIG_DIR = before;
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return dir;
}

test("Client installation preserves existing init, is repeatable, and uninstalls only its block", (t) => {
  const dir = fixture(t);
  const existing = "globalThis.existingCustomization = true;\n";
  fs.writeFileSync(path.join(dir, "init.js"), existing);
  install();
  const first = fs.readFileSync(path.join(dir, "init.js"), "utf8");
  assert(first.startsWith(existing));
  install();
  assert.equal(fs.readFileSync(path.join(dir, "init.js"), "utf8"), first);
  install(true);
  assert.equal(fs.readFileSync(path.join(dir, "init.js"), "utf8"), existing);
  assert.throws(() =>
    removeBlock(
      "// BEGIN INKDROP IMAGE SAFETY",
      "// BEGIN INKDROP IMAGE SAFETY",
      "// END INKDROP IMAGE SAFETY",
    ),
  );
});

test("Clipboard images are optimized before local storage; offline sync and existing revisions survive", async (t) => {
  const dir = fixture(t);
  install();
  const input = await sharp({
    create: { width: 600, height: 300, channels: 4, background: "#2255aa88" },
  })
    .png({ compressionLevel: 0 })
    .toBuffer();
  const saved = [];
  class Files {
    async put(doc) {
      saved.push(doc);
      return { id: doc._id, rev: "1-test" };
    }
    createId() {
      return "file:test";
    }
  }
  let warnings = 0;
  const app = {
    localDB: { files: new Files() },
    clipboard: {
      readImageAsDataURL: async () =>
        "data:image/png;base64," + input.toString("base64"),
      saveAsImageAttachment: async () => null,
    },
    notifications: {
      addWarning() {
        warnings++;
      },
      addError(message, options) {
        throw new Error(message + ": " + options.detail);
      },
    },
  };
  require("../client/inkdrop-init.cjs")(app);
  assert.equal(
    await app.clipboard.saveAsImageAttachment({ publicIn: ["note:test"] }),
    "file:test",
  );
  const output = Buffer.from(saved[0]._attachments.index.data, "base64");
  assert(output.length < input.length);
  assert.deepEqual(
    await sharp(output).raw().toBuffer(),
    await sharp(input).raw().toBuffer(),
  );
  assert.deepEqual(saved[0].publicIn, ["note:test"]);
  assert.equal(warnings, 1);
  const revision = {
    _id: "file:existing",
    _rev: "2-remote",
    _attachments: { index: { stub: true } },
  };
  await app.localDB.files.put(revision);
  assert.equal(saved[1], revision);
  assert(
    fs
      .readdirSync(path.join(dir, "image-originals"))
      .some((name) => name.endsWith(".png")),
  );
});
