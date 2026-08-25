import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";

const WEAPP_WEBPACK_CACHE_NAMES = new Set(["production-weapp", "development-weapp"]);

export function clearWeappWebpackCaches(appRoot, cacheNames) {
  const cacheRoot = join(appRoot, "node_modules", ".cache", "webpack");
  for (const cacheName of cacheNames) {
    if (!WEAPP_WEBPACK_CACHE_NAMES.has(cacheName)) {
      throw new Error(`拒绝清理未知微信构建缓存：${cacheName}`);
    }
    rmSync(join(cacheRoot, cacheName), {
      recursive: true,
      force: true,
      maxRetries: 5,
      retryDelay: 200,
    });
  }
}

export function listFiles(root, current = root) {
  if (!existsSync(current)) return [];
  return readdirSync(current, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = join(current, entry.name);
    return entry.isDirectory() ? listFiles(root, fullPath) : [relative(root, fullPath)];
  });
}

function registeredPages(appConfig) {
  const rootPages = Array.isArray(appConfig.pages) ? appConfig.pages : [];
  const packagePages = (appConfig.subPackages ?? appConfig.subpackages ?? []).flatMap(
    (subpackage) => (subpackage.pages ?? []).map((page) => `${subpackage.root}/${page}`),
  );
  return [...rootPages, ...packagePages];
}

export function validateWeappOutput(root) {
  const appJsonPath = join(root, "app.json");
  if (!existsSync(appJsonPath)) {
    throw new Error("微信小程序构建缺少 app.json。");
  }
  const appConfig = JSON.parse(readFileSync(appJsonPath, "utf8"));
  const pages = registeredPages(appConfig);
  if (!pages.length) throw new Error("微信小程序 app.json 未注册任何页面。");

  for (const page of pages) {
    for (const extension of [".js", ".json", ".wxml"]) {
      const artifact = join(root, `${page}${extension}`);
      if (!existsSync(artifact) || statSync(artifact).size === 0) {
        throw new Error(`注册页面 ${page} 缺少有效的 ${extension} 产物。`);
      }
    }
    const javascriptPath = join(root, `${page}.js`);
    const javascript = readFileSync(javascriptPath, "utf8");
    if (Buffer.byteLength(javascript) < 1024) {
      throw new Error(`注册页面 ${page} 的 JavaScript 产物异常过小。`);
    }
    if (!/\bPage\(/.test(javascript)) {
      throw new Error(`注册页面 ${page} 的 JavaScript 产物没有注册 Page。`);
    }
    if (/(?:^|[,{])\d+:function\(\)\{\}(?:[,}])/.test(javascript)) {
      throw new Error(`注册页面 ${page} 的组件模块为空。`);
    }
  }
  return pages;
}

export function syncWeappOutput(sourceRoot, outputRoot) {
  const sourceFiles = listFiles(sourceRoot);
  const sourceSet = new Set(sourceFiles);
  mkdirSync(outputRoot, { recursive: true });
  for (const outputFile of listFiles(outputRoot)) {
    if (!sourceSet.has(outputFile) && outputFile !== "app.json") {
      rmSync(join(outputRoot, outputFile), { force: true });
    }
  }
  for (const sourceFile of sourceFiles.filter((file) => file !== "app.json")) {
    const destination = join(outputRoot, sourceFile);
    mkdirSync(dirname(destination), { recursive: true });
    copyFileSync(join(sourceRoot, sourceFile), destination);
  }
  copyFileSync(join(sourceRoot, "app.json"), join(outputRoot, "app.json"));
}

export function outputFingerprint(root) {
  return listFiles(root)
    .sort()
    .map((file) => {
      const stat = statSync(join(root, file));
      return `${file}:${stat.size}:${stat.mtimeMs}`;
    })
    .join("|");
}
