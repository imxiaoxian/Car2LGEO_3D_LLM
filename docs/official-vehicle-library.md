# 中文车辆模型库

网站 `/sets` 接入 `data/official-library/catalog.zh.json`。文件保存在独立目录，默认是 `D:/lego/result`，可通过 `CAR2LEGO_MODEL_LIBRARY` 配置。不会修改原项目代码或数据库。

## 同步

运行 `scripts/sync_official_library.py enrich`，复用本项目的文本模型服务翻译车型并分类。请求及响应保存到本项目的 `runs/official-library-sync`，已完成结果可恢复。名称和型号不代表实车尺寸或官方授权认证。

运行 `scripts/sync_official_library.py publish --set-metadata <set_metadata.json>`，在源哈希匹配后将文件改为中文名，保留具体型号代号、套装编号和替代方案。源清单备份及改名计划在结果目录 `_reports` 中；同步中文 CSV、JSON 和网站目录。重复执行不会重复改名。不要再次执行旧采集脚本的 publish 阶段，以免恢复旧英文命名。

车辆年款与套装发行年使用独立字段。车辆年款仅录入原始名称中明确出现的年份；未标注时为空。无现实品牌及型号的乐高原创车辆，不推测具体制造商。多车套装保留多个品牌与类型；过滤品牌和类型时会匹配任意一个对应标签。

## 文件和预览

- `GET /api/v1/sets/library?q=&brand=&type=&year=release:2024&page=1&limit=24`
- `GET /api/v1/sets/library/{id}`
- `GET /api/v1/sets/library/{id}/download`
- `GET /api/v1/sets/library/{id}/thumbnail`
- `GET /api/v1/sets/library/{id}/preview`，异步返回 processing、ready 或 failed。
- `GET /api/v1/sets/library/{id}/model.glb`

年份也支持 `vehicle:1966` 和 `vehicle:unknown`。下载使用 RFC 5987 中文文件名。下载返回清单对应的文件；路径仅从受控清单解析。兼容性修复单独记录源哈希、修复哈希和原文件备份。

3D 预览直接读取 IO 内的 MPD、嵌套变换、颜色与嵌入零件，再使用本地 Studio 的 LDraw 库补充标准零件；不是把套装照片当成实际模型预览。按源 SHA-256 缓存 GLB，后台串行构建以限制内存使用。缺失的几何会明确列出；整个模型没有可用几何时返回失败。预览不会假造开门、转向或搭建步骤。

封面优先使用 IO 内嵌缩略图，否则显示套装图片并标明来源类别。官方替代搭建的套装照片不代表当前替代模型的实际外观，以 3D 预览及下载文件为准。

保留原始作者、来源、缺件、贴纸及印刷说明。这些是官方套装的社区数字复刻，并非 LEGO 发布的 CAD。完整性检查不等于逐项对照说明书或实体搭建验收。

## 奥迪 42160 的旧零件原点迁移

2023 年的源文件内嵌了以顶部为原点的轮胎和轮毂总成。当前 Studio 的同编号零件以轴心为原点，且旧 `3468c01` 完整总成在新库对应 `100943p01`；直接用新库加载会使轮胎、轮毂和传动接头分离。

`scripts/repair_audi_wheel_origins.py` 仅接受已核实源哈希的这一个文件，默认在本项目 runs 中输出候选文件及检查报告。它按零件局部坐标修正全部三份模型记录，移除三项过时的内嵌零件定义，并使用当前标准件。保留其余零件、子模型、步骤及档案元数据。检查四组轮胎与轮圈同心、轮轴方向及轮毂安装位置，确认标准件几何、连接与碰撞数据存在。

加 `--apply` 后先备份原文件和元数据，再同步下载、中文目录、CSV 及完整性清单。报告为 `_reports/audi-wheel-origin-fix.json`，备份为 `_reports/before-audi-wheel-origin-fix/`。数字检查不代表 Studio GUI 或实物验收。
