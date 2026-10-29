# 统一工作流

2026-10-29，保留 `imxiaoxian/Car2LGEO_3D_LLM`，整合原 `Car2LEGO_3D` 的重建、轮廓拟合和候选优化能力。默认仍是 LLM + Blender，所有后端使用本仓库的环境和新任务目录。

## 路由

| 桌面建模选项 | 装配选项 | 实际输入与用途 |
| --- | --- | --- |
| `gpt-blender` | `legacy` 或原生代理 | Blender 网格或语义场景；保留主流程自动/精确尺寸合同 |
| `hunyuan-mv` | `legacy` | Hunyuan 校准网格 → 迁移的 `mesh_envelope` 拟合器；尺寸为外观偏好 |
| `hybrid` | `codex` / `local_finetuned` | Blender 语义 + 同参考图和规格的 Hunyuan 形状证据 → 原生装配代理 |

不兼容的组合在创建任务前报错。`auto` 代理仍要求已验收的对比结果。默认 `legacy` 不表示已经启用自主原生装配。

混合任务的 Hunyuan 输出位于 `02-shape`；宿主核验重建完成状态、原始网格和校准文件哈希、米制单位、规格及参考图哈希，再生成 `shape_evidence` JSON 资源。完整顶点和三角面可通过 context 工具按 JSON pointer 分页读取。原始证据与资源都绑定到启动记录，执行结束复核；恢复到新修订时复制并重验。网格前后方向、表面准确度、真实孔轴和承载仍未验证。

## CLI

```powershell
# Open-source reconstruction, explicitly selected.
.venv/Scripts/python.exe -m car2lego3d reconstruct --backend hunyuan-mv --views runs/images/views.json --vehicle-specs runs/spec/spec.json --output runs/shape
# Migrated appearance-first fitter; no exact-scale claim.
.venv/Scripts/python.exe -m car2lego3d mesh-envelope --mesh runs/shape/calibrated.glb --vehicle-specs runs/spec/spec.json --reference-views runs/images/views.json --output runs/envelope
# Wedge candidates only; may call the configured visual reviewer.
.venv/Scripts/python.exe -m car2lego3d mesh-optimize --directory runs/envelope
```

原生代理 CLI 为 `python -m car2lego3d.assembly_website`，可附加 `--shape-evidence runs/shape`，同时仍须提供 `--semantic`、`--request`、`--specifications`、`--references`、`--planner` 和 `--output`。

## 迁移与恢复

29 个拟合器模块迁至 `src/car2lego3d/mesh_envelope`，8 组相应回归迁至 `tests/mesh_envelope`；初始来源哈希见 `mesh-envelope-source-map.json`，后续适配由 Git 记录。主项目已有较新的图像依赖调度，旧调度测试由 `test_reference_set.py` 覆盖，图片原始字节回归保留在 `test_reference_anchor.py`。主尺寸算法、语义机构合同与递归 Studio 导出继续使用主模块。

备份位于 `D:/lego-project-archives/Car2LEGO_3D-20261029`，包含全部 Git 引用、三个工作树的文件归档、未提交补丁和来源清单。归档可能包含 `.env`，禁止上传。`verify_legacy_archive.ps1` 实际恢复 Git 和文件、核对 SHA256，并保留模型、第三方源码及旧环境依赖清单。验证结果以该目录中的 `restoration-verification.json` 为准。

模型复制到本仓库被忽略的 `models/`，Hunyuan 与 TripoSR 源码复制到 `external/`；历史 Blender 工作树的 external 单独归档。`D:/lego` 始终只读。GitHub 删除和本地清理状态单独记录，不因备份完成而自动视为已执行。

## 验证边界

软件回归、前端构建、原生检查及六视图预览分别验证对应的软件和数字几何行为。此次没有运行收费整车生成、同条件整车基准、Studio GUI 验收或实体搭建。结合模式已接通并做合同回归，未据此声明整车质量提升。

### 2026-10-30 迁移检查

- 备份实际恢复后逐文件核对 SHA256：主工作树 5,617 个文件，Seed3D 工作树 626 个，历史 Blender 工作树 14,448 个；模型及第三方源码复制另行核对。
- 统一环境修复了缺失的 Transformers 及混装的 NumPy 文件；`numpy==1.26.4`、`transformers==4.42.4`、`torch==2.5.1+cu124` 已验证，`uv pip check` 通过。
- 在统一环境中从本地权重完整加载 Hunyuan，conditioner、model、VAE 均成功初始化；仅验证加载，未生成整车。
- 迁移拟合器对合成盒体成功生成 222 个零件，并导出 Studio IO；这只是接口冒烟检查，外观和拼装验收仍未通过。
- 前端生产构建通过；桌面路由 Playwright 用例 4 项通过，检查了桌面和移动布局。
- 修复环境后完整 Python 回归：1,237 项通过，14 条弃用等警告，耗时 298.26 秒；日志在本地 `runs/unified-regression-20261030-repaired.log`。
- 发布排除环境、权重、第三方源码、运行产物、装配语料和训练数据；三个新增归档标签的额外历史已做敏感文件名、密钥模式和大文件检查。
