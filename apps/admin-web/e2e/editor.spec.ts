import { expect, test, type Page } from '@playwright/test';
import {
  demoGamePattern,
  hasAdminCredentials,
  loginAndOpenEditor,
  openDemoSheet,
  releaseCurrentLease,
  waitForSaved,
} from './helpers';

test.skip(
  !hasAdminCredentials,
  '需要 PKUBA_E2E_USERNAME 与 PKUBA_E2E_PASSWORD 登录真实管理站。',
);

async function currentSourceFile(page: Page) {
  const sourceUrl = await page.getByRole('img', { name: '上传的篮球记录表' }).getAttribute('src');
  if (!sourceUrl) throw new Error('当前记录表没有可重新上传的原图。');
  const response = await page.request.get(new URL(sourceUrl, page.url()).href);
  expect(response.ok()).toBe(true);
  return response.body();
}

test.describe.serial('PKUBA formal scoresheet workflow', () => {
  test.afterEach(async ({ page }) => {
    await releaseCurrentLease(page);
  });

  test('first launch is a blank template with no synthetic product entry', async ({ page }) => {
    await loginAndOpenEditor(page);
    await page.evaluate(() => localStorage.removeItem('scoresheet-reader:last-document-id'));
    await page.reload();

    await expect(page.getByText('ScoresheetReader', { exact: true })).toBeVisible();
    await expect(page.getByTestId('scoresheet-logo').first()).toBeVisible();
    await expect(page.getByText('尚未选择比赛').first()).toBeVisible();
    await expect(page.getByText('空白标准记录表')).toBeVisible();
    await expect(page.getByText('合成样表')).toHaveCount(0);
    await expect(page.getByText(/^v\d+/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: /保存草稿/ })).toBeDisabled();
    await expect(page.getByRole('button', { name: /^校验/ })).toBeDisabled();
    await expect(page.getByRole('button', { name: /提交记录表/ })).toBeDisabled();

    const sourceCanvas = page.getByLabel('照片画布：拖动平移，滚轮缩放');
    const documentCanvas = page.getByLabel('标准记录表画布：拖动平移，滚轮缩放');
    const [sourceBounds, documentBounds] = await Promise.all([
      sourceCanvas.boundingBox(), documentCanvas.boundingBox(),
    ]);
    expect(sourceBounds).not.toBeNull();
    expect(documentBounds).not.toBeNull();
    expect(Math.abs(sourceBounds!.y - documentBounds!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(sourceBounds!.height - documentBounds!.height)).toBeLessThanOrEqual(1);

    await page.getByRole('button', { name: '导入记录表照片' }).click();
    await expect(page.getByRole('dialog', { name: '选择比赛' })).toBeVisible();
    await page.getByRole('button', { name: '关闭比赛列表' }).click();
  });

  test('opens, edits, logs, undoes, redoes, autosaves and restores', async ({ page }) => {
    const recognition = await openDemoSheet(page);
    await expect(recognition).toContainText('总计 0 tokens');
    await expect(page.locator('.document-state')).toContainText('示例学院甲 vs 示例学院乙');
    await expect(page.locator('.document-state')).not.toContainText(/v\d+/);

    await page.locator('rect[data-field-id="header.game_number"]').dblclick();
    const gameNumber = page.getByLabel('比赛序号');
    const before = await gameNumber.inputValue();
    const target = before === 'E2E-42' ? 'E2E-43' : 'E2E-42';
    await gameNumber.fill(target);
    await waitForSaved(page);

    await page.getByRole('button', { name: '撤销' }).click();
    await expect(gameNumber).toHaveValue(before);
    await waitForSaved(page);
    await page.getByRole('button', { name: '重做' }).click();
    await expect(gameNumber).toHaveValue(target);
    await waitForSaved(page);

    await expect(page.getByText('人工修改记录')).toBeVisible();
    const latestChange = page.locator('.change-log-list details').first();
    await expect(latestChange).toContainText('重做修改');
    await latestChange.locator('summary').click();
    await expect(latestChange).toContainText('比赛信息 · 比赛序号');
    await expect(latestChange).toContainText(before || '（空）');
    await expect(latestChange).toContainText(target);

    await page.reload();
    await page.locator('rect[data-field-id="header.game_number"]').dblclick();
    await expect(page.getByLabel('比赛序号')).toHaveValue(target);
  });

  test('photo and document canvases support direct pan, zoom, reset and reload', async ({ page }) => {
    await openDemoSheet(page);
    await expect(page.getByRole('button', { name: '撤回照片视图' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '照片视图向前一步' })).toHaveCount(0);

    const sourceCanvas = page.getByLabel('照片画布：拖动平移，滚轮缩放');
    const sourceZoom = page.getByRole('button', { name: '原图倍率复位' });
    await page.getByRole('button', { name: '放大原图' }).click();
    await expect(sourceZoom).toHaveText('110%');
    await sourceZoom.click();
    await expect(sourceZoom).toHaveText('100%');
    await sourceCanvas.hover();
    await page.mouse.wheel(0, -100);
    await expect(sourceZoom).toHaveText('110%');

    const sourceImage = page.getByRole('img', { name: '上传的篮球记录表' });
    const beforeReload = await sourceImage.getAttribute('src');
    await page.getByRole('button', { name: '重新载入原图' }).click();
    await expect.poll(() => sourceImage.getAttribute('src')).not.toBe(beforeReload);
    for (let index = 0; index < 6; index += 1) await page.mouse.wheel(0, -100);
    const sourceBeforePan = await sourceCanvas.evaluate((element) => element.scrollLeft);
    const sourceBox = await sourceCanvas.boundingBox();
    await page.mouse.move(sourceBox!.x + sourceBox!.width / 2, sourceBox!.y + sourceBox!.height / 2);
    await page.mouse.down();
    await page.mouse.move(sourceBox!.x + sourceBox!.width / 2 - 70, sourceBox!.y + sourceBox!.height / 2);
    await page.mouse.up();
    await expect.poll(() => sourceCanvas.evaluate((element) => element.scrollLeft))
      .toBeGreaterThan(sourceBeforePan);

    const overlay = page.locator('svg.scene-overlay');
    const stage = page.locator('.page-stage');
    const initialWidth = await stage.evaluate((element) => element.getBoundingClientRect().width);
    const documentCanvas = page.getByLabel('标准记录表画布：拖动平移，滚轮缩放');
    await documentCanvas.hover();
    await page.mouse.wheel(0, -100);
    await expect.poll(() => stage.evaluate((element) => element.getBoundingClientRect().width))
      .toBeGreaterThan(initialWidth);
    await expect(overlay).toHaveAttribute('viewBox', '0 0 595.32 842.04');

    const [sourceBounds, documentBounds] = await Promise.all([
      sourceCanvas.boundingBox(), documentCanvas.boundingBox(),
    ]);
    expect(Math.abs(sourceBounds!.y - documentBounds!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(sourceBounds!.height - documentBounds!.height)).toBeLessThanOrEqual(1);

    await page.getByRole('button', { name: '切换原图叠加' }).click();
    await expect(page.getByLabel('原图透明度')).toBeVisible();
    await page.getByLabel('原图透明度').fill('0.35');
    await expect(overlay).toHaveAttribute('viewBox', '0 0 595.32 842.04');
  });

  test('identical reupload resets the draft and automatically starts a new recognition cycle', async ({ page }) => {
    test.setTimeout(1_200_000);
    test.skip(
      process.env.RUN_SCORESHEET_RECOGNITION_E2E !== '1',
      '真实照片重新识别需配置 Qwen Key，并显式设置 RUN_SCORESHEET_RECOGNITION_E2E=1。',
    );
    await openDemoSheet(page);
    const source = await currentSourceFile(page);
    await page.getByRole('banner').getByRole('button', { name: '选择比赛' }).click();
    const dialog = page.getByRole('dialog', { name: '选择比赛' });
    const demoRow = dialog.locator('.game-row-shell').filter({ hasText: demoGamePattern });
    page.once('dialog', (confirmation) => confirmation.accept());
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      demoRow.getByRole('button', { name: '重新上传' }).click(),
    ]);
    await chooser.setFiles({ name: 'ScoresheetReader-demo.jpg', mimeType: 'image/jpeg', buffer: source });
    await expect(dialog).toHaveCount(0);
    await expect(page.getByLabel('大模型识别结果')).toContainText('识别结果已载入', {
      timeout: 1_200_000,
    });
    await page.locator('rect[data-field-id="header.game_number"]').dblclick();
    await expect(page.getByLabel('比赛序号')).toHaveValue('SCORESHEET-DEMO-001');
    await expect(page.getByText('重新上传记录表并重置草稿').first()).toBeVisible();
  });

  test('running-score editing and deterministic validation remain available', async ({ page }) => {
    await openDemoSheet(page);
    await page.locator('rect[data-field-id="score.A.004"]').click();
    const ledger = page.getByLabel('A 队得分事件账本');
    await expect(ledger).toBeVisible();
    await expect(page.getByRole('tab', { name: /Q1/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByLabel('本次得分', { exact: true })).toHaveCount(0);
    await page.getByLabel('得分队员').selectOption('8');
    await expect(ledger.locator('[data-score-field="score.A.004"]')).toBeVisible();

    await page.locator('rect[data-field-id="score.A.003"]').dblclick();
    await page.getByRole('button', { name: '删除本格号码' }).click();
    await page.locator('rect[data-field-id="score.A.004"]').dblclick();
    await page.getByRole('button', { name: '删除本格号码' }).click();
    await waitForSaved(page);
    await page.getByRole('button', { name: /^校验/ }).click();
    await expect(page.getByRole('button', { name: /SCORE_SEQUENCE_GAP/ }).first()).toBeVisible();
    await page.getByRole('button', { name: '撤销' }).click();
    await page.getByRole('button', { name: '撤销' }).click();
    await waitForSaved(page);

    await page.locator('[data-field-id="summary"]').click();
    await expect(page.getByLabel('A 队最终比分')).toHaveAttribute('readonly', '');
    await expect(page.getByLabel('胜队')).toHaveAttribute('readonly', '');
  });

  test('a real uploaded sheet validates, confirms and exports the current draft PDF', async ({ page }) => {
    await openDemoSheet(page);
    const validationResponse = page.waitForResponse((response) =>
      response.url().endsWith('/validate')
      && response.request().method() === 'POST'
      && response.ok(),
    );
    await page.getByRole('button', { name: /^校验/ }).click();
    await validationResponse;
    await expect(page.locator('.issue-row.error')).toHaveCount(0);

    const confirmResponse = page.waitForResponse((response) =>
      response.url().endsWith('/publish')
      && response.request().method() === 'POST'
      && response.ok(),
    );
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: /提交记录表/ }).click();
    await confirmResponse;
    await expect(page.locator('.document-state')).toContainText('已提交');

    const exportLink = page.getByRole('link', { name: /导出 PDF/ });
    const href = await exportLink.getAttribute('href');
    const response = await page.request.get(new URL(href!, page.url()).href);
    expect(response.ok()).toBe(true);
    expect((await response.body()).subarray(0, 4).toString()).toBe('%PDF');
  });
});
