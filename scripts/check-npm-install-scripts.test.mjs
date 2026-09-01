import test from "node:test";
import assert from "node:assert/strict";

import {
  REVIEWED_INSTALL_SCRIPT_POLICY,
  parsePendingInstallScriptOutput,
  validateInstallScriptPolicy,
  validatePackageLockEngine,
  validateProjectNpmrc,
} from "./check-npm-install-scripts.mjs";

function packageDocument(overrides = {}) {
  return {
    engines: { npm: ">=11.16.0 <12" },
    allowScripts: {
      ...REVIEWED_INSTALL_SCRIPT_POLICY,
      ...overrides,
    },
  };
}

test("accepts the exact reviewed install-script policy with no pending packages", () => {
  assert.deepEqual(
    validateInstallScriptPolicy(packageDocument(), []),
    { approved: 3, denied: 8 },
  );
});

test("requires npm 11.16 or newer in both package manifests", () => {
  assert.throws(
    () => validateInstallScriptPolicy({ ...packageDocument(), engines: { npm: ">=11 <12" } }, []),
    /package\.json npm engine must be >=11\.16\.0 <12/,
  );
  assert.doesNotThrow(() => validatePackageLockEngine({
    packages: { "": { engines: { npm: ">=11.16.0 <12" } } },
  }));
  assert.throws(
    () => validatePackageLockEngine({ packages: { "": { engines: { npm: ">=11 <12" } } } }),
    /package-lock\.json root npm engine must be >=11\.16\.0 <12/,
  );
});

test("requires both strict project npm settings and rejects bypasses", () => {
  const valid = "engine-strict=true\nstrict-allow-scripts=true\n";
  assert.doesNotThrow(() => validateProjectNpmrc(valid));
  assert.throws(() => validateProjectNpmrc("strict-allow-scripts=true\n"), /missing.*engine-strict/);
  assert.throws(
    () => validateProjectNpmrc("engine-strict=true\nstrict-allow-scripts=false\n"),
    /strict-allow-scripts=true/,
  );
  assert.throws(
    () => validateProjectNpmrc(`${valid}strict-allow-scripts=true\n`),
    /duplicate strict-allow-scripts/,
  );
  assert.throws(
    () => validateProjectNpmrc(`${valid}dangerously-allow-all-scripts=false\n`),
    /must not declare dangerously-allow-all-scripts/,
  );
});

test("accepts npm 11.16 and 11.17 empty pending formats but rejects other text", () => {
  assert.deepEqual(
    parsePendingInstallScriptOutput("No packages with unreviewed install scripts.\n"),
    [],
  );
  assert.deepEqual(parsePendingInstallScriptOutput('{"allowScripts":[]}\n'), []);
  assert.throws(
    () => parsePendingInstallScriptOutput("some unreviewed package"),
    /Cannot parse npm pending install-script output/,
  );
  assert.throws(
    () => parsePendingInstallScriptOutput('{"allowScripts":null}'),
    /pending install-script output is malformed/,
  );
});

test("rejects missing, unexpected, and changed install-script decisions", () => {
  const missing = packageDocument();
  delete missing.allowScripts["esbuild@0.28.2"];
  assert.throws(() => validateInstallScriptPolicy(missing, []), /missing \[esbuild@0\.28\.2\]/);
  assert.throws(
    () => validateInstallScriptPolicy(packageDocument({ "new-tool@1.0.0": true }), []),
    /unexpected \[new-tool@1\.0\.0\]/,
  );
  assert.throws(
    () => validateInstallScriptPolicy(packageDocument({ "@tarojs\/cli@4.2.1": true }), []),
    /changed \[@tarojs\/cli@4\.2\.1\]/,
  );
});

test("rejects unpinned or non-boolean policy entries", () => {
  assert.throws(
    () => validateInstallScriptPolicy(packageDocument({ "future-tool@^1.0.0": true }), []),
    /exact package version and boolean/,
  );
  assert.throws(
    () => validateInstallScriptPolicy(packageDocument({ "future-tool@1.0.0": "yes" }), []),
    /exact package version and boolean/,
  );
});

test("fails closed when npm reports an unreviewed install script", () => {
  assert.throws(
    () => validateInstallScriptPolicy(packageDocument(), [{ name: "new-tool@1.0.0" }]),
    /Unreviewed dependency install scripts: new-tool@1\.0\.0/,
  );
  assert.throws(
    () => validateInstallScriptPolicy(packageDocument(), null),
    /pending install-script output is malformed/,
  );
});
