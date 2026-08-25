import { spawn, spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import {
  outputFingerprint,
  syncWeappOutput,
  validateWeappOutput,
} from "./weapp-artifacts.mjs";

const require = createRequire(import.meta.url);
const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const stagingRoot = join(appRoot, ".dist-watch-staging");
const outputRoot = join(appRoot, "dist");
const cliRoot = dirname(require.resolve("@tarojs/cli/package.json"));
const cliPath = join(cliRoot, "bin", "taro");

const initial = spawnSync(process.execPath, [join(appRoot, "scripts", "build-weapp.mjs")], {
  cwd: appRoot,
  env: process.env,
  stdio: "inherit",
});
if (initial.error) throw initial.error;
if (initial.status !== 0) process.exit(initial.status ?? 1);

rmSync(stagingRoot, { recursive: true, force: true });
const watcher = spawn(process.execPath, [cliPath, "build", "--type", "weapp", "--watch"], {
  cwd: appRoot,
  env: { ...process.env, PKUBA_MINIAPP_OUTPUT_ROOT: ".dist-watch-staging" },
  stdio: "inherit",
});

let lastSeen = "";
let lastSynced = "";
let stableTicks = 0;
const timer = setInterval(() => {
  let fingerprint;
  try {
    fingerprint = outputFingerprint(stagingRoot);
  } catch {
    return;
  }
  if (!fingerprint || fingerprint !== lastSeen) {
    lastSeen = fingerprint;
    stableTicks = 0;
    return;
  }
  stableTicks += 1;
  if (stableTicks < 2 || fingerprint === lastSynced) return;
  try {
    validateWeappOutput(stagingRoot);
    syncWeappOutput(stagingRoot, outputRoot);
    lastSynced = fingerprint;
    console.log("微信小程序 watch 产物校验通过，已原子同步到 dist。");
  } catch (error) {
    console.warn(`微信小程序 watch 暂存产物尚不可用，保留上一份 dist：${error.message}`);
  }
}, 500);

function stop(signal) {
  clearInterval(timer);
  watcher.kill(signal);
}
process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));
watcher.on("exit", (code) => {
  clearInterval(timer);
  rmSync(stagingRoot, { recursive: true, force: true });
  process.exit(code ?? 0);
});
