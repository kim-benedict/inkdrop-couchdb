"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { configDir } = require("./runtime.cjs");
const sharp = require("sharp");
sharp.concurrency(1);
sharp.cache({ memory: 32, files: 0, items: 32 });
const MAX_INPUT = 32 * 1024 * 1024;
const MAX_OUTPUT = 2 * 1024 * 1024;
const TARGET = 512 * 1024;
const MAX_PIXELS = 40 * 1000 * 1000;
const hash = (b) => crypto.createHash("sha256").update(b).digest("hex");
const root = () => path.join(configDir(), "image-originals");
function privateDir(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
}
function writeOnce(file, bytes, verify = false) {
  if (fs.existsSync(file)) {
    if (verify && hash(fs.readFileSync(file)) !== hash(bytes))
      throw new Error(
        "Archived original failed its integrity check. No file was overwritten.",
      );
    return;
  }
  const temp = file + "." + crypto.randomBytes(8).toString("hex") + ".tmp";
  try {
    const fd = fs.openSync(temp, "wx", 0o600);
    try {
      fs.writeFileSync(fd, bytes);
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    try {
      fs.linkSync(temp, file);
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
    }
    if (verify && hash(fs.readFileSync(file)) !== hash(bytes))
      throw new Error("Original archive integrity check failed.");
    if (process.platform !== "win32") {
      const dir = fs.openSync(path.dirname(file), "r");
      try {
        fs.fsyncSync(dir);
      } finally {
        fs.closeSync(dir);
      }
    }
  } finally {
    if (fs.existsSync(temp)) fs.unlinkSync(temp);
  }
}
async function optimize(input, type, options = {}) {
  if (!Buffer.isBuffer(input) || input.length === 0 || input.length > MAX_INPUT)
    throw new Error(
      "Source must be nonempty and at most 32 MiB. The source was not changed.",
    );
  const archive = options.archive || root();
  privateDir(archive);
  privateDir(path.join(archive, "index"));
  const inputHash = hash(input);
  if (fs.existsSync(path.join(archive, "index", inputHash + ".json"))) {
    if (input.length > MAX_OUTPUT)
      throw new Error("Optimized image exceeds 2MiB.");
    return {
      buffer: input,
      type,
      sourceHash: inputHash,
      outputHash: inputHash,
      reused: true,
    };
  }
  const ext = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/heic": "heic",
    "image/heif": "heif",
  }[type];
  if (!ext) throw new Error("Unsupported image format. Use PNG or JPEG.");
  const space = fs.statfsSync(archive);
  if (Number(space.bavail) * Number(space.bsize) < input.length + 1024 ** 3)
    throw new Error(
      "Insufficient local space to archive the original. Upload stopped.",
    );
  writeOnce(path.join(archive, inputHash + "." + ext), input, true);
  writeOnce(
    path.join(archive, inputHash + ".json"),
    JSON.stringify({
      sha256: inputHash,
      type,
      bytes: input.length,
      archivedAt: new Date().toISOString(),
    }),
  );
  let output = input,
    outputType = type;
  let resized = false;
  if (type === "image/gif") {
    if (input.length > MAX_OUTPUT)
      throw new Error("GIF animation is preserved. Use a file below 2MiB.");
    const metadata = await sharp(input, {
      animated: true,
      limitInputPixels: MAX_PIXELS,
      failOn: "error",
    }).metadata();
    if (metadata.format !== "gif") throw new Error("GIF format mismatch.");
  }
  if (type === "image/jpeg" || type === "image/jpg" || type === "image/png") {
    const metadata = await sharp(input, {
      limitInputPixels: MAX_PIXELS,
      failOn: "error",
      animated: true,
    }).metadata();
    const expected = type === "image/png" ? "png" : "jpeg";
    if (metadata.format !== expected)
      throw new Error("Image format mismatch. Use a valid PNG or JPEG.");
    if ((metadata.pages || 1) > 1 || metadata.depth !== "uchar") {
      if (input.length > MAX_OUTPUT)
        throw new Error(
          "Animated and high bit-depth images are preserved. Use a file below 2MiB.",
        );
    } else if (expected === "jpeg") {
      let candidate;
      for (const [edge, quality] of [
        [2560, 85],
        [2560, 80],
        [2048, 80],
        [1920, 75],
      ]) {
        candidate = await sharp(input, {
          limitInputPixels: MAX_PIXELS,
          failOn: "error",
        })
          .rotate()
          .resize({
            width: edge,
            height: edge,
            fit: "inside",
            withoutEnlargement: true,
          })
          .jpeg({ quality, progressive: true, mozjpeg: true })
          .timeout({ seconds: 15 })
          .toBuffer();
        if (candidate.length <= TARGET) break;
      }
      if (candidate.length < input.length) {
        output = candidate;
        outputType = "image/jpeg";
        const out = await sharp(output).metadata();
        resized =
          out.width !== metadata.width || out.height !== metadata.height;
      }
    } else {
      let candidate = await sharp(input, {
        limitInputPixels: MAX_PIXELS,
        failOn: "error",
      })
        .keepIccProfile()
        .png({ compressionLevel: 9, adaptiveFiltering: true, palette: false })
        .timeout({ seconds: 15 })
        .toBuffer();
      if (candidate.length < input.length) output = candidate;
      if (output.length > MAX_OUTPUT) {
        for (const edge of [2560, 2048]) {
          candidate = await sharp(input, {
            limitInputPixels: MAX_PIXELS,
            failOn: "error",
          })
            .resize({
              width: edge,
              height: edge,
              fit: "inside",
              withoutEnlargement: true,
            })
            .keepIccProfile()
            .png({
              compressionLevel: 9,
              adaptiveFiltering: true,
              palette: false,
            })
            .timeout({ seconds: 15 })
            .toBuffer();
          if (candidate.length < output.length) {
            output = candidate;
            resized = true;
          }
          if (output.length <= MAX_OUTPUT) break;
        }
      }
    }
    await sharp(output, {
      limitInputPixels: MAX_PIXELS,
      failOn: "error",
      animated: true,
    }).metadata();
  }
  if (output.length > MAX_OUTPUT)
    throw new Error(
      "Image remains above 2MiB after optimization. The original was archived; nothing uploaded.",
    );
  const outputHash = hash(output);
  const info = {
    sourceHash: inputHash,
    outputHash,
    sourceBytes: input.length,
    outputBytes: output.length,
    type: outputType,
    resized,
    optimizedAt: new Date().toISOString(),
  };
  writeOnce(
    path.join(archive, "index", outputHash + ".json"),
    JSON.stringify(info),
  );
  return { buffer: output, ...info };
}
module.exports = { optimize, MAX_OUTPUT, MAX_INPUT, TARGET };
