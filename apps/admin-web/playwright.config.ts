import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  outputDir: process.env.PKUBA_E2E_OUTPUT_DIR ?? '../../output/playwright/admin-web/test-results',
  fullyParallel: false,
  retries: 0,
  reporter: [
    ['list'],
    ['json', {
      outputFile: process.env.PKUBA_E2E_RESULT_PATH
        ?? '../../output/playwright/admin-web/results.json',
    }],
  ],
  use: {
    baseURL: process.env.PKUBA_E2E_BASE_URL ?? 'http://127.0.0.1:8088',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1600, height: 1000 },
      },
    },
  ],
});
