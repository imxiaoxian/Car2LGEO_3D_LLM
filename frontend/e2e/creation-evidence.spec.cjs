const { test, expect } = require('@playwright/test');

test('all vehicle module tasks fit the desktop detail page', async ({ page }, info) => {
  const id = '00000000000000000000000000000003';
  const modules = ['chassis', 'steering', 'wheel_front_left', 'wheel_front_right',
    'wheel_rear_left', 'wheel_rear_right', 'cabin', 'door_left', 'door_right',
    'front', 'rear', 'front_lights', 'rear_lights', 'hood', 'rear_cover',
    'signature', 'headlamp_mechanism'];
  const detail = {
    id, status: 'processing', car: { id, make: 'Lamborghini', model: 'Countach', year: null },
    parts: [], file_urls: {},
    module_progress: { status: 'incomplete', complete: 1, required: modules.length, completion_ready: false, modules: [] },
    module_tasks: {
      planner: 'codex', active: 'steering', complete: 1, required: modules.length,
      tasks: modules.map((module, index) => ({
        id: module, status: index === 0 ? 'assembled' : index === 1 ? 'running' : 'pending',
        attempts: index < 2 ? 1 : 0, candidates: { passing: index === 0 ? 3 : 0, required: 3 },
        dependencies: index === 0 ? [] : ['chassis'],
        reason: index > 1 ? 'dependency_not_assembled' : null,
      })),
    },
  };
  await page.route(`**/api/v1/designs/${id}`, route => route.fulfill({ json: detail }));
  await page.route(`**/api/v1/designs/${id}/status`, route => route.fulfill({ json: {
    id, status: detail.status, stage: 'assembly_agent', progress: 45,
  } }));
  await page.route('**/api/v1/mods', route => route.fulfill({ json: {} }));
  await page.goto(`/designs/${id}`);
  const rows = page.locator('.module-progress-list > div');
  await expect(rows).toHaveCount(modules.length);
  const bounds = await rows.evaluateAll(elements => elements.map(element => {
    const { left, right, top, bottom } = element.getBoundingClientRect();
    return { left, right, top, bottom };
  }));
  const width = page.viewportSize().width;
  for (let index = 0; index < bounds.length; index++) {
    expect(bounds[index].left).toBeGreaterThanOrEqual(0);
    expect(bounds[index].right).toBeLessThanOrEqual(width);
    if (index > 0) expect(bounds[index].top).toBeGreaterThanOrEqual(bounds[index - 1].bottom);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  await page.screenshot({ path: info.outputPath('module-tasks.png'), fullPage: true });
});
