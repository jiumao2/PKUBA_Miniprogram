import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const allowlistPath = resolve(root, "docs/dependency-audit-allowlist.json");
const npmCli = process.env.npm_execpath;
if (!npmCli) throw new Error("npm_execpath is unavailable; run this check through npm run.");
const audit = spawnSync(process.execPath, [npmCli, "audit", "--omit=dev", "--json"], {
  cwd: root,
  encoding: "utf8",
  shell: false,
});

if (!audit.stdout?.trim()) {
  process.stderr.write(audit.stderr || "npm audit did not return JSON.\n");
  process.exit(2);
}

let report;
try {
  report = JSON.parse(audit.stdout);
} catch (error) {
  process.stderr.write(`Cannot parse npm audit output: ${error}\n`);
  process.exit(2);
}

const counts = report.metadata?.vulnerabilities ?? {};
if ((counts.critical ?? 0) > 0 || (counts.high ?? 0) > 0) {
  process.stderr.write(
    `Production dependency audit failed: ${counts.critical ?? 0} critical, ${counts.high ?? 0} high.\n`,
  );
  process.exit(1);
}

const allowlist = JSON.parse(readFileSync(allowlistPath, "utf8"));
const today = new Date().toISOString().slice(0, 10);
const allowed = new Map();
for (const entry of allowlist.entries ?? []) {
  if (!entry.id || !entry.reason || !/^\d{4}-\d{2}-\d{2}$/.test(entry.expires_on ?? "")) {
    throw new Error(`Invalid dependency audit allowlist entry: ${JSON.stringify(entry)}`);
  }
  if (entry.expires_on < today) {
    process.stderr.write(`Dependency audit exception expired: ${entry.id} (${entry.expires_on}).\n`);
    process.exit(1);
  }
  allowed.set(String(entry.id).toUpperCase(), entry);
}

const advisoryIds = new Set();
for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  for (const via of vulnerability.via ?? []) {
    if (typeof via !== "object" || !via.url) continue;
    const match = String(via.url).match(/(GHSA-[0-9a-z-]+)/i);
    if (match) advisoryIds.add(match[1].toUpperCase());
  }
}

const missing = [...advisoryIds].filter((id) => !allowed.has(id));
if (missing.length > 0) {
  process.stderr.write(`Unreviewed production dependency advisories: ${missing.join(", ")}\n`);
  process.exit(1);
}

console.log(
  `Production dependency audit accepted: 0 critical, 0 high, ${counts.moderate ?? 0} reviewed moderate advisories.`,
);
