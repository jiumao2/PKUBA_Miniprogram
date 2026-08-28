import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { test } from 'node:test';
import { assetCopies, staticAssets } from './static-assets.mjs';

test('canonical assets detect drift and only explicit sync replaces a copy', () => {
  const root = mkdtempSync(join(tmpdir(), 'pkuba-static-assets-'));
  try {
    for (const [canonical] of assetCopies) {
      mkdirSync(dirname(join(root, canonical)), { recursive: true });
      writeFileSync(join(root, canonical), `synthetic-${canonical}`);
    }
    staticAssets(root, true);
    staticAssets(root);
    const [canonical, target] = assetCopies[0];
    writeFileSync(join(root, target), 'different-copy');
    assert.throws(() => staticAssets(root), /Static asset drift/);
    assert.equal(readFileSync(join(root, target), 'utf8'), 'different-copy');
    writeFileSync(join(root, canonical), 'new-canonical');
    staticAssets(root, true);
    staticAssets(root);
    assert.equal(readFileSync(join(root, target), 'utf8'), 'new-canonical');
    assert.equal(readFileSync(join(root, assetCopies[1][1]), 'utf8'), 'new-canonical');
  } finally {
    rmSync(root, { recursive: true, force: true }); // Only this test's mkdtemp tree.
  }
});
