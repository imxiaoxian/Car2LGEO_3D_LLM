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

### 清理状态

实现提交 `f422515` 及三个历史归档标签已推送，草稿 [PR #1](https://github.com/imxiaoxian/Car2LGEO_3D_LLM/pull/1) 已建立。2026-10-30 用户完成新的 GitHub 设备授权，本机确认取得 `delete_repo` 权限后，旧远端 `imxiaoxian/Car2LEGO_3D` 删除成功；登录状态下复查返回 HTTP 404，保留仓库仍可访问。本地旧工作树清理命令此前在启动前被执行环境自动审批以 `blocked by policy` 拒绝，未提供更具体原因，三个旧目录仍存在。本地和远端删除状态分别记录，详细状态保存在仓库外备份的 `deletion-status.json`。

### 汽车模块进展

2026-10-30 的真实转向轮试验完成了描述检索选件、原生连接、碰撞修复、嵌套转向与滚动、六视图反馈及 IO 导出回读。两个九姿态单关节扫描和 81 个组合姿态均通过；固定支座未改变。该历史版本完整回归 1,251 项通过，前端生产构建通过。详细证据、哈希和复现命令见 [汽车模块记录](car-modules-20261030.md)。

后续新增 `linkage_motion` 闭环机构求解，保留原始连接点对及插入位置，在关节限位内求解从动角，并逐姿态复核原生碰撞、连通及约束秩。33 件确定性四轮底盘夹具通过 9 个双轮联动姿态、4 轮分别滚动的 324 个组合姿态、38 对原始接触保持、四轮中立布局、六视图预览及 IO 图回读。前轮轴线实际转动 30 度，后轮和固定车架在转向时不动。该结果证明工具和夹具的数字闭环，尚不是代理自主选件搭建联动底盘的结果。旧失败记录保留；普通 `motion` 和整车门槛没有被替换。见 [联动底盘记录](closed-linkage-20261030.md)。轴向防脱、承载、实体、Studio GUI、整车外观及通用物体生成仍未完成。

联动版本完整 Python 回归 1,274 项通过，14 条警告，耗时 580.46 秒；75 项定向回归、前端生产构建及 106 个历史样例哈希/件数核对通过。首轮完整回归发现新增指南使克隆工作流索引错位，已修复并新增资源索引断言，原有 12,000 字符上限未放宽。当前来源下的底盘夹具已重新运行至 `linkage-chassis-native-v4`，旧试验及失败日志保留。

提交 `5c5a295` 新增空场景自主联动底盘诊断入口及八项原生库验收器回归。最终源码完整本地回归 1,282 项通过，14 条警告，耗时 764.41 秒；独立前端生产构建及 106 个历史文件哈希/件数核对通过。验收器要求真实选件记录、竖直转向主销、四个独立圆孔轴承、实际前轮轴线响应、当前像素反馈、IO 回读和来源不变；其单元夹具不能证明代理自主成功。复现命令和验收边界见 [自主联动底盘试验](autonomous-linkage-20261030.md)。

首轮空场景实测耗尽 2,400 秒预算，未通过自主联动诊断。代理后期修复了拉杆接触保持，但最终仍有 4/9 个转向姿态发生轮胎与固定车架碰撞，普通运动和静态检查均未保持到最终修订，预览与导出尚未完成。上下文 v23 修复关节表及机构失败摘要在后续决策中丢失的问题；11 项新增回归、79 项定向回归、完整 1,293 项 Python 回归、前端生产构建及 106 个历史文件哈希/件数核对通过。当前源码确定性底盘夹具 v5 再次通过，实际六视图已查看。

第二轮独立空场景实测 `autonomous-linkage-live-v2` 在相同预算内以 2,068.890 秒、59 次代理动作完成，自主联动诊断通过：自行检索并查询六种原生零件，搭建 20 件、7 关节底盘，最终修订 13 的静态检查、普通运动、9 个联动姿态和 324 个个别车轮滚动组合姿态均通过，20 对原始接触保持。两个前轮轴线实际转动约 30 度，后轮和固定车架在转向时不动；当前六视图像素反馈、导出、IO 图回读及来源不变均核实。中途独立 45 度从动转向碰撞通过将该关节限位收至任务允许的 ±30 度修复，要求的主转向范围保留，原始失败轨迹未修改。单次成功不代表稳定成功率，也不是整车完成；预览轮胎仍显蓝色，配色、外观、Studio GUI、实体、防脱及承载仍未验收。完整报告及哈希见 [自主联动底盘试验](autonomous-linkage-20261030.md)。
