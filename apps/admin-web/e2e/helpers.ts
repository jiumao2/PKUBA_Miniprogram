import { expect, type Page } from '@playwright/test';

export const hasAdminCredentials = Boolean(
  process.env.PKUBA_E2E_USERNAME && process.env.PKUBA_E2E_PASSWORD,
);

export const demoGamePattern = new RegExp(
  process.env.PKUBA_E2E_GAME_PATTERN ?? '示例学院甲.*示例学院乙',
);

export async function loginAndOpenEditor(page: Page) {
  await page.goto('/');
  const loginTitle = page.getByRole('heading', { name: '登录工作台' });
  const signedIn = page.getByRole('button', { name: '退出登录' });
  await expect(loginTitle.or(signedIn).first()).toBeVisible({ timeout: 10_000 });
  if (await loginTitle.isVisible()) {
    await page.getByRole('tab', { name: '密码登录' }).click();
    await page.getByLabel('用户名').fill(process.env.PKUBA_E2E_USERNAME ?? '');
    await page.getByLabel('密码').fill(process.env.PKUBA_E2E_PASSWORD ?? '');
    const loginResponse = page.waitForResponse((response) =>
      response.url().endsWith('/api/v1/auth/admin/password-login')
      && response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: '登录', exact: true }).click();
    expect((await loginResponse).ok()).toBe(true);
    await expect(signedIn).toBeVisible({ timeout: 10_000 });
  }
  await page.goto('/scoresheet.html');
  await expect(page.getByText('ScoresheetReader', { exact: true })).toBeVisible({ timeout: 10_000 });
}

export async function openDemoSheet(page: Page) {
  await loginAndOpenEditor(page);
  await page.getByRole('banner').getByRole('button', { name: '选择比赛' }).click();
  const dialog = page.getByRole('dialog', { name: '选择比赛' });
  const game = dialog.getByRole('button', { name: demoGamePattern });
  await expect(game).toBeVisible();
  await expect(game.locator('.game-ready')).not.toContainText('待上传');
  await game.click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('svg.scene-overlay')).toBeVisible();
  const validateButton = page.getByRole('button', { name: /^校验/ });
  if (await validateButton.isDisabled()) {
    const documentId = await page.evaluate(() =>
      localStorage.getItem('scoresheet-reader:last-document-id'));
    if (!documentId) throw new Error('只读演示记录表缺少逐记录纠错标识。');
    await page.goto(`/scoresheet.html?archived_view=1&archived_correction=${encodeURIComponent(documentId)}`);
    await expect(page.locator('svg.scene-overlay')).toBeVisible();
    await expect(validateButton).toBeEnabled({ timeout: 10_000 });
  }
  const recognition = page.getByLabel('大模型识别结果');
  await expect(recognition).toContainText('识别结果已载入', { timeout: 10_000 });
  return recognition;
}

export async function waitForSaved(page: Page) {
  await expect(page.locator('.save-indicator')).toHaveText('已保存', { timeout: 5_000 });
}

export async function releaseCurrentLease(page: Page) {
  if (page.isClosed()) return;
  await page.evaluate(async () => {
    const scoresheetId = localStorage.getItem('scoresheet-reader:last-document-id');
    const clientId = sessionStorage.getItem('pkuba:scoresheet-reader:web-client-id');
    if (!scoresheetId || !clientId) return;
    const tokenKey = `pkuba:scoresheet-reader:lease-token:${scoresheetId}`;
    const leaseToken = sessionStorage.getItem(tokenKey);
    if (!leaseToken) return;
    const csrf = document.cookie.match(/(?:^|; )pkuba_csrftoken=([^;]+)/)?.[1] ?? '';
    const response = await fetch(`/api/v1/scoresheets/${scoresheetId}/lease/release`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': decodeURIComponent(csrf),
      },
      body: JSON.stringify({
        lease_token: leaseToken,
        client_id: clientId,
        surface: 'WEB',
      }),
    });
    if (!response.ok) throw new Error(`租约清理失败：${response.status}`);
    sessionStorage.removeItem(tokenKey);
  }).catch(() => undefined);
}
