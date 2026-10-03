"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const https = require("node:https");
const { endpoint } = require("../client/runtime.cjs");

function read(suffix, limit) {
  const url = endpoint();
  return new Promise((resolve, reject) => {
    const req = https.get(
      {
        hostname: url.hostname,
        port: url.port || 443,
        path: url.pathname.replace(/\/$/, "") + suffix,
        auth:
          decodeURIComponent(url.username) +
          ":" +
          decodeURIComponent(url.password),
        headers: { "User-Agent": "InkdropImageSafety/0.1", Accept: "*/*" },
      },
      (res) => {
        const chunks = [];
        let bytes = 0;
        res.on("data", (chunk) => {
          bytes += chunk.length;
          if (bytes > limit)
            req.destroy(new Error("Response exceeds size limit."));
          else chunks.push(chunk);
        });
        res.on("error", reject);
        res.on("end", () =>
          res.statusCode === 200
            ? resolve(Buffer.concat(chunks))
            : reject(new Error("Read failed: HTTP " + res.statusCode)),
        );
      },
    );
    req.on("error", reject);
    req.setTimeout(30000, () => req.destroy(new Error("Read timed out.")));
  });
}

async function main(args) {
  if (args.length !== 2 || !/^file:[A-Za-z0-9_-]+$/.test(args[0]))
    throw new Error("Usage: node scripts/read-image.cjs file:ID output.png");
  const id = args[0];
  const prefix = "/" + encodeURIComponent(id);
  const doc = JSON.parse((await read(prefix, 65536)).toString("utf8"));
  const attachment = doc._attachments?.index;
  if (!attachment) throw new Error("Image attachment not found.");
  const bytes = await read(prefix + "/index", 4 * 1024 ** 2);
  const digest =
    "md5-" + crypto.createHash("md5").update(bytes).digest("base64");
  if (attachment.digest && attachment.digest !== digest)
    throw new Error("Attachment integrity check failed.");
  const output = path.resolve(args[1]);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const fd = fs.openSync(output, "wx", 0o600);
  try {
    fs.writeFileSync(fd, bytes);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  console.log(
    JSON.stringify({
      id,
      bytes: bytes.length,
      type: attachment.content_type,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
      output,
    }),
  );
}

if (require.main === module)
  main(process.argv.slice(2)).catch(() => {
    console.error(
      "Image read failed. Check the file ID, Inkdrop login, network, and unused output path.",
    );
    process.exitCode = 1;
  });
module.exports = { main };
