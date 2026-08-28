import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const assetCopies = [
  ['packages/design-tokens/src/assets/pkuba-logo.png', 'apps/admin-web/public/pkuba-logo.png'],
  ['packages/design-tokens/src/assets/pkuba-logo.png', 'apps/miniapp/src/assets/pkuba-logo.png'],
  ['apps/api/core/assets/scoresheet/template_definition.json', 'apps/admin-web/shared/template_definition.json'],
];

export function staticAssets(root, sync = false) {
  for (const [canonical, target] of assetCopies) {
    const from = resolve(root, canonical), to = resolve(root, target);
    if (sync) { mkdirSync(dirname(to), { recursive: true }); copyFileSync(from, to); }
    if (!readFileSync(from).equals(readFileSync(to))) {
      throw new Error(`Static asset drift: ${target}; update ${canonical}, then run npm run assets:sync.`);
    }
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mode = process.argv[2];
  if (!['--check', '--sync'].includes(mode)) throw new Error('Use --check or --sync');
  staticAssets(resolve(dirname(fileURLToPath(import.meta.url)), '..'), mode === '--sync');
  console.log(`Static assets ${mode === '--sync' ? 'synchronized and checked' : 'match canonical sources'}.`);
}
