const { test, expect } = require('@playwright/test');
const { PNG } = require('pngjs');
const path = require('node:path');

const job = process.env.DESKTOP_JOB;

test('original creation, brand selection, optional year, and photo workflow', async ({ page }, info) => {
  const submitted = [];
  await page.route('**/api/v1/designs', async route => {
    submitted.push(route.request().postDataJSON());
    await route.fulfill({ status: 422, body: 'Browser test: generation disabled' });
  });
  let photoBody;
  await page.route('**/api/v1/designs/from-image', async route => {
    photoBody = route.request().postData();
    await route.fulfill({ status: 422, body: 'Browser test: generation disabled' });
  });
  await page.goto('/?scale=1:24');
  await expect(page.getByRole('heading', { name: 'Car2LEGO', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '选择品牌', exact: true }).click();
  await page.getByRole('textbox', { name: '搜索品牌', exact: true }).fill('宝马');
  await page.getByRole('button', { name: '宝马 BMW', exact: true }).click();
  await expect(page.getByRole('button', { name: '选择品牌：宝马' })).toBeVisible();
  await expect(page.getByRole('spinbutton', { name: '年款（选填）' })).toHaveValue('');
  await page.getByRole('button', { name: '选择品牌：宝马' }).click();
  await page.getByRole('button', { name: '其他，自定义品牌' }).click();
  await page.getByRole('textbox', { name: '自定义品牌名称' }).fill('测试品牌');
  await page.getByRole('textbox', { name: '车型', exact: true }).fill('Test');
  await expect(page.getByRole('combobox', { name: '模型比例' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '比例设计要求' })).toHaveCount(0);
  await page.getByRole('button', { name: '创建设计', exact: true }).click();
  await expect(page.getByRole('alert').filter({hasText: 'Browser test: generation disabled'})).toBeVisible();
  expect(submitted).toEqual([{ make: '测试品牌', model: 'Test' }]);
  await page.screenshot({ path: info.outputPath('creation.png') });
  await page.getByRole('button', { name: '车辆照片', exact: true }).click();
  await page.getByLabel('上传车辆照片').setInputFiles(path.join(__dirname, '../public/images/ferrari-f40.webp'));
  await expect(page.getByAltText('已选择的车辆照片')).toBeVisible();
  await page.getByRole('button', { name: '创建设计', exact: true }).click();
  await expect(page.getByRole('alert').filter({hasText: 'Browser test: generation disabled'})).toBeVisible();
  expect(photoBody).toContain('name="image"');
  expect(photoBody).not.toMatch(/name="(?:scale|scale_mode|length_studs|width_studs)"/);
  await page.getByRole('button', { name: '移除照片' }).click();
  await expect(page.getByRole('button', { name: '选择车辆照片' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('vehicle catalog filters downloadable models by brand and body', async ({ page }) => {
  await page.goto('/sets');
  await page.getByRole('button', { name: '筛选品牌：全部品牌' }).click();
  await page.getByRole('textbox', { name: '搜索品牌', exact: true }).fill('宝马');
  await page.getByRole('button', { name: '宝马 BMW', exact: true }).click();
  await page.getByRole('combobox', { name: '筛选车辆类型' }).selectOption('race_car');
  await expect(page.getByRole('heading', { name: '宝马 M4 GT3 EVO 赛车' })).toBeVisible();
  await expect(page.locator('.catalog-item')).toHaveCount(1);
  await expect(page.getByRole('link', { name: '下载 .io', exact: true })).toHaveAttribute('href', /\/sets\/library\/[^/]+\/download$/);
});

test('customization and parameter dialogue omit scale even for a historical design', async ({ page }, info) => {
  const id = '00000000000000000000000000000001';
  const car = { id, make: 'Lamborghini', model: 'Countach', year: null, created_at: '' };
  const specs = { version: 'test', vehicle: car, scale: null, overall_grounded: false,
    measurements: {}, missing: [], conflicts: [], sources: [] };
  await page.route(`**/api/v1/designs/${id}`, route => route.fulfill({ json: {
    id, status: 'draft', requested_scale: '1:24', car, parts_count: 188,
    difficulty: 'Expert', parts: [], file_urls: {}, created_at: '', physical_specifications: specs,
  } }));
  await page.route('**/api/v1/mods', route => route.fulfill({ json: {} }));
  let customization, parameters;
  await page.route(`**/api/v1/designs/${id}/customize`, async route => {
    customization = route.request().postDataJSON();
    await route.fulfill({ status: 422, body: 'Browser test: generation disabled' });
  });
  await page.route('**/api/v1/research/physical-specifications', async route => {
    parameters = route.request().postDataJSON();
    await route.fulfill({ json: specs });
  });
  await page.goto(`/designs/${id}`);
  await expect(page.getByRole('heading', { name: 'Lamborghini Countach', exact: true })).toBeVisible();
  await expect(page.getByText('模型比例', { exact: true })).toHaveCount(0);
  await expect(page.getByText('1:24', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '通过模型获取车辆参数', exact: true }).click();
  await expect(page.getByText('新获取参数', { exact: true })).toBeVisible();
  expect(parameters).toEqual({ make: 'Lamborghini', model: 'Countach', year: null });
  await page.getByRole('textbox', { name: '定制内容' }).fill('车身改为黑色');
  await page.getByRole('button', { name: '创建设计副本', exact: true }).click();
  await expect(page.getByText(/Browser test: generation disabled/)).toBeVisible();
  expect(customization).toEqual({ customization_text: '车身改为黑色' });
  await page.screenshot({ path: info.outputPath('customization.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('reference batches and module detail views remain visible while generating', async ({ page }, info) => {
  const id = '00000000000000000000000000000002';
  const names = ['overview', 'front', 'rear', 'left', 'right', 'top', 'front_left', 'front_right',
    'rear_left', 'rear_right', 'windscreen', 'door_left_closed', 'door_right_closed',
    'door_left_open', 'door_right_open', 'headlamps', 'taillamps', 'front_cabin_join',
    'rear_cabin_join', 'dashboard', 'seats_pedals', 'front_running_gear', 'rear_running_gear',
    'front_cover_open', 'rear_cover_open', 'powertrain_cargo', 'scissor_hinge', 'side_intake', 'popup_lamps'];
  await page.route(`**/api/v1/designs/${id}`, route => route.fulfill({ json: {
    id, status: 'processing', parts: [], file_urls: {},
    car: { id, make: 'Lamborghini', model: 'Countach', year: null },
    reference_images: names.map((view, index) => ({ view, category: index < 10 ? 'exterior' : 'detail',
      url: '/api/v1/test-reference.png', synthetic: true, model: 'gpt-image-test' })),
    reference_dispatch: { status: 'running', batch_index: 4, batch_size: 5,
      views: names.slice(10, 15), completed_views: names.slice(0, 10), expected_views: 29, failed_views: [] },
  } }));
  await page.route(`**/api/v1/designs/${id}/status`, route => route.fulfill({ json: {
    id, status: 'processing', stage: 'reference_images', progress: 20,
  } }));
  await page.route('**/api/v1/mods', route => route.fulfill({ json: {} }));
  await page.route('**/api/v1/test-reference.png', route => route.fulfill({
    path: path.join(__dirname, '../public/images/ferrari-f40.webp'), contentType: 'image/webp',
  }));
  await page.goto(`/designs/${id}`);
  await expect(page.getByText('参考图第 4 组 · 本组 5 张 · 已完成 10 / 29 张')).toBeVisible();
  await page.getByRole('tab', { name: '内饰与机构', exact: true }).click();
  await expect(page.getByRole('tab', { name: '车头与座舱连接', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '右门开启', exact: true }).click();
  const image = page.locator('img.reference-main');
  await expect(image).toHaveAttribute('alt', 'Lamborghini Countach 右门开启参考图');
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(element => element.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByRole('tab', { name: '左前斜视', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: info.outputPath('reference-batch.png'), fullPage: true });
});

test('real draft geometry, canvas pixels, controls, and exports', async ({ page, request }, info) => {
  test.skip(!job, 'Set DESKTOP_JOB to a real imported or generated draft');
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`/?job=${job}`);
  await expect(page).toHaveURL(new RegExp(`/designs/${job}$`));
  await expect(page.getByRole('button', { name: '恢复视角' })).toBeVisible({ timeout: 60000 });
  const canvas = page.locator('.assembly-canvas canvas');
  await canvas.scrollIntoViewIfNeeded();
  const first = await canvas.screenshot({ path: info.outputPath('assembly.png') });
  const pixels = PNG.sync.read(first);
  const colors = new Set();
  for (let i = 0; i < pixels.data.length; i += 16) colors.add(pixels.data.readUInt32BE(i));
  expect(colors.size).toBeGreaterThan(100);
  await page.getByRole('button', { name: '线框', exact: true }).click();
  await expect(page.getByRole('button', { name: '线框', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const wire = await canvas.screenshot({ path: info.outputPath('wireframe.png') });
  expect(first.equals(wire)).toBeFalsy();
  await page.getByRole('button', { name: '线框', exact: true }).click();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 150, box.y + box.height / 2 + 60, { steps: 15 });
  await page.mouse.up();
  const rotated = await canvas.screenshot({ path: info.outputPath('rotated.png') });
  expect(rotated.equals(first)).toBeFalsy();
  await page.getByRole('button', { name: '恢复视角' }).click();
  await page.getByRole('slider', { name: '搭建步骤' }).focus();
  await page.keyboard.press('Home');
  await expect(page.getByRole('slider', { name: '搭建步骤' })).toHaveValue('1');
  const partial = await canvas.screenshot({ path: info.outputPath('first-step.png') });
  expect(partial.equals(first)).toBeFalsy();
  await page.keyboard.press('End');
  const response = await request.get(`/api/v1/designs/${job}`);
  const detail = await response.json();
  expect(detail.status).toBe('draft');
  expect(detail.quality_report.precision_verified).toBe(false);
  if (detail.physical_specifications?.research?.mode === 'model_dialogue') {
    const specifications = page.getByRole('region', { name: '车辆尺寸与结构参数' });
    await expect(specifications).toContainText('模型估计，未经厂商或实测核验');
    await expect(specifications).toContainText('模型对话');
    await expect(specifications).toContainText('估算换算');
    expect(detail.physical_specifications.overall_grounded).toBe(false);
    expect(detail.physical_specifications.sources).toEqual([]);
    expect(detail.physical_specifications.research.queries).toEqual([]);
  }
  for (const [kind, url] of Object.entries(detail.file_urls)) {
    const exported = await request.get(url);
    expect(exported.ok(), kind).toBeTruthy();
    expect((await exported.body()).length, kind).toBeGreaterThan(30);
  }
  await page.getByRole('button', { name: '放大参考图' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭预览' }).click();
  await page.getByRole('textbox', { name: '定制内容' }).fill('车身改为黑色');
  await expect(page.getByRole('button', { name: '创建设计副本' })).toBeEnabled();
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
