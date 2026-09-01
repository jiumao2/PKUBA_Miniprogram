import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const allowlistPath = resolve(root, "docs/dependency-audit-allowlist.json");
const GHSA_PATTERN = /^GHSA-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$/;
const ALLOWED_SCOPES = new Set(["development", "production-transitive"]);

function isValidIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value ?? "")) return false;
  return new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

function auditCounts(report, label) {
  const counts = report?.metadata?.vulnerabilities;
  if (!counts || typeof report?.vulnerabilities !== "object") {
    throw new Error(`${label} npm audit report is missing vulnerability metadata.`);
  }
  for (const severity of ["info", "low", "moderate", "high", "critical", "total"]) {
    if (!Number.isInteger(counts[severity]) || counts[severity] < 0) {
      throw new Error(`${label} npm audit report has an invalid ${severity} count.`);
    }
  }
  const severityTotal = counts.info + counts.low + counts.moderate + counts.high + counts.critical;
  if (counts.total !== severityTotal) {
    throw new Error(
      `${label} npm audit report total ${counts.total} does not match severity counts ${severityTotal}.`,
    );
  }
  return counts;
}

export function collectAdvisories(report, label = "Full") {
  const counts = auditCounts(report, label);
  const advisories = new Map();
  for (const vulnerability of Object.values(report.vulnerabilities)) {
    for (const via of vulnerability.via ?? []) {
      if (typeof via === "string") continue;
      const match = String(via?.url ?? "").match(/GHSA-[0-9a-z-]+/i);
      if (!match || !via?.name) {
        throw new Error(`${label} npm audit contains an advisory without a stable GHSA/package identity.`);
      }
      const id = match[0].toUpperCase();
      const packageName = String(via.name);
      const existing = advisories.get(id);
      if (existing && existing.package !== packageName) {
        throw new Error(`${label} npm audit maps ${id} to multiple packages.`);
      }
      advisories.set(id, { id, package: packageName });
    }
  }
  if (counts.total > 0 && advisories.size === 0) {
    throw new Error(`${label} npm audit cannot establish a stable GHSA/package identity for a non-zero report.`);
  }
  if (counts.total === 0 && advisories.size > 0) {
    throw new Error(`${label} npm audit contains advisories despite reporting zero vulnerabilities.`);
  }
  return advisories;
}

export function validateAllowlist(document, today = new Date().toISOString().slice(0, 10)) {
  if (document?.schema_version !== 2 || !Array.isArray(document.entries)) {
    throw new Error("Dependency audit allowlist must use schema_version 2 with an entries array.");
  }
  const allowed = new Map();
  for (const entry of document.entries) {
    const id = String(entry?.id ?? "").toUpperCase();
    const requiredText = ["package", "dependency_path", "command_boundary", "reason"];
    if (
      !GHSA_PATTERN.test(id)
      || !isValidIsoDate(entry?.expires_on)
      || !ALLOWED_SCOPES.has(entry?.scope)
      || requiredText.some((field) => typeof entry?.[field] !== "string" || !entry[field].trim())
    ) {
      throw new Error(`Invalid dependency audit allowlist entry: ${JSON.stringify(entry)}`);
    }
    if (allowed.has(id)) {
      throw new Error(`Duplicate dependency audit allowlist entry: ${id}`);
    }
    if (entry.expires_on < today) {
      throw new Error(`Dependency audit exception expired: ${id} (${entry.expires_on}).`);
    }
    allowed.set(id, { ...entry, id });
  }
  return allowed;
}

export function evaluateAuditPolicy({ productionReport, fullReport, allowlist, today }) {
  const productionCounts = auditCounts(productionReport, "Production");
  const fullCounts = auditCounts(fullReport, "Full");
  if (productionCounts.critical > 0 || productionCounts.high > 0) {
    throw new Error(
      `Production dependency audit failed: ${productionCounts.critical} critical, ${productionCounts.high} high.`,
    );
  }

  const allowed = validateAllowlist(allowlist, today);
  const productionAdvisories = collectAdvisories(productionReport, "Production");
  const fullAdvisories = collectAdvisories(fullReport, "Full");

  for (const id of productionAdvisories.keys()) {
    if (!fullAdvisories.has(id)) {
      throw new Error(`Production advisory ${id} is absent from the full audit report.`);
    }
  }
  const missing = [...fullAdvisories.keys()].filter((id) => !allowed.has(id));
  if (missing.length > 0) {
    throw new Error(`Unreviewed dependency advisories: ${missing.join(", ")}`);
  }
  const stale = [...allowed.keys()].filter((id) => !fullAdvisories.has(id));
  if (stale.length > 0) {
    throw new Error(`Stale dependency audit exceptions must be removed: ${stale.join(", ")}`);
  }
  for (const [id, advisory] of fullAdvisories) {
    const entry = allowed.get(id);
    if (entry.package !== advisory.package) {
      throw new Error(
        `Dependency audit exception package mismatch for ${id}: expected ${advisory.package}, got ${entry.package}.`,
      );
    }
    const expectedScope = productionAdvisories.has(id) ? "production-transitive" : "development";
    if (entry.scope !== expectedScope) {
      throw new Error(
        `Dependency audit exception scope mismatch for ${id}: expected ${expectedScope}, got ${entry.scope}.`,
      );
    }
  }

  return {
    productionCounts,
    fullCounts,
    productionAdvisories: productionAdvisories.size,
    fullAdvisories: fullAdvisories.size,
  };
}

function runNpmAudit({ omitDev }) {
  const npmCli = process.env.npm_execpath;
  if (!npmCli) throw new Error("npm_execpath is unavailable; run this check through npm run.");
  const args = [npmCli, "audit", ...(omitDev ? ["--omit=dev"] : []), "--json"];
  const audit = spawnSync(process.execPath, args, {
    cwd: root,
    encoding: "utf8",
    shell: false,
    maxBuffer: 16 * 1024 * 1024,
  });
  if (audit.error) throw audit.error;
  if (!audit.stdout?.trim()) {
    throw new Error(audit.stderr || "npm audit did not return JSON.");
  }
  let report;
  try {
    report = JSON.parse(audit.stdout);
  } catch (error) {
    throw new Error(`Cannot parse npm audit output: ${error}`);
  }
  if (![0, 1].includes(audit.status)) {
    throw new Error(audit.stderr || `npm audit failed with status ${audit.status}.`);
  }
  return report;
}

function main() {
  try {
    const allowlist = JSON.parse(readFileSync(allowlistPath, "utf8"));
    const result = evaluateAuditPolicy({
      productionReport: runNpmAudit({ omitDev: true }),
      fullReport: runNpmAudit({ omitDev: false }),
      allowlist,
    });
    console.log(
      `Dependency audit accepted: production ${result.productionCounts.critical} critical/${result.productionCounts.high} high; `
      + `full tree ${result.fullCounts.total} vulnerable packages across ${result.fullAdvisories} reviewed advisories.`,
    );
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
