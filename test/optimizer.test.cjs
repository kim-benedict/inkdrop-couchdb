"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const sharp = require("sharp");
const { optimize, MAX_OUTPUT } = require("../client/optimizer.cjs");
const hash = (b) => crypto.createHash("sha256").update(b).digest("hex");
function archive(t) {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "inkdrop-image-test-"));
  t.after(() => fs.rmSync(d, { recursive: true, force: true }));
  return d;
}
test("PNG text-like pixels and alpha survive lossless optimization; exact original archived", async (t) => {
  const d = archive(t),
    raw = Buffer.alloc(800 * 400 * 4);
  for (let y = 0; y < 400; y++)
    for (let x = 0; x < 800; x++) {
      const i = (y * 800 + x) * 4,
        v = x % 15 < 3 && y % 30 > 8 ? 0 : 255;
      raw[i] = raw[i + 1] = raw[i + 2] = v;
      raw[i + 3] = x < 30 ? 0 : 255;
    }
  const src = await sharp(raw, {
    raw: { width: 800, height: 400, channels: 4 },
  })
    .png({ compressionLevel: 0 })
    .toBuffer();
  const r = await optimize(src, "image/png", { archive: d });
  assert(r.buffer.length < src.length);
  assert.equal(r.type, "image/png");
  assert.equal(r.resized, false);
  assert.deepEqual(await sharp(r.buffer).ensureAlpha().raw().toBuffer(), raw);
  assert.deepEqual(fs.readFileSync(path.join(d, hash(src) + ".png")), src);
  const retry = await optimize(r.buffer, r.type, { archive: d });
  assert.equal(retry.reused, true);
  assert.deepEqual(retry.buffer, r.buffer);
});
test("Large JPEG is oriented, resized, reduced and decodable; source file unchanged", async (t) => {
  const d = archive(t),
    raw = crypto.randomBytes(3200 * 2000 * 3);
  const src = await sharp(raw, {
    raw: { width: 3200, height: 2000, channels: 3 },
  })
    .jpeg({ quality: 98 })
    .withMetadata({ orientation: 6 })
    .toBuffer();
  const r = await optimize(src, "image/jpeg", { archive: d }),
    m = await sharp(r.buffer).metadata();
  assert(r.buffer.length < src.length);
  assert(r.buffer.length <= MAX_OUTPUT);
  assert(Math.max(m.width, m.height) <= 2560);
  assert(m.height > m.width);
  assert.deepEqual(fs.readFileSync(path.join(d, hash(src) + ".jpg")), src);
});
test("Animated GIF and SVG are preserved byte-for-byte", async (t) => {
  const d = archive(t),
    frames = Buffer.alloc(10 * 20 * 4);
  frames.fill(200, 0, 10 * 10 * 4);
  frames.fill(100, 10 * 10 * 4);
  const gif = await sharp(frames, {
    raw: { width: 10, height: 20, channels: 4, pageHeight: 10 },
  })
    .gif({ delay: [100, 100], loop: 0 })
    .toBuffer();
  const r = await optimize(gif, "image/gif", { archive: d });
  assert.deepEqual(r.buffer, gif);
  assert.equal((await sharp(r.buffer, { animated: true }).metadata()).pages, 2);
  const svg = Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/></svg>',
  );
  assert.deepEqual(
    (await optimize(svg, "image/svg+xml", { archive: d })).buffer,
    svg,
  );
});
test("Oversized preserved formats and malformed raster fail without deleting the archived source", async (t) => {
  const d = archive(t),
    big = Buffer.alloc(MAX_OUTPUT + 1, 1);
  await assert.rejects(optimize(big, "image/gif", { archive: d }), /2MiB/);
  assert.deepEqual(fs.readFileSync(path.join(d, hash(big) + ".gif")), big);
  const corrupt = Buffer.from("not a png");
  await assert.rejects(optimize(corrupt, "image/png", { archive: d }));
  assert.deepEqual(
    fs.readFileSync(path.join(d, hash(corrupt) + ".png")),
    corrupt,
  );
});

test("A damaged archived original is never silently overwritten", async (t) => {
  const d = archive(t);
  const src = await sharp({
    create: { width: 20, height: 20, channels: 4, background: "#336699" },
  })
    .png()
    .toBuffer();
  fs.writeFileSync(
    path.join(d, hash(src) + ".png"),
    Buffer.from("damaged archive"),
  );
  await assert.rejects(optimize(src, "image/png", { archive: d }), /integrity/);
  assert.equal(
    fs.readFileSync(path.join(d, hash(src) + ".png"), "utf8"),
    "damaged archive",
  );
});
