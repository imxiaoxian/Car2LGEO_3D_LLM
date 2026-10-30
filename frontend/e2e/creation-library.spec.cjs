const { test, expect } = require('@playwright/test');
const path = require('node:path');

test('creation opens the sole archived model with preview and download', async ({page}, info) => {
  let submissions = 0;
  await page.route('**/api/v1/designs', async route => {
    submissions++;
    const data = route.request().postDataJSON();
    expect(data).toEqual({make:'Lamborghini', model:'Countach'});
    await route.continue();
  });
  await page.goto('/?make=兰博基尼&model=Countach');
  await page.getByRole('button', {name:'创建设计', exact:true}).click();
  await expect(page).toHaveURL(/\/sets\/[a-f0-9]{24}\?/);
  await expect(page.getByRole('heading', {name:'兰博基尼康塔什 Countach 5000 Quattrovalvole', exact:true})).toBeVisible();
  await expect(page.getByRole('link', {name:'下载 .io', exact:true})).toBeVisible();
  await expect(page.locator('.library-canvas canvas')).toBeVisible();
  expect(submissions).toBe(1);
  await page.screenshot({path:info.outputPath('creation-archive-result.png')});
});

test('creation lists all matching versions with optional vehicle year and normalized model', async ({page}, info) => {
  await page.route('**/api/v1/designs', route => route.continue());
  await page.goto('/?make=法拉利&model=F%2040&year=1990');
  await page.getByRole('button', {name:'创建设计', exact:true}).click();
  await expect(page).toHaveURL(/\/sets\?.*match_model=F\+40/);
  await expect(page.locator('.catalog-item')).toHaveCount(3);
  await expect(page.getByRole('textbox', {name:'搜索车型', exact:true})).toHaveValue('F 40');
  await expect(page.getByRole('button', {name:'筛选品牌：法拉利'})).toBeVisible();
  await expect(page.getByText('1990 年款 · 含未标注', {exact:true})).toBeVisible();
  await page.screenshot({path:info.outputPath('creation-multiple-versions.png')});
  await page.getByRole('link', {name:'查看模型', exact:true}).first().click();
  await page.getByRole('link', {name:'车型库', exact:true}).click();
  await expect(page.locator('.catalog-item')).toHaveCount(3);
  await page.getByRole('textbox', {name:'搜索车型', exact:true}).fill('F400ZZZ');
  await expect(page.getByRole('heading', {name:'没有相关车型'})).toBeVisible();
  await expect(page).not.toHaveURL(/match_model=/);
});

test('photo-only creation redirects after recognition finds an archived model', async ({page}) => {
  const id = '00000000000000000000000000000002';
  const target = '/sets/82421e957f27137efd395836';
  let reads = 0;
  await page.route('**/api/v1/designs/from-image', route => route.fulfill({json:{id,status:'processing',parts:[],file_urls:{}}}));
  await page.route(`**/api/v1/designs/${id}`, route => {
    reads++;
    return route.fulfill({json:{id,status:reads===1?'processing':'matched',parts:[],file_urls:{},
      library_match:reads===1?null:{url:target,total:1,model_ids:['82421e957f27137efd395836']}}});
  });
  await page.route(`**/api/v1/designs/${id}/status`, route => route.fulfill({json:{id,status:'matched',progress:100}}));
  await page.goto('/');
  await page.getByRole('button', {name:'车辆照片', exact:true}).click();
  await page.getByLabel('上传车辆照片').setInputFiles(path.join(__dirname, '../public/images/ferrari-f40.webp'));
  await page.getByRole('button', {name:'创建设计', exact:true}).click();
  await expect(page).toHaveURL(target);
  expect(reads).toBe(2);
});
