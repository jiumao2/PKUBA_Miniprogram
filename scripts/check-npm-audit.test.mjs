import test from "node:test";
import assert from "node:assert/strict";

import {
  deriveProductionReport,
  evaluateAuditPolicy,
  runNpmAudit,
  validateAllowlist,
} from "./check-npm-audit.mjs";

const TODAY = "2026-09-01";

function advisory(id, packageName, severity = "moderate") {
  return {
    source: Number(id.replace(/\D/g, "").slice(0, 8) || 1),
    name: packageName,
    severity,
    title: `${packageName} advisory`,
    url: `https://github.com/advisories/${id}`,
  };
}

function report(advisories, counts = {}) {
  const vulnerabilities = {};
  advisories.forEach((item, index) => {
    vulnerabilities[`package-${index}`] = { via: [item] };
  });
  const high = counts.high ?? 0;
  const critical = counts.critical ?? 0;
  const moderate = counts.moderate ?? advisories.length;
  return {
    metadata: {
      vulnerabilities: {
        info: 0,
        low: 0,
        moderate,
        high,
        critical,
        total: counts.total ?? moderate + high + critical,
      },
    },
    vulnerabilities,
  };
}

function exception(id, packageName, scope = "development") {
  return {
    id,
    package: packageName,
    scope,
    dependency_path: `tool > ${packageName}`,
    command_boundary: "Only the reviewed local tool command can reach this package.",
    expires_on: "2026-11-30",
    reason: "The affected behavior is outside PKUBA build and runtime entrypoints.",
  };
}

function policy({ production = [], full = production, entries = full, productionCounts } = {}) {
  return evaluateAuditPolicy({
    productionReport: report(production, productionCounts),
    fullReport: report(full),
    allowlist: {
      schema_version: 2,
      entries: entries.map((item) => exception(
        item.url.split("/").at(-1),
        item.name,
        production.some((productionItem) => productionItem.url === item.url)
          ? "production-transitive"
          : "development",
      )),
    },
    today: TODAY,
  });
}

test("accepts reviewed production and development advisories", () => {
  const production = [advisory("GHSA-AAAA-BBBB-CCCC", "prod-tool")];
  const full = [...production, advisory("GHSA-DDDD-EEEE-FFFF", "dev-tool", "high")];
  const result = policy({ production, full });
  assert.equal(result.productionAdvisories, 1);
  assert.equal(result.fullAdvisories, 2);
});

test("never allows production high or critical advisories", () => {
  const item = advisory("GHSA-AAAA-BBBB-CCCC", "prod-tool", "high");
  assert.throws(
    () => policy({ production: [item], productionCounts: { high: 1, moderate: 0, total: 1 } }),
    /Production dependency audit failed/,
  );
});

test("fails closed on a new unreviewed full-tree advisory", () => {
  const reviewed = advisory("GHSA-AAAA-BBBB-CCCC", "reviewed-tool");
  const added = advisory("GHSA-DDDD-EEEE-FFFF", "new-tool");
  assert.throws(() => policy({ full: [reviewed, added], entries: [reviewed] }), /Unreviewed dependency/);
});

test("rejects expired, duplicate, and incomplete exceptions", () => {
  const valid = exception("GHSA-AAAA-BBBB-CCCC", "tool");
  assert.throws(
    () => validateAllowlist({ schema_version: 2, entries: [{ ...valid, expires_on: "2026-08-31" }] }, TODAY),
    /expired/,
  );
  assert.throws(
    () => validateAllowlist({ schema_version: 2, entries: [valid, valid] }, TODAY),
    /Duplicate/,
  );
  assert.throws(
    () => validateAllowlist({ schema_version: 2, entries: [{ ...valid, command_boundary: "" }] }, TODAY),
    /Invalid/,
  );
  assert.throws(
    () => validateAllowlist({ schema_version: 2, entries: [{ ...valid, expires_on: "2026-02-30" }] }, TODAY),
    /Invalid/,
  );
});

test("rejects stale exceptions and package mismatches", () => {
  const item = advisory("GHSA-AAAA-BBBB-CCCC", "actual-tool");
  const stale = advisory("GHSA-DDDD-EEEE-FFFF", "stale-tool");
  assert.throws(() => policy({ full: [item], entries: [item, stale] }), /Stale dependency/);
  assert.throws(
    () => evaluateAuditPolicy({
      productionReport: report([]),
      fullReport: report([item]),
      allowlist: { schema_version: 2, entries: [exception("GHSA-AAAA-BBBB-CCCC", "wrong-tool")] },
      today: TODAY,
    }),
    /package mismatch/,
  );
});

test("rejects audit findings without stable GHSA identities", () => {
  const malformed = { name: "tool", url: "https://example.test/advisory/1" };
  assert.throws(() => policy({ full: [malformed], entries: [] }), /without a stable GHSA/);
});

test("rejects non-zero audit reports containing only transitive string references", () => {
  const malformed = report([]);
  malformed.metadata.vulnerabilities.moderate = 1;
  malformed.metadata.vulnerabilities.total = 1;
  malformed.vulnerabilities.wrapper = { via: ["missing-root-advisory"] };
  assert.throws(
    () => evaluateAuditPolicy({
      productionReport: report([]),
      fullReport: malformed,
      allowlist: { schema_version: 2, entries: [] },
      today: TODAY,
    }),
    /cannot establish a stable GHSA\/package identity/,
  );
});

test("rejects inconsistent severity totals and advisories with zero counts", () => {
  const inconsistent = report([]);
  inconsistent.metadata.vulnerabilities.moderate = 1;
  assert.throws(
    () => evaluateAuditPolicy({
      productionReport: report([]),
      fullReport: inconsistent,
      allowlist: { schema_version: 2, entries: [] },
      today: TODAY,
    }),
    /does not match severity counts/,
  );

  const item = advisory("GHSA-AAAA-BBBB-CCCC", "tool");
  const zeroWithAdvisory = report([item], { moderate: 0, total: 0 });
  assert.throws(
    () => evaluateAuditPolicy({
      productionReport: report([]),
      fullReport: zeroWithAdvisory,
      allowlist: { schema_version: 2, entries: [exception("GHSA-AAAA-BBBB-CCCC", "tool")] },
      today: TODAY,
    }),
    /contains advisories despite reporting zero vulnerabilities/,
  );
});

test("requires production-transitive scope for advisories in the production audit", () => {
  const item = advisory("GHSA-AAAA-BBBB-CCCC", "prod-tool");
  assert.throws(
    () => evaluateAuditPolicy({
      productionReport: report([item]),
      fullReport: report([item]),
      allowlist: { schema_version: 2, entries: [exception("GHSA-AAAA-BBBB-CCCC", "prod-tool")] },
      today: TODAY,
    }),
    /scope mismatch/,
  );
});

test("derives production scope from the same full report and exact lockfile locations", () => {
  const prodAdvisory = advisory("GHSA-AAAA-BBBB-CCCC", "prod-leaf");
  const devAdvisory = advisory("GHSA-DDDD-EEEE-FFFF", "dev-leaf", "high");
  const fullReport = {
    metadata: {
      vulnerabilities: { info: 0, low: 0, moderate: 2, high: 1, critical: 0, total: 3 },
    },
    vulnerabilities: {
      "prod-wrapper": {
        severity: "moderate",
        via: ["prod-leaf"],
        nodes: ["node_modules/prod-wrapper"],
      },
      "prod-leaf": {
        severity: "moderate",
        via: [prodAdvisory],
        nodes: ["node_modules/prod-leaf"],
      },
      "dev-leaf": {
        severity: "high",
        via: [devAdvisory],
        nodes: ["node_modules/dev-leaf"],
      },
    },
  };
  const productionReport = deriveProductionReport(fullReport, {
    packages: {
      "node_modules/prod-wrapper": {},
      "node_modules/prod-leaf": {},
      "node_modules/dev-leaf": { dev: true },
    },
  });

  assert.deepEqual(Object.keys(productionReport.vulnerabilities), ["prod-wrapper", "prod-leaf"]);
  assert.deepEqual(productionReport.metadata.vulnerabilities, {
    info: 0,
    low: 0,
    moderate: 2,
    high: 0,
    critical: 0,
    total: 2,
  });
});

test("fails closed when full audit locations cannot be classified from the lockfile", () => {
  const fullReport = {
    metadata: {
      vulnerabilities: { info: 0, low: 0, moderate: 1, high: 0, critical: 0, total: 1 },
    },
    vulnerabilities: {
      tool: {
        severity: "moderate",
        via: [advisory("GHSA-AAAA-BBBB-CCCC", "tool")],
        nodes: ["node_modules/tool"],
      },
    },
  };
  assert.throws(
    () => deriveProductionReport(fullReport, { packages: {} }),
    /location is absent from package-lock.json/,
  );
});

test("retries malformed registry responses without weakening the audit result", () => {
  const accepted = report([]);
  const results = [
    {
      status: 1,
      stdout: JSON.stringify({
        message: "network timeout at the official audit endpoint",
        error: { summary: "", detail: "" },
      }),
      stderr: "",
    },
    { status: 0, stdout: JSON.stringify(accepted), stderr: "" },
  ];
  const calls = [];
  const actual = runNpmAudit({
    npmCli: "/npm-cli.js",
    spawn: (...args) => {
      calls.push(args);
      return results.shift();
    },
    onRetry: () => undefined,
  });

  assert.deepEqual(actual, accepted);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][1].includes("--omit=dev"), false);
  assert.ok(calls[0][1].includes("--fetch-timeout=45000"));
  assert.ok(calls[0][1].includes("--fetch-retries=0"));
  assert.equal(calls[0][2].timeout, 60_000);
});

test("fails closed after bounded invalid audit responses", () => {
  let attempts = 0;
  assert.throws(
    () => runNpmAudit({
      npmCli: "/npm-cli.js",
      spawn: () => {
        attempts += 1;
        return {
          status: 1,
          stdout: JSON.stringify({ message: "registry unavailable" }),
          stderr: "",
        };
      },
      onRetry: () => undefined,
    }),
    /Full npm audit failed closed after 3 attempts: registry unavailable/,
  );
  assert.equal(attempts, 3);
});
