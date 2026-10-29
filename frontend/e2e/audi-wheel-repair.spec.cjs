const { test, expect } = require('@playwright/test');
const { PNG } = require('pngjs');
const { createHash } = require('node:crypto');

test('repaired Audi download and actual geometry remain available and interactive', async ({page, request}, info) => {
  const id = '06fd9c4b71ffc41cb60545b0';
  const base = `/api/v1/sets/library/${id}`;
  const model = await (await request.get(base)).json();
  expect(model.repair.kind).toBe('studio_native_wheel_origin_migration');
  expect(model.repair.source_sha256).not.toBe(model.sha256);
  const download = await request.get(`${base}/download`);
  expect(download.ok()).toBe(true);
  expect(createHash('sha256').update(await download.body()).digest('hex')).toBe(model.sha256);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`/sets/${id}`);
  const canvas = page.locator('.library-canvas canvas');
  await expect(page.getByRole('button', {name:'恢复视角', exact:true})).toBeVisible({timeout:120000});
  const status = await (await request.get(`${base}/preview`)).json();
  expect(status.status).toBe('ready');
  expect(status.source_instance_count).toBe(914);
  expect(status.missing_geometry).toEqual([]);
  const before = await canvas.screenshot({path:info.outputPath('audi-repaired.png')});
  const png = PNG.sync.read(before);
  let foreground = 0, red = 0;
  for (let i=0; i<png.data.length; i+=4) {
    const [r,g,b] = png.data.subarray(i,i+3);
    if (Math.abs(r-245)+Math.abs(g-245)+Math.abs(b-247)>42) foreground++;
    if (r>100 && r>g*1.5 && r>b*1.5) red++;
  }
  expect(foreground/(png.width*png.height)).toBeGreaterThan(.02);
  expect(foreground/(png.width*png.height)).toBeLessThan(.8);
  expect(red).toBeGreaterThan(500);
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);
  await page.mouse.down();
  await page.mouse.move(box.x+box.width*.7,box.y+box.height*.6,{steps:12});
  await page.mouse.up();
  await expect.poll(async()=>createHash('sha256').update(await canvas.screenshot()).digest('hex'))
    .not.toBe(createHash('sha256').update(before).digest('hex'));
  await canvas.screenshot({path:info.outputPath('audi-repaired-orbit.png')});
  expect(errors).toEqual([]);
});
