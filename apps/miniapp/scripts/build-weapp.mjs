import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const stagingRoot = join(appRoot, ".dist-staging");
const outputRoot = join(appRoot, "dist");
const cliRoot = dirname(require.resolve("@tarojs/cli/package.json"));
const cliPath = join(cliRoot, "bin", "taro");

rmSync(stagingRoot, { recursive: true, force: true });
const build = spawnSync(process.execPath, [cliPath, "build", "--type", "weapp"], {
  cwd: appRoot,
  env: { ...process.env, PKUBA_MINIAPP_OUTPUT_ROOT: ".dist-staging" },
  stdio: "inherit",
});
if (build.error) throw build.error;
if (build.status !== 0) process.exit(build.status ?? 1);

const stagedAppJson = join(stagingRoot, "app.json");
if (!existsSync(stagedAppJson)) {
  throw new Error("微信小程序暂存构建缺少 app.json，未更新 dist。");
}

function listFiles(root, current = root) {
  if (!existsSync(current)) return [];
  return readdirSync(current, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = join(current, entry.name);
    return entry.isDirectory() ? listFiles(root, fullPath) : [relative(root, fullPath)];
  });
}

const stagedFiles = listFiles(stagingRoot);
const stagedSet = new Set(stagedFiles);
mkdirSync(outputRoot, { recursive: true });
for (const outputFile of listFiles(outputRoot)) {
  if (!stagedSet.has(outputFile) && outputFile !== "app.json") {
    rmSync(join(outputRoot, outputFile), { force: true });
  }
}
for (const stagedFile of stagedFiles.filter((file) => file !== "app.json")) {
  const destination = join(outputRoot, stagedFile);
  mkdirSync(dirname(destination), { recursive: true });
  copyFileSync(join(stagingRoot, stagedFile), destination);
}
copyFileSync(stagedAppJson, join(outputRoot, "app.json"));
rmSync(stagingRoot, { recursive: true, force: true });
console.log("微信小程序已完整构建并同步到 dist；app.json 在同步过程中始终可用。");
