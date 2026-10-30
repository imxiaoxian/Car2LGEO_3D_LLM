"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowUpRight, Download, LoaderCircle } from "lucide-react";
import { apiFileUrl, getOfficialModel, OfficialModel } from "@/lib/api";
import ProductImage from "@/components/ProductImage";

const ModelViewer = dynamic(() => import("@/components/viewer/LibraryModelViewer"), {ssr: false});
const knownStatus = (value: string) => value === "Yes" ? "有缺失" : value === "No" ? "未报告缺失" : "未标注";

function ModelDetail({id}: {id: string}) {
  const params = useSearchParams();
  const [model, setModel] = useState<OfficialModel>(), [error, setError] = useState("");
  const [view, setView] = useState("3d"), [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    void getOfficialModel(id).then(data => { if (active) setModel(data); }).catch(() => { if (active) setError("此模型暂时无法加载。"); });
    return () => { active = false; };
  }, [id, retry]);
  const back = `/sets${params.toString() ? "?" + params.toString() : ""}`;
  if (error) return <div className="empty-state"><p role="alert">{error}</p><button className="text-link" onClick={() => setRetry(v => v+1)}>重试</button><Link href={back} className="text-link">返回车型库</Link></div>;
  if (!model) return <div className="empty-state"><LoaderCircle className="spin"/>正在加载模型</div>;
  return <div className="content-width library-detail">
    <Link href={back} className="back-link"><ArrowLeft size={14}/>车型库</Link>
    <div className="library-detail-heading"><div><p className="eyebrow">{model.brands.map(b => b.zh).join(" · ")} / {model.vehicle_type_labels.join(" · ")}</p><h1>{model.name_zh}</h1><p className="secondary">LEGO {model.set_num} · {model.parts_count.toLocaleString()} 个零件实例{model.release_year ? ` · ${model.release_year} 年发行` : ""}</p></div><a className="button primary" href={apiFileUrl(model.download_url)} download={model.filename}><Download size={18}/>下载 .io</a></div>
    <div className="library-view-tabs" role="tablist" aria-label="模型视图"><button role="tab" aria-selected={view === "3d"} onClick={() => setView("3d")}>3D 模型</button><button role="tab" aria-selected={view === "image"} onClick={() => setView("image")}>模型与套装图片</button></div>
    <div role="tabpanel">{view === "3d" ? <ModelViewer id={id}/> : <div className="library-detail-image">{model.thumbnail_url ? <ProductImage src={apiFileUrl(model.thumbnail_url)} alt={model.name_zh}/> : <p className="secondary">此模型没有附带预览图片</p>}<span>{model.image_kind === "model_thumbnail" ? "原文件内嵌预览" : "套装图片"}</span></div>}</div>
    <div className="library-information">
      <section><h2>车辆信息</h2><p className="secondary">{model.description_zh}</p><dl className="library-facts"><div><dt>具体车型 / 套装车辆</dt><dd>{model.model}</dd></div><div><dt>品牌</dt><dd>{model.brands.map(b => b.zh).join("、")}</dd></div><div><dt>车辆类型</dt><dd>{model.vehicle_type_labels.join("、")}</dd></div><div><dt>车辆年款</dt><dd>{model.vehicle_year ? `${model.vehicle_year} 年` : "原始资料未标注"}</dd></div><div><dt>套装发行年</dt><dd>{model.release_year ? `${model.release_year} 年` : "未标注"}</dd></div><div><dt>搭建方案</dt><dd>{model.variant_zh || "主模型"}</dd></div></dl></section>
      <section><h2>模型文件</h2><dl className="library-facts"><div><dt>中文文件名</dt><dd>{model.filename}</dd></div><div><dt>文件大小</dt><dd>{(model.bytes / 1024).toLocaleString(undefined, {maximumFractionDigits: 1})} KB</dd></div><div><dt>零件引用</dt><dd>{model.unresolved_parts.length ? `待补齐：${model.unresolved_parts.join("、")}` : "已解析"}</dd></div><div><dt>原作者缺件说明</dt><dd>{knownStatus(model.missing_parts)}</dd></div><div><dt>印刷 / 贴纸</dt><dd>{knownStatus(model.missing_patterns)} / {knownStatus(model.missing_stickers)}</dd></div><div><dt>作者</dt><dd>{model.author}</dd></div></dl><a className="text-link" href={model.source_url} target="_blank" rel="noreferrer">原始来源与作者说明<ArrowUpRight size={14}/></a><p className="library-source-note">官方套装的社区数字复刻。文件完整性已检查，尚未逐项核对官方说明书；使用请遵守原作者许可。</p></section>
    </div>
  </div>;
}

export default function Page({params}: {params: {id: string}}) {
  return <Suspense fallback={<div className="empty-state"><LoaderCircle className="spin"/></div>}><ModelDetail id={params.id}/></Suspense>;
}
