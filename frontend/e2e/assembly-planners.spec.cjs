const { test, expect } = require('@playwright/test');

test('creation submits Codex and fine-tuned planners as independent alternatives', async ({ page }) => {
  const submissions = [];
  await page.route('**/api/v1/designs/assembly-planners', route => route.fulfill({ json: {
    default: 'legacy', comparison_status: 'requires_actual_evaluation', winner: null,
    alternatives: ['codex', 'local_base', 'local_finetuned'].map(id => ({ id, configured: true })),
  } }));
  await page.route('**/api/v1/designs', async route => {
    submissions.push(route.request().postDataJSON());
    await route.fulfill({ status: 422, body: 'Planner contract test: generation disabled' });
  });
  await page.goto('/?make=BMW&model=M4');
  const selector = page.getByRole('combobox', { name: '装配方案' });
  await expect(selector).toBeVisible();
  await expect(selector.locator('option[value=auto]')).toHaveJSProperty('disabled', true);
  await expect(selector.locator('option[value=local_base]')).toHaveCount(0);
  for (const planner of ['codex', 'local_finetuned']) {
    await selector.selectOption(planner);
    await page.getByRole('button', { name: '创建设计', exact: true }).click();
    await expect(page.locator('.error-message[role=alert]')).toContainText('Planner contract test');
    await expect(selector).toBeEnabled();
    expect(submissions.at(-1)).toEqual({ make: 'BMW', model: 'M4', assembly_planner: planner, reconstruction_backend: 'gpt-blender' });
  }
  expect(submissions.map(item => item.assembly_planner)).toEqual(['codex', 'local_finetuned']);
});

test('hybrid and envelope routes follow the selected assembly contract', async ({ page }) => {
  let submitted;
  await page.route('**/api/v1/designs/assembly-planners', route => route.fulfill({ json: {
    default: 'legacy', comparison_status: 'requires_actual_evaluation',
    alternatives: [{ id: 'codex', configured: true }],
  } }));
  await page.route('**/api/v1/designs', async route => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ status: 422, body: 'Routing fixture: generation disabled' });
  });
  await page.goto('/?make=BMW&model=M4');
  const assembly = page.getByRole('combobox', { name: '装配方案' });
  const reconstruction = page.getByRole('combobox', { name: '建模方案' });
  await expect(assembly).toBeVisible();
  await expect(reconstruction.locator('option[value=hybrid]')).toHaveJSProperty('disabled', true);
  await reconstruction.selectOption('hunyuan-mv');
  await assembly.selectOption('codex');
  await expect(reconstruction).toHaveValue('gpt-blender');
  await expect(reconstruction.locator('option[value=hunyuan-mv]')).toHaveJSProperty('disabled', true);
  await reconstruction.selectOption('hybrid');
  await page.getByRole('button', { name: '创建设计', exact: true }).click();
  await expect(page.locator('.error-message[role=alert]')).toContainText('Routing fixture');
  expect(submitted.reconstruction_backend).toBe('hybrid');
  expect(submitted.assembly_planner).toBe('codex');
});

test('an unconfigured local model remains unavailable independently of Codex', async ({ page }) => {
  await page.route('**/api/v1/designs/assembly-planners', route => route.fulfill({ json: {
    default: 'legacy', comparison_status: 'requires_actual_evaluation', winner: null,
    alternatives: [{ id: 'codex', configured: true }, { id: 'local_finetuned', configured: false }],
  } }));
  await page.goto('/');
  const selector = page.getByRole('combobox', { name: '装配方案' });
  await expect(selector.locator('option[value=codex]')).toBeEnabled();
  await expect(selector.locator('option[value=local_finetuned]')).toHaveJSProperty('disabled', true);
  await expect(selector.locator('option[value=auto]')).toHaveJSProperty('disabled', true);
});

test('unified controls fit desktop and mobile', async ({ page }, testInfo) => {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.goto('/?make=BMW&model=M4');
    const reconstruction = page.getByRole('combobox', { name: '建模方案' });
    await expect(reconstruction).toBeVisible();
    await reconstruction.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(await reconstruction.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return rect.width > 200 && rect.left >= 0 && rect.right <= window.innerWidth;
    })).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`unified-${viewport.width}.png`), fullPage: true });
  }
});
