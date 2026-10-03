"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const https = require("node:https");
const { execFile } = require("node:child_process");
const { configDir, settings, endpoint } = require("./runtime.cjs");
const MAX_OUTPUT = 2 * 1024 ** 2;
const MAX_DB_FILE = 8 * 1024 ** 3;

class CapacityError extends Error {}

function requestCapacity() {
  const url = endpoint();
  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        hostname: url.hostname,
        port: url.port || 443,
        path: url.pathname.replace(/\/$/, ""),
        method: "GET",
        auth:
          decodeURIComponent(url.username) +
          ":" +
          decodeURIComponent(url.password),
        headers: {
          "User-Agent": "InkdropImageSafety/0.1",
          Accept: "application/json",
        },
      },
      (res) => {
        let data = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          data += chunk;
          if (data.length > 65536)
            req.destroy(new Error("Database response exceeds size limit."));
        });
        res.on("error", reject);
        res.on("end", () => {
          try {
            if (res.statusCode !== 200)
              throw new Error("Database capacity check unavailable.");
            const bytes = JSON.parse(data).sizes?.file;
            if (!Number.isFinite(bytes) || bytes < 0)
              throw new CapacityError("Invalid database size response.");
            if (bytes >= MAX_DB_FILE)
              throw new CapacityError(
                "Database reached the 8 GiB safety limit. The original was kept locally.",
              );
            resolve(bytes);
          } catch (error) {
            reject(error);
          }
        });
      },
    );
    req.on("error", () =>
      reject(new Error("Database capacity check unavailable.")),
    );
    req.setTimeout(12000, () => req.destroy());
    req.end();
  });
}

function runWorker(data, type) {
  return new Promise((resolve, reject) => {
    const child = execFile(
      settings().node,
      [path.join(__dirname, "worker.cjs")],
      {
        timeout: 45000,
        maxBuffer: 6 * 1024 ** 2,
        env: { ...process.env, INKDROP_CONFIG_DIR: configDir() },
      },
      (error, stdout) => {
        try {
          const result = JSON.parse(stdout);
          if (!result.ok)
            throw new Error(result.error || "Image optimization failed.");
          if (error) throw new Error("Image worker did not finish.");
          resolve(result);
        } catch (failure) {
          reject(failure);
        }
      },
    );
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify({ data, type }));
  });
}

module.exports = function install(inkdrop) {
  if (globalThis.__inkdropImageSafety) return;
  const dir = path.join(configDir(), "image-safety");
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const state = (value) => {
    try {
      fs.writeFileSync(
        path.join(dir, "status.json"),
        JSON.stringify({ at: new Date().toISOString(), ...value }),
        { mode: 0o600 },
      );
    } catch {
      inkdrop.notifications.addWarning(
        "Could not update image safety status.",
        { dismissable: true },
      );
    }
  };
  const db = inkdrop.localDB;
  if (!db?.files?.put || !inkdrop.clipboard?.readImageAsDataURL)
    throw new Error("Image safety requires Inkdrop desktop 6.x.");
  const proto = Object.getPrototypeOf(db.files);
  const originalPut = proto.put;
  const originalClipboard = inkdrop.clipboard.saveAsImageAttachment;
  let queue = Promise.resolve();
  let lastWarning = 0;

  async function processDoc(receiver, doc) {
    const att = doc?._attachments?.index;
    if (
      doc?._rev ||
      !doc?._id?.startsWith("file:") ||
      typeof att?.data !== "string"
    )
      return originalPut.call(receiver, doc);
    const result = await runWorker(
      att.data,
      att.content_type || doc.contentType,
    );
    const payload = Buffer.from(result.data, "base64");
    if (payload.length > MAX_OUTPUT) throw new Error("Image exceeds 2 MiB.");
    let dbBytes = null;
    try {
      dbBytes = await requestCapacity();
    } catch (error) {
      if (error instanceof CapacityError) throw error;
      if (Date.now() - lastWarning > 600000) {
        lastWarning = Date.now();
        inkdrop.notifications.addWarning(
          "Image saved locally; server capacity could not be checked.",
          {
            detail:
              "Inkdrop will sync when reachable. Server storage and outbound guards still apply.",
            dismissable: true,
          },
        );
      }
    }
    const optimized = {
      ...doc,
      contentType: result.type,
      contentLength: payload.length,
      md5digest: crypto.createHash("md5").update(payload).digest("hex"),
      _attachments: { index: { content_type: result.type, data: result.data } },
    };
    const answer = await originalPut.call(receiver, optimized);
    Object.assign(doc, optimized);
    state({
      enabled: true,
      lastResult: {
        sourceBytes: result.sourceBytes || payload.length,
        outputBytes: payload.length,
        resized: !!result.resized,
        dbFileBytes: dbBytes,
        id: doc._id,
      },
    });
    return answer;
  }

  proto.put = function (doc) {
    if (
      doc?._rev ||
      !doc?._id?.startsWith("file:") ||
      typeof doc?._attachments?.index?.data !== "string"
    )
      return originalPut.call(this, doc);
    const job = queue.then(() => processDoc(this, doc));
    queue = job.catch(() => {});
    return job.catch((error) => {
      inkdrop.notifications.addError("Could not save image.", {
        detail: error.message,
        dismissable: true,
      });
      throw error;
    });
  };
  inkdrop.clipboard.saveAsImageAttachment = async function (options = {}) {
    try {
      const url = await inkdrop.clipboard.readImageAsDataURL();
      if (!url) return null;
      const match = url.match(
        /^data:(image\/[a-zA-Z0-9.+-]+);base64,([\s\S]+)$/,
      );
      if (!match) throw new Error("Unsupported clipboard image.");
      const bytes = Buffer.from(match[2], "base64");
      const doc = {
        _id: db.files.createId(),
        createdAt: Date.now(),
        name: "clipboard.png",
        contentType: match[1],
        contentLength: bytes.length,
        publicIn: options.publicIn || [],
        md5digest: crypto.createHash("md5").update(bytes).digest("hex"),
        _attachments: { index: { content_type: match[1], data: match[2] } },
      };
      await db.files.put(doc);
      return doc._id;
    } catch (error) {
      inkdrop.notifications.addError("Could not paste image.", {
        detail: error.message,
        dismissable: true,
      });
      return null;
    }
  };
  globalThis.__inkdropImageSafety = {
    version: 1,
    restore() {
      proto.put = originalPut;
      inkdrop.clipboard.saveAsImageAttachment = originalClipboard;
      delete globalThis.__inkdropImageSafety;
    },
  };
  state({
    enabled: true,
    version: 1,
    attachmentMaxBytes: MAX_OUTPUT,
    dbFileMaxBytes: MAX_DB_FILE,
  });
};
