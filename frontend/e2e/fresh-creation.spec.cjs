const { test, expect } = require('@playwright/test');

test('fresh generation is explicit while archive matching remains the default', async ({ page }) => {
  const submissions = [];
  await page.route('**/api/v1/designs', async route => {
    submissions.push(route.request().postDataJSON());
    await route.fulfill({ status: 422, body: 'Fresh-generation routing fixture' });
  });
  await page.goto('/?make=Lamborghini&model=Countach');
  const preference = page.getByLabel('优先使用车型档案', { exact: true });
  await expect(preference).toBeChecked();
  await preference.uncheck();
  await page.getByRole('button', { name: '创建设计', exact: true }).click();
  await expect(page.locator('.error-message[role=alert]')).toContainText('Fresh-generation routing fixture');
  expect(submissions).toEqual([{ make: 'Lamborghini', model: 'Countach', reuse_library: false }]);
});
