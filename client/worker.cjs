"use strict";
const { optimize } = require("./optimizer.cjs");
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (s) => {
  input += s;
  if (input.length > 46 * 1024 * 1024) process.exit(2);
});
process.stdin.on("end", async () => {
  try {
    const request = JSON.parse(input);
    const result = await optimize(
      Buffer.from(request.data, "base64"),
      request.type,
    );
    const { buffer, ...stats } = result;
    process.stdout.write(
      JSON.stringify({ ok: true, data: buffer.toString("base64"), ...stats }),
    );
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: e.message }));
    process.exitCode = 1;
  }
});
