import { expect, test } from '@playwright/test';
import {
  hasAdminCredentials,
  loginAndOpenEditor,
  openDemoSheet,
  releaseCurrentLease,
  waitForSaved,
} from './helpers';

test.skip(
  !hasAdminCredentials || process.env.RUN_PRIVATE_LIVE_UI !== '1',
  '跨标签真实页面核对需登录凭据并显式设置 RUN_PRIVATE_LIVE_UI=1。',
);

test('the second tab opens the same private sheet read-only and receives edits within two seconds', async ({
  page,
  context,
}) => {
  const second = await context.newPage();
  try {
    await openDemoSheet(page);
    await loginAndOpenEditor(second);
    await expect(second.locator('svg.scene-overlay')).toBeVisible();
    await expect(second.locator('.offline-badge')).toContainText('正在通过网页编辑', { timeout: 5_000 });

    await page.locator('rect[data-field-id="header.game_number"]').dblclick();
    const firstInput = page.getByLabel('比赛序号');
    const target = `跨端-${Date.now()}`;
    await firstInput.fill(target);
    await waitForSaved(page);

    await second.locator('rect[data-field-id="header.game_number"]').dblclick();
    const secondInput = second.getByLabel('比赛序号');
    await expect(secondInput).toBeDisabled();
    await expect(secondInput).toHaveValue(target, { timeout: 3_500 });

    const firstClient = await page.evaluate(() =>
      sessionStorage.getItem('pkuba:scoresheet-reader:web-client-id'));
    const secondClient = await second.evaluate(() =>
      sessionStorage.getItem('pkuba:scoresheet-reader:web-client-id'));
    expect(firstClient).toBeTruthy();
    expect(secondClient).toBeTruthy();
    expect(secondClient).not.toBe(firstClient);
  } finally {
    await releaseCurrentLease(page);
    await releaseCurrentLease(second);
    await second.close();
  }
});
