const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { PNG } = require('pngjs');

const manifestPath = process.env.VEHICLE_E2E_CASES;

test('live vehicle creation reaches native preview and exact artifact downloads', async ({ page, request }, info) => {
  test.skip(!manifestPath, 'Set VEHICLE_E2E_CASES to an explicit live case manifest; real model calls may be made');
  test.setTimeout(12 * 60 * 60 * 1000);
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const evidence = { schema: 'vehicle-website-evidence.v1', scope: 'website_transport_and_preview_only', quality_accepted: false, cases: [] };
  const save = () => fs.writeFileSync(info.outputPath('live-website-evidence.json'), JSON.stringify(evidence, null, 2));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const item of manifest.cases) {
    const row = { case: item.case, job_id: item.job_id, status: 'waiting', downloads: [] };
    evidence.cases.push(row); save();
    try {
    if (!item.job_id) {
      await page.goto(`/?make=${encodeURIComponent(item.make)}&model=${encodeURIComponent(item.model)}`);
      await page.getByLabel('优先使用车型档案', { exact: true }).uncheck();
      await page.getByRole('combobox', { name: '装配方案' }).selectOption(item.planner || 'codex');
      const response = page.waitForResponse(r => r.url().endsWith('/api/v1/designs') && r.request().method() === 'POST');
      await page.getByRole('button', { name: '创建设计', exact: true }).click();
      const submitted = await response;
      expect(submitted.ok()).toBeTruthy();
      const job = await submitted.json();
      expect(job.library_match).toBeFalsy();
      expect(job.id).toMatch(/^[a-f0-9]{32}$/);
      item.job_id = job.id;
      // Keep paid job identities before waiting, so reruns resume rather than resubmit.
      fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
    }
    row.job_id = item.job_id;
    await page.goto(`/designs/${item.job_id}`);
    if (item.resume_failed === true) {
      const previous = await request.get(`/api/v1/designs/${item.job_id}`);
      expect(previous.ok()).toBeTruthy();
      if ((await previous.json()).status === 'failed') {
        const resumed = page.waitForResponse(r => r.url().endsWith(`/designs/${item.job_id}/control/resume`)
          && r.request().method() === 'POST');
        await page.getByRole('button', { name: '恢复任务', exact: true }).click();
        expect((await resumed).ok()).toBeTruthy();
        row.resumed_existing_job = true; save();
      }
    }
    const deadline = Date.now() + (manifest.max_minutes_per_case || 90) * 60000;
    let detail;
    while (Date.now() < deadline) {
      const response = await request.get(`/api/v1/designs/${item.job_id}`);
      expect(response.ok()).toBeTruthy();
      detail = await response.json();
      Object.assign(row, { status: detail.status, stage: detail.stage, progress: detail.progress, error: detail.error_message });
      save();
      if (!['pending', 'processing'].includes(detail.status)) break;
      await page.waitForTimeout(10000);
    }
    await page.screenshot({ path: info.outputPath(`${item.case}-status.png`), fullPage: true });
    expect(detail.status, JSON.stringify(row)).toBe('draft');
    expect(detail.assembly_planner).toBe(item.planner || 'codex');
    expect(detail.parts_count).toBeGreaterThan(0);
    expect(detail.file_urls.io).toBeTruthy();
    await page.reload();
    const canvas = page.locator('.assembly-canvas canvas');
    await expect(page.getByRole('button', { name: '恢复视角', exact: true })).toBeVisible({ timeout: 120000 });
    const solidPath = info.outputPath(`${item.case}-preview.png`);
    const solid = await canvas.screenshot({ path: solidPath });
    const pixels = PNG.sync.read(solid);
    let foreground = 0;
    for (let i = 0; i < pixels.data.length; i += 4) {
      if (Math.abs(pixels.data[i] - 245) + Math.abs(pixels.data[i + 1] - 245) + Math.abs(pixels.data[i + 2] - 247) > 42) foreground++;
    }
    expect(foreground / (pixels.width * pixels.height)).toBeGreaterThan(.01);
    row.preview = { foreground_ratio: foreground / (pixels.width * pixels.height),
      solid: { file: solidPath, sha256: createHash('sha256').update(solid).digest('hex') } };
    const before = await canvas.screenshot();
    await page.getByRole('button', { name: '线框', exact: true }).click();
    await expect.poll(async () => createHash('sha256').update(await canvas.screenshot()).digest('hex'))
      .not.toBe(createHash('sha256').update(before).digest('hex'));
    const wireframePath = info.outputPath(`${item.case}-wireframe.png`);
    const wireframe = await canvas.screenshot({ path: wireframePath });
    row.preview.wireframe = { file: wireframePath, sha256: createHash('sha256').update(wireframe).digest('hex') };
    save();
    for (const [kind, url] of Object.entries(detail.file_urls)) {
      const exported = await request.get(url);
      expect(exported.ok(), kind).toBeTruthy();
      const data = await exported.body();
      expect(data.length, kind).toBeGreaterThan(1);
      const filename = info.outputPath(`${item.case}-${path.basename(new URL(url, info.project.use.baseURL).pathname)}.${kind}`);
      fs.writeFileSync(filename, data);
      row.downloads.push({ kind, url, file: filename, sha256: createHash('sha256').update(data).digest('hex'), bytes: data.length });
    }
    row.quality = detail.quality_report;
    row.status = 'website_transport_verified'; save();
    } catch (error) {
      row.status = 'failed'; row.failure = error.message; save();
      await page.screenshot({ path: info.outputPath(`${item.case}-failure.png`), fullPage: true }).catch(() => {});
    }
  }
  expect(evidence.cases.filter(row => row.status !== 'website_transport_verified').map(row => ({
    case: row.case, status: row.status, failure: row.failure }))).toEqual([]);
  expect(errors).toEqual([]);
});
