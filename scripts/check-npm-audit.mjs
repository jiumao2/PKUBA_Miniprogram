import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const allowlistPath = resolve(root, "docs/dependency-audit-allowlist.json");
const packageLockPath = resolve(root, "package-lock.json");
const GHSA_PATTERN = /^GHSA-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$/;
const ALLOWED_SCOPES = new Set(["development", "production-transitive"]);
const SEVERITIES = new Set(["info", "low", "moderate", "high", "critical"]);
const AUDIT_ATTEMPTS = 3;
const AUDIT_FETCH_TIMEOUT_MS = 45_000;
const AUDIT_PROCESS_TIMEOUT_MS = 60_000;

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
      if (!SEVERITIES.has(via.severity) || (existing && existing.severity !== via.severity)) {
        throw new Error(`${label} npm audit has an invalid or inconsistent severity for ${id}.`);
      }
      advisories.set(id, { id, package: packageName, severity: via.severity });
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
    if (entry.build_only && (
      !SEVERITIES.has(entry.build_only.severity)
      || !/^[a-f0-9]{64}$/.test(entry.build_only.lock_sha256 ?? "")
      || !/^[a-f0-9]{64}$/.test(entry.build_only.usage_sha256 ?? "")
    )) {
      throw new Error(`Invalid build-only dependency exception: ${id}.`);
    }
    allowed.set(id, { ...entry, id });
  }
  return allowed;
}

// Review-bound fingerprints, not a claim that npm's dev flag proves runtime reachability.
// Any lock change requires re-review; command/compiler/packaging changes do too.
export function buildOnlyContext(packageLock, usageFiles) {
  const hash = (value) => createHash("sha256").update(value).digest("hex");
  return {
    lock_sha256: hash(JSON.stringify(packageLock)),
    usage_sha256: hash(JSON.stringify(Object.entries(usageFiles).sort(([a], [b]) => a.localeCompare(b, "en"))
      .map(([path, contents]) => [path, contents.replace(/\r\n/g, "\n")]))),
  };
}

export function readBuildOnlyContext() {
  const usageFiles = {};
  for (const path of [
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    "apps/admin-web/vite.config.ts",
    "apps/miniapp/config/index.ts",
    "apps/miniapp/scripts/build-weapp.mjs",
    "apps/miniapp/scripts/watch-weapp.mjs",
    "infra/admin-web.Dockerfile",
  ]) usageFiles[path] = readFileSync(resolve(root, path), "utf8");
  // Lockfile records versions/dependency edges; scripts define the permitted commands.
  for (const path of ["package.json", "apps/miniapp/package.json", "apps/admin-web/package.json"]) {
    usageFiles[`${path}:scripts`] = JSON.stringify(JSON.parse(readFileSync(resolve(root, path), "utf8")).scripts);
  }
  return buildOnlyContext(JSON.parse(readFileSync(packageLockPath, "utf8")), usageFiles);
}

export function evaluateAuditPolicy({ productionReport, fullReport, allowlist, today, buildContext }) {
  const productionCounts = auditCounts(productionReport, "Production");
  const fullCounts = auditCounts(fullReport, "Full");
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
  const acceptedBuildOnly = new Set();
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
    if (entry.build_only) {
      if (entry.build_only.severity !== advisory.severity
        || entry.build_only.lock_sha256 !== buildContext?.lock_sha256
        || entry.build_only.usage_sha256 !== buildContext?.usage_sha256) {
        throw new Error(`Build-only exception requires re-review: ${id} (severity, dependencies or usage changed).`);
      }
      acceptedBuildOnly.add(id);
    }
  }

  // Check actual GHSA causes, not the count of packages inheriting the same cause.
  // A new high/critical cause in an already excepted package still blocks.
  const highProduction = [...productionAdvisories.values()]
    .filter((item) => ["high", "critical"].includes(item.severity));
  function causes(name, visited = new Set()) {
    if (visited.has(name)) return [];
    const node = productionReport.vulnerabilities[name];
    if (!node || !Array.isArray(node.via) || node.via.length === 0) {
      throw new Error(`Production audit cannot resolve advisory causes for ${name}.`);
    }
    const next = new Set([...visited, name]);
    return node.via.flatMap((via) => typeof via === "string" ? causes(via, next) : [via]);
  }
  for (const [name, node] of Object.entries(productionReport.vulnerabilities)) {
    if (["high", "critical"].includes(node.severity)) {
      const evidence = causes(name);
      const expected = node.severity === "critical" ? ["critical"] : ["high", "critical"];
      if (!evidence.some((via) => expected.includes(via.severity))) {
        throw new Error(`Production audit cannot explain ${node.severity} severity for ${name}.`);
      }
    }
  }
  if ((productionCounts.high + productionCounts.critical > 0 && highProduction.length === 0)
    || highProduction.some((item) => !acceptedBuildOnly.has(item.id))) {
    throw new Error(`Production dependency audit failed: ${productionCounts.critical} critical, ${productionCounts.high} high.`);
  }

  return {
    productionCounts,
    fullCounts,
    productionAdvisories: productionAdvisories.size,
    fullAdvisories: fullAdvisories.size,
    acceptedBuildOnly: [...acceptedBuildOnly],
  };
}

export function deriveProductionReport(fullReport, packageLock) {
  auditCounts(fullReport, "Full");
  const packages = packageLock?.packages;
  if (!packages || typeof packages !== "object" || Array.isArray(packages)) {
    throw new Error("package-lock.json must contain a packages map.");
  }

  const vulnerabilities = fullReport.vulnerabilities;
  const productionNames = new Set();
  for (const [name, vulnerability] of Object.entries(vulnerabilities)) {
    if (!Array.isArray(vulnerability?.nodes) || vulnerability.nodes.length === 0) {
      throw new Error(`Full npm audit cannot classify ${name} without installed node locations.`);
    }
    for (const location of vulnerability.nodes) {
      if (typeof location !== "string" || !Object.hasOwn(packages, location)) {
        throw new Error(`Full npm audit location is absent from package-lock.json: ${String(location)}`);
      }
      if (packages[location]?.dev !== true) {
        productionNames.add(name);
      }
    }
  }

  const pending = [...productionNames];
  while (pending.length > 0) {
    const name = pending.pop();
    for (const via of vulnerabilities[name]?.via ?? []) {
      if (typeof via !== "string") continue;
      if (!Object.hasOwn(vulnerabilities, via)) {
        throw new Error(`Full npm audit references an unknown vulnerability: ${via}`);
      }
      if (!productionNames.has(via)) {
        productionNames.add(via);
        pending.push(via);
      }
    }
  }

  const productionVulnerabilities = {};
  const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0, total: 0 };
  for (const [name, vulnerability] of Object.entries(vulnerabilities)) {
    if (!productionNames.has(name)) continue;
    if (!Object.hasOwn(counts, vulnerability?.severity) || vulnerability.severity === "total") {
      throw new Error(`Full npm audit has an invalid severity for ${name}.`);
    }
    productionVulnerabilities[name] = vulnerability;
    counts[vulnerability.severity] += 1;
    counts.total += 1;
  }

  return {
    ...fullReport,
    metadata: { ...fullReport.metadata, vulnerabilities: counts },
    vulnerabilities: productionVulnerabilities,
  };
}

function auditFailureReason(audit, report) {
  if (audit.error?.code) return `process error ${audit.error.code}`;
  const responseMessage = [
    report?.message,
    report?.error?.summary,
    report?.error?.detail,
  ].find((value) => typeof value === "string" && value.trim());
  if (responseMessage) return responseMessage.replace(/\s+/g, " ").trim().slice(0, 300);
  if (!audit.stdout?.trim()) return `exit ${String(audit.status)} without JSON output`;
  return `exit ${String(audit.status)} without vulnerability metadata`;
}

export function runNpmAudit({
  npmCli = process.env.npm_execpath,
  spawn = spawnSync,
  onRetry = (message) => console.warn(message),
}) {
  if (!npmCli) throw new Error("npm_execpath is unavailable; run this check through npm run.");
  const label = "Full";
  const args = [
    npmCli,
    "audit",
    "--json",
    `--fetch-timeout=${AUDIT_FETCH_TIMEOUT_MS}`,
    "--fetch-retries=0",
  ];
  let lastFailure = "unknown response";

  for (let attempt = 1; attempt <= AUDIT_ATTEMPTS; attempt += 1) {
    const audit = spawn(process.execPath, args, {
      cwd: root,
      encoding: "utf8",
      shell: false,
      maxBuffer: 16 * 1024 * 1024,
      timeout: AUDIT_PROCESS_TIMEOUT_MS,
    });
    let report = null;
    try {
      report = audit.stdout?.trim() ? JSON.parse(audit.stdout) : null;
    } catch {
      // The command result is invalid and must be retried or rejected below.
    }
    if (
      [0, 1].includes(audit.status)
      && report?.metadata?.vulnerabilities
      && typeof report?.vulnerabilities === "object"
    ) {
      return report;
    }
    lastFailure = auditFailureReason(audit, report);
    if (attempt < AUDIT_ATTEMPTS) {
      onRetry(
        `${label} npm audit attempt ${attempt}/${AUDIT_ATTEMPTS} returned no valid report: ${lastFailure}`,
      );
    }
  }

  throw new Error(
    `${label} npm audit failed closed after ${AUDIT_ATTEMPTS} attempts: ${lastFailure}`,
  );
}

function main() {
  try {
    const allowlist = JSON.parse(readFileSync(allowlistPath, "utf8"));
    const packageLock = JSON.parse(readFileSync(packageLockPath, "utf8"));
    const fullReport = runNpmAudit({});
    const result = evaluateAuditPolicy({
      productionReport: deriveProductionReport(fullReport, packageLock),
      fullReport,
      allowlist,
      buildContext: readBuildOnlyContext(),
    });
    console.log(
      `Dependency audit accepted with reviewed risks: npm non-dev graph ${result.productionCounts.critical} critical/${result.productionCounts.high} high; `
      + `full tree ${result.fullCounts.total} vulnerable packages across ${result.fullAdvisories} reviewed advisories; `
      + `build-only accepted risks [${result.acceptedBuildOnly.join(", ")}]. No claim of vulnerability remediation.`,
    );
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
