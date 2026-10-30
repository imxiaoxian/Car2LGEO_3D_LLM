import { useState } from "react";
import { Check, X, LoaderCircle } from "lucide-react";
import { DesignDetail, reviewAppearance } from "@/lib/api";

const moduleNames: Record<string, string> = {
  chassis: "底盘", steering: "转向", suspension: "悬挂", cabin: "座舱", door_left: "左门及玻璃", door_right: "右门及玻璃",
  front: "车头", rear: "车尾", front_lights: "前灯组", rear_lights: "尾灯组", hood: "前盖", rear_cover: "后盖", signature: "车型特征",
  headlamp_mechanism: "跳灯机构",
  wheel_front_left: "左前轮组", wheel_front_right: "右前轮组", wheel_rear_left: "左后轮组", wheel_rear_right: "右后轮组",
  door_front_left: "左前门及玻璃", door_front_right: "右前门及玻璃", door_rear_left: "左后门及玻璃", door_rear_right: "右后门及玻璃",
};
const statuses: Record<string, string> = { pass: "已通过", fail: "未通过", unverified: "未验证", closed: "已关闭", open: "待修复",
  pending: "待执行", running: "执行中", interrupted: "已中断", blocked: "已阻断", assembled: "模块检查通过" };
const reasons: Record<string, string> = {
  missing_binding: "尚未关联实际装配", empty_module: "子模型没有零件", no_verified_external_connection: "缺少与整车连接的证据",
  door_glass_not_in_shared_moving_subtree: "门板与玻璃没有共同运动", independent_role_review_required: "零件用途待独立复核",
  independent_role_review_failed: "零件用途复核未通过", current_revision_check_required: "模型已变化，需要重新检查",
  missing_native_geometry_or_connectivity: "缺少零件几何或连接数据", opaque_glass: "车窗材质不透明", opaque_lens: "灯罩材质不透明",
  motion_not_passed: "机构运动尚未通过", role_part_reused: "不同模块重复使用同一零件", invalid_binding: "装配关联无效",
  dependency_not_assembled: "依赖模块尚未通过", repair_budget_exhausted: "已达到修复上限，需要诊断",
  explicit_retry_required: "等待恢复", module_execution_interrupted: "执行中断，可继续当前检查点",
  tool_budget_exhausted: "已达到本次操作预算", repeated_tool_cycle: "重复操作未取得进展",
};
function reason(value: string) {
  const key = value.split(":")[0];
  if (reasons[key]) return reasons[key];
  if (key.startsWith("missing_") && key.endsWith("_joints")) return "缺少对应的可动机构";
  return "检查证据不足";
}

export default function CreationEvidence({ design, onReviewed }: { design: DesignDetail; onReviewed?: () => void }) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const gate = design.reference_gate, progress = design.module_progress;
  async function submit(status: "pass" | "fail") {
    if (!design.acceptance?.review_inputs) return;
    setBusy(true); setError("");
    try {
      await reviewAppearance(design.id, status, design.acceptance.review_inputs, notes);
      onReviewed?.();
    } catch (err) { setError(err instanceof Error ? err.message : "外观确认未保存"); }
    finally { setBusy(false); }
  }
  if (!gate && !progress && !design.acceptance) return null;
  return <section className="quality-section creation-evidence" aria-label="创作检查进度">
    <div className="section-heading"><h2>创作检查</h2><span className="secondary">{design.acceptance?.accepted ? "交付已通过" : "尚未通过交付验收"}</span></div>
    <div className="quality-gates">
      <div><span>图片检查</span><strong data-status={gate?.image_correct ? "passed" : gate?.status === "blocked" ? "failed" : "unverified"}>{gate?.image_correct ? "已通过" : gate?.status === "ready_with_repairs" ? "局部待修复" : gate?.status === "blocked" ? "需修图" : "未验证"}</strong></div>
      <div><span>建模依据</span><strong data-status={gate?.allowed ? "passed" : "unverified"}>{gate?.allowed ? "允许进入建模" : gate?.status === "blocked" ? "已阻断" : "待检查"}</strong></div>
      <div><span>整车模块</span><strong>{progress?.required != null ? `${progress.complete} / ${progress.required}` : "未验证"}</strong></div>
      <div><span>待修问题</span><strong>{design.repair_tasks?.filter(r => r.status !== "closed").length ?? 0}</strong></div>
      <div><span>外观确认</span><strong>{statuses[design.acceptance?.gates.appearance || "unverified"] || "未验证"}</strong></div>
      <div><span>网站实跑</span><strong>{statuses[design.acceptance?.gates.website || "unverified"] || "未验证"}</strong></div>
    </div>
    {!!gate?.blocked_views?.length && <p className="secondary">需修正视角：{gate.blocked_views.join("、")}</p>}
    {!!design.repair_tasks?.length && <details className="quality-issues"><summary>参考图转交问题 · {design.repair_tasks.length} 项</summary>
      {design.repair_tasks.map(task => <div key={task.id} className="creation-repair"><strong>{statuses[task.status] || "待验证"}</strong><span>{task.expected}</span><span className="secondary">{task.modules.map(m => moduleNames[m] || m).join("、")} · {task.verification}</span></div>)}
    </details>}
    {!!progress?.modules?.length && <details className="quality-issues" open={progress.status !== "pass"}><summary>模块检查 · {progress.complete} / {progress.required}</summary>
      <div className="module-progress-list">{progress.modules.map(module => <div key={module.id}>
        <strong>{moduleNames[module.id] || module.id}</strong><span>{module.parts.length} 个关联零件</span><span data-status={module.status}>{statuses[module.status] || "未验证"}</span>
        {(module.failures.length > 0 || module.unverified.length > 0) && <p>{Array.from(new Set([...module.failures, ...module.unverified].map(reason))).join("；")}</p>}
      </div>)}</div>
    </details>}
    {!!design.module_tasks?.tasks.length && <details className="quality-issues" open><summary>装配任务 · {design.module_tasks.complete} / {design.module_tasks.required}</summary>
      <div className="module-progress-list">{design.module_tasks.tasks.map(task => <div key={task.id}>
        <strong>{moduleNames[task.id] || task.id}</strong>
        <span>{task.attempts} / 3 次尝试{task.candidates ? ` · 候选 ${task.candidates.passing} / ${task.candidates.required}` : ""}</span>
        <span data-status={task.status}>{statuses[task.status] || "未验证"}</span>
        {!!task.dependencies.length && <p>依赖：{task.dependencies.map(id => moduleNames[id] || id).join("、")}</p>}
        {task.reason && task.status !== "assembled" && <p>{reason(task.reason)}</p>}
      </div>)}</div>
    </details>}
    {design.acceptance?.appearance_review_ready && !design.acceptance.accepted && <div className="appearance-review">
      <label htmlFor="appearance-notes">外观评审意见</label>
      <textarea id="appearance-notes" value={notes} onChange={event => setNotes(event.target.value)} maxLength={4000} rows={3} disabled={busy} />
      <div className="dialog-controls">
        <button type="button" className="button secondary-button" disabled={busy} onClick={() => submit("fail")}><X size={16} />需要改进</button>
        <button type="button" className="button primary" disabled={busy} onClick={() => submit("pass")}>{busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}确认外观达标</button>
      </div>
      {error && <p className="error-message" role="alert">{error}</p>}
    </div>}
  </section>;
}
