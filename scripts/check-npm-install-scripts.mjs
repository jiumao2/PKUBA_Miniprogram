import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packagePath = resolve(root, "package.json");
const packageLockPath = resolve(root, "package-lock.json");
const npmrcPath = resolve(root, ".npmrc");
const REQUIRED_NPM_ENGINE = ">=11.16.0 <12";
const EXACT_PACKAGE_VERSION = /^(?:@[^/]+\/[^@]+|[^@/][^@]*)@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

// Only esbuild's pinned binary validation scripts are required. The denied
// scripts are platform/source-build-only, informational, or can perform
// unpinned network installation; changing any decision requires another source review.
export const REVIEWED_INSTALL_SCRIPT_POLICY = Object.freeze({
  "@parcel/watcher@2.6.0": false,
  "@swc/core@1.3.96": false,
  "@tarojs/binding@4.2.1": false,
  "@tarojs/cli@4.2.1": false,
  "core-js@3.50.0": false,
  "core-js-pure@3.50.0": false,
  "esbuild@0.18.20": true,
  "esbuild@0.21.5": true,
  "esbuild@0.28.2": true,
  "fsevents@2.3.2": false,
  "fsevents@2.3.3": false,
});

export function validateProjectNpmrc(contents) {
  const required = new Set(["engine-strict", "strict-allow-scripts"]);
  const seen = new Map();
  for (const [index, rawLine] of String(contents).split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || line.startsWith(";")) continue;
    const separator = line.indexOf("=");
    if (separator < 1) {
      throw new Error(`Invalid project .npmrc entry on line ${index + 1}.`);
    }
    const key = line.slice(0, separator).trim().toLowerCase();
    const value = line.slice(separator + 1).trim().toLowerCase();
    if (key === "dangerously-allow-all-scripts") {
      throw new Error("Project .npmrc must not declare dangerously-allow-all-scripts.");
    }
    if (!required.has(key)) continue;
    if (seen.has(key)) {
      throw new Error(`Project .npmrc contains duplicate ${key} entries.`);
    }
    if (value !== "true") {
      throw new Error(`Project .npmrc must set ${key}=true.`);
    }
    seen.set(key, value);
  }
  const missing = [...required].filter((key) => !seen.has(key));
  if (missing.length > 0) {
    throw new Error(`Project .npmrc is missing required settings: ${missing.join(", ")}.`);
  }
}

export function validatePackageLockEngine(packageLockDocument) {
  const value = packageLockDocument?.packages?.[""]?.engines?.npm;
  if (value !== REQUIRED_NPM_ENGINE) {
    throw new Error(
      `package-lock.json root npm engine must be ${REQUIRED_NPM_ENGINE}; got ${value ?? "missing"}.`,
    );
  }
}

export function validateInstallScriptPolicy(packageDocument, pendingPackages = []) {
  if (packageDocument?.engines?.npm !== REQUIRED_NPM_ENGINE) {
    throw new Error(
      `package.json npm engine must be ${REQUIRED_NPM_ENGINE}; got ${packageDocument?.engines?.npm ?? "missing"}.`,
    );
  }
  const policy = packageDocument?.allowScripts;
  if (!policy || typeof policy !== "object" || Array.isArray(policy)) {
    throw new Error("package.json must define an allowScripts object.");
  }

  const actualKeys = Object.keys(policy).sort();
  const expectedKeys = Object.keys(REVIEWED_INSTALL_SCRIPT_POLICY).sort();
  for (const key of actualKeys) {
    if (!EXACT_PACKAGE_VERSION.test(key) || typeof policy[key] !== "boolean") {
      throw new Error(`Install-script policy entry must be an exact package version and boolean: ${key}`);
    }
  }

  const missing = expectedKeys.filter((key) => !Object.hasOwn(policy, key));
  const unexpected = actualKeys.filter((key) => !Object.hasOwn(REVIEWED_INSTALL_SCRIPT_POLICY, key));
  const changed = expectedKeys.filter(
    (key) => Object.hasOwn(policy, key) && policy[key] !== REVIEWED_INSTALL_SCRIPT_POLICY[key],
  );
  if (missing.length || unexpected.length || changed.length) {
    throw new Error(
      `Install-script policy differs from the reviewed set: missing [${missing.join(", ")}], `
      + `unexpected [${unexpected.join(", ")}], changed [${changed.join(", ")}].`,
    );
  }

  if (!Array.isArray(pendingPackages)) {
    throw new Error("npm pending install-script output is malformed.");
  }
  if (pendingPackages.length > 0) {
    const identities = pendingPackages.map((entry) => (
      typeof entry === "string" ? entry : entry?.path ?? entry?.name ?? JSON.stringify(entry)
    ));
    throw new Error(`Unreviewed dependency install scripts: ${identities.join(", ")}`);
  }

  return {
    approved: expectedKeys.filter((key) => policy[key]).length,
    denied: expectedKeys.filter((key) => !policy[key]).length,
  };
}

export function parsePendingInstallScriptOutput(stdout) {
  const value = String(stdout).trim();
  if (value === "No packages with unreviewed install scripts.") return [];
  let output;
  try {
    output = JSON.parse(value);
  } catch (error) {
    throw new Error(`Cannot parse npm pending install-script output: ${error}`);
  }
  if (!Array.isArray(output?.allowScripts)) {
    throw new Error("npm pending install-script output is malformed.");
  }
  return output.allowScripts;
}

function readPendingInstallScripts() {
  const npmCli = process.env.npm_execpath;
  if (!npmCli) throw new Error("npm_execpath is unavailable; run this check through npm run.");
  const result = spawnSync(
    process.execPath,
    [npmCli, "approve-scripts", "--allow-scripts-pending", "--json"],
    {
      cwd: root,
      encoding: "utf8",
      shell: false,
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(result.stderr || `npm approve-scripts failed with status ${result.status}.`);
  }
  return parsePendingInstallScriptOutput(result.stdout);
}

function main() {
  try {
    const packageDocument = JSON.parse(readFileSync(packagePath, "utf8"));
    const packageLockDocument = JSON.parse(readFileSync(packageLockPath, "utf8"));
    validateProjectNpmrc(readFileSync(npmrcPath, "utf8"));
    validatePackageLockEngine(packageLockDocument);
    const result = validateInstallScriptPolicy(packageDocument, readPendingInstallScripts());
    console.log(
      `Dependency install-script policy accepted: ${result.approved} approved, ${result.denied} denied, 0 pending.`,
    );
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
