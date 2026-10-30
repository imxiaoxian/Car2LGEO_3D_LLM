# Car2LGEO_3D_LLM

**GPT 图片 → 大模型 + Blender 建模 → 乐高结构 → BrickLink Studio `.io`**。

本仓库统一维护 LLM + Blender 与 Hunyuan 多视图方案。旧 `Car2LEGO_3D` 的轮廓拟合和候选优化迁入 `car2lego3d.mesh_envelope`，与主流程的自动/精确尺寸合同分开。详见 [整合说明](docs/unified-workflows.md)。

BrickWright、BrickBench/BrickAgent、LEGO Forge 的源码对照、连接点安装工具和独立装配复核见 [开源装配参考](docs/open-source-assembly-references.md)。

两套路线的架构取舍与同输入比较方法见 [两套方案分析](docs/two-pipeline-analysis-20261029.md)。[SKY-001 核验](docs/skyfall-sky001-research.md) 记录其公开能力和接入边界；当前采用分层规划思路，尚未将其注册为可用模型后端。

汽车模块的真实执行证据分别记录在 [转向轮模块](docs/car-modules-20261030.md)、[闭环联动工具与确定性夹具](docs/closed-linkage-20261030.md) 和 [空场景自主联动试验](docs/autonomous-linkage-20261030.md)。软件回归、确定性夹具与自主试验保留各自结论，不据此宣称整车通过。

[剪刀门诊断](docs/countach-door-20261030.md) 保留失败、离线修复和原轨迹成功续跑的独立记录；[座舱集成诊断](docs/cabin-integration-20261030.md) 检查新增内饰与受保护联动底盘的连接和运动间隙。模块诊断不代表车型外观、实体搭建或整车验收。

## 生成链路

1. 输入品牌、型号和选填年款，通过大模型对话获取车型尺寸估计、外观和配置；未知项保留为空并记录置信度。
2. 只读复用原 Car2LEGO 的 GPT 生图服务，先生成身份锚点，再按依赖生成整车、多角度和细节图片。每组最多 5 张并发，整组完成后再开始下一组；失败保留成功缓存。
3. 大模型分析图片和语义模块，生成受约束的 Blender 脚本，经 Blender MCP 执行建模、检查与修复。建模和视觉复查默认使用独立配置的 `gpt-6-astra`。
4. 根据网格、车型特征和官方 LEGO 结构参考，拟合门窗、灯组、车身、内饰与连接模块，输出 LDraw、零件清单和检查报告。
5. 打包 Studio `.io`，核对归档内容、颜色、变换和步骤。归档通过不等于 Studio GUI、外观或实物验收通过。

默认建模为 `gpt-blender`。桌面可显式选择 Hunyuan 多视图加轮廓拟合，或 Blender + Hunyuan 加原生装配代理。结合模式保留 Blender 语义，将同一参考图和尺寸规格生成的 Hunyuan 网格作为可导航、带哈希的形状参考；不据此生成真实连接器或机构验收结论。

## 本地安装

需要 Git、uv、Python 3.11、Node.js 22.12+、Blender。原 Car2LEGO 服务代码及 Studio 零件库由本机 `D:/lego` 只读提供，不随本仓库发布。

```powershell
git clone https://github.com/imxiaoxian/Car2LGEO_3D_LLM.git
cd Car2LGEO_3D_LLM
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -Python 3.11
```

根据 `.env.example` 配置本项目的 `.env`。`CAR2LEGO_SOURCE_ROOT` 指向原服务目录；`CAR2LEGO_SOURCE_PYTHON` 使用已安装原服务依赖的 Python；`CAR2LEGO_MCP_PYTHON` 可指向本仓库 `.venv/Scripts/python.exe`。API 配置默认继承原服务的 `backend/.env`，也支持本地覆盖；密钥不提交。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-web.ps1
```

默认页面为 `http://127.0.0.1:8778`，API 为 `http://127.0.0.1:8777`。所有方案使用本仓库的环境与任务目录，每次执行一个生成任务。

CLI 分阶段运行：

```powershell
.venv/Scripts/python.exe -m car2lego3d research --brand Lamborghini --model Countach --output runs/spec
.venv/Scripts/python.exe -m car2lego3d image --prompt "Lamborghini Countach" --vehicle-specs runs/spec/spec.json --output runs/images
.venv/Scripts/python.exe -m car2lego3d reconstruct --views runs/images/views.json --vehicle-specs runs/spec/spec.json --output runs/mesh
.venv/Scripts/python.exe -m car2lego3d lego --mesh runs/mesh/calibrated.glb --vehicle-specs runs/spec/spec.json --reference-views runs/images/views.json --output runs/lego
.venv/Scripts/python.exe -m car2lego3d studio --ldraw runs/lego/model.ldr --output runs/studio
```

## 当前能力

前端保留品牌中文名、Logo、其他品牌自定义、车辆照片、任务详情、图片分组、装配查看和下载。不提供比例选择，约 1:12 仅作为体量与细节偏好。

当前大尺寸原生轮组、玻璃与底盘配方尚未完整实现。自动尺寸任务可能保存规划后停止，不能把这种情况标成完整模型或验收通过。历史小尺寸实验可通过显式 CLI 尺寸参数复查，详见 [自动尺寸](docs/scale-requirements.md)。仓库整合不表示这些能力限制已经解决。

`runs/` 保存独立任务、图片、网格、装配、预览、清单和审计信息。旧工作树及历史产物在仓库外的保密备份中保留，不与新任务混用。

## 验证与历史

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/verify_examples.py
cd frontend
npm ci --no-fund --no-audit
npm run build
```

历史开源重建样例仍保留原哈希及验收状态，用于底层导出回归，不代表本方案的建模结果。旧 Car2LEGO 仓库历史通过归档分支及合并父提交保留，迁移不使用强制推送。

- [项目隔离与恢复](docs/project-isolation.md)
- [Blender MCP 建模及模型配置](docs/gpt-blender-experiment.md)
- [模块化车身](docs/modular-exterior.md)
- [透明件拟合](docs/glazing-fitting.md)
- [迁移前历史说明](docs/historical-pipeline.md)

代码使用 MIT；模型、依赖及第三方资产遵守各自许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。模型权重、Studio 零件库、运行环境和密钥不上传。
