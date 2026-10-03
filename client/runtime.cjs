"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function configDir() {
  if (process.env.INKDROP_CONFIG_DIR)
    return path.resolve(process.env.INKDROP_CONFIG_DIR);
  if (process.platform === "darwin")
    return path.join(os.homedir(), "Library", "Application Support", "inkdrop");
  if (process.platform === "win32")
    return path.join(
      process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"),
      "inkdrop",
    );
  return path.join(
    process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"),
    "inkdrop",
  );
}

function settings() {
  return JSON.parse(
    fs.readFileSync(
      path.join(configDir(), "image-safety", "runtime.json"),
      "utf8",
    ),
  );
}

function endpoint() {
  const config = JSON.parse(
    fs.readFileSync(path.join(configDir(), "config.json"), "utf8"),
  );
  const remote = (config["*"] || config).core?.db?.remote;
  const url = new URL(remote?.url);
  if (
    url.protocol !== "https:" ||
    !url.username ||
    !url.password ||
    url.pathname === "/"
  ) {
    throw new Error(
      "Configure an authenticated HTTPS database URL in Inkdrop first.",
    );
  }
  return url;
}

module.exports = { configDir, settings, endpoint };
