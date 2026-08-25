import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const stagingRoot = join(appRoot, ".dist-staging");
const outputRoot = join(appRoot, "dist");
const cliRoot = dirname(require.resolve("@tarojs/cli/package.json"));
const cliPath = join(cliRoot, "bin", "taro");

const apiBaseUrl = process.env.PKUBA_API_BASE_URL?.trim();
const adminWebUrl = process.env.PKUBA_ADMIN_WEB_URL?.trim();
if (!apiBaseUrl) {
  throw new Error(
    "微信小程序完整构建必须显式设置 PKUBA_API_BASE_URL；拒绝把本地地址写入候选包。",
  );
}
if (!adminWebUrl) {
  throw new Error(
    "微信小程序完整构建必须显式设置 PKUBA_ADMIN_WEB_URL；拒绝把错误的后台地址写入候选包。",
  );
}
let parsedApiBaseUrl;
let parsedAdminWebUrl;
try {
  parsedApiBaseUrl = new URL(apiBaseUrl);
  parsedAdminWebUrl = new URL(adminWebUrl);
} catch {
  throw new Error("PKUBA_API_BASE_URL 或 PKUBA_ADMIN_WEB_URL 不是有效的绝对 URL。");
}
const allowInsecureLocal = process.env.PKUBA_ALLOW_INSECURE_MINIAPP_URL === "1";
const localHostname = ["localhost", "127.0.0.1", "::1"].includes(
  parsedApiBaseUrl.hostname,
);
const localAdminHostname = ["localhost", "127.0.0.1", "::1"].includes(
  parsedAdminWebUrl.hostname,
);
if (
  !allowInsecureLocal
  && (
    parsedApiBaseUrl.protocol !== "https:"
    || localHostname
    || parsedAdminWebUrl.protocol !== "https:"
    || localAdminHostname
  )
) {
  throw new Error(
    "候选小程序只能使用非本机 HTTPS API 和管理后台；本地构建需显式设置 PKUBA_ALLOW_INSECURE_MINIAPP_URL=1。",
  );
}
if (!allowInsecureLocal) {
  const allowedHosts = new Set(
    (process.env.PKUBA_ALLOWED_MINIAPP_HOSTS ?? "api.pkuba.cn,admin.pkuba.cn")
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean),
  );
  for (const [label, parsed] of [
    ["API", parsedApiBaseUrl],
    ["管理后台", parsedAdminWebUrl],
  ]) {
    if (!allowedHosts.has(parsed.hostname.toLowerCase())) {
      throw new Error(`${label} 域名 ${parsed.hostname} 不在 PKUBA_ALLOWED_MINIAPP_HOSTS 中。`);
    }
  }
}

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
if (!allowInsecureLocal) {
  const textExtensions = new Set([".js", ".json", ".wxml", ".wxss", ".sitemap"]);
  const forbidden = [
    /http:\/\/localhost(?=[:/"'])/i,
    /http:\/\/127\.0\.0\.1(?=[:/"'])/i,
    /http:\/\/\[::1\](?=[:/"'])/i,
    /\b(?:WECHAT_APP_SECRET|QWEN_API_KEY|DJANGO_SECRET_KEY|POSTGRES_PASSWORD|EMAIL_HOST_PASSWORD)\b/,
  ];
  for (const stagedFile of stagedFiles) {
    const extension = stagedFile.slice(stagedFile.lastIndexOf("."));
    if (!textExtensions.has(extension)) continue;
    const content = readFileSync(join(stagingRoot, stagedFile), "utf8");
    const match = forbidden.find((pattern) => pattern.test(content));
    if (match) {
      throw new Error(`候选小程序产物 ${stagedFile} 包含禁止内容：${match}`);
    }
  }
}
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
