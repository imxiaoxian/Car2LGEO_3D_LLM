"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowRight, ChevronDown, ChevronLeft, ChevronRight, Download, LoaderCircle, Search, X } from "lucide-react";
import { apiFileUrl, getOfficialModels, OfficialModelPage } from "@/lib/api";
import ProductImage from "@/components/ProductImage";
import BrandSelector from "@/components/BrandSelector";
import { findVehicleBrand, VehicleBrand } from "@/lib/vehicle-brands";

function LibraryBrowse() {
  const location = useSearchParams();
  const locationQuery = location.toString();
  const [queryString, setQueryString] = useState(locationQuery);
  const authoredLocations = useRef(new Set([locationQuery]));
  const latestLocation = useRef(locationQuery);
  const params = new URLSearchParams(queryString);
  const [data, setData] = useState<OfficialModelPage>();
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [retry, setRetry] = useState(0), [query, setQuery] = useState(params.get("q") || params.get("match_model") || "");
  const change = (name: string, value: string) => {
    setQueryString(previous => {
      const next = new URLSearchParams(previous);
      if (["q", "brand", "year"].includes(name)) {
        if (name !== "q" && next.get("match_model")) next.set("q", next.get("match_model")!);
        ["match_make", "match_model", "match_year"].forEach(key => next.delete(key));
      }
      if (value) next.set(name, value); else next.delete(name);
      if (name !== "page") next.delete("page");
      return next.toString();
    });
  };
  useEffect(() => {
    if (authoredLocations.current.has(locationQuery)) {
      if (locationQuery === latestLocation.current) authoredLocations.current = new Set([locationQuery]);
      return;
    }
    const next = new URLSearchParams(locationQuery);
    setQueryString(locationQuery); setQuery(next.get("q") || next.get("match_model") || "");
  }, [locationQuery]);
  useEffect(() => {
    if (window.location.search.slice(1) === queryString) return;
    latestLocation.current = queryString;
    authoredLocations.current.add(queryString);
    window.history.replaceState(null, "", `/sets${queryString ? "?" + queryString : ""}`);
  }, [queryString]);
  useEffect(() => {
    if (query === (params.get("q") || params.get("match_model") || "")) return;
    const timer = setTimeout(() => change("q", query), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, queryString]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    void getOfficialModels(queryString).then(value => { if (active) setData(value); })
      .catch(() => { if (active) setError("车型库暂时无法加载。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [queryString, retry]);
  const clear = () => { setQuery(""); setQueryString(""); };
  const hasFilters = ["q", "brand", "type", "year", "match_model"].some(key => params.get(key));
  const brands = useMemo<VehicleBrand[]>(() => (data?.facets.brands || []).map(brand => {
    const known = findVehicleBrand(brand.id) || findVehicleBrand(brand.en);
    return { id: brand.id, name: brand.en, name_zh: brand.zh, logo: brand.logo || known?.logo || "",
      aliases: known?.aliases || [], popular_rank: known?.popular_rank ?? null };
  }), [data?.facets.brands]);
  const selectedBrand = brands.find(brand => brand.id === params.get("brand"));
  const selectedType = data?.facets.types.find(type => type.id === params.get("type"));
  const year = params.get("year");
  const yearLabel = year === "vehicle:unknown" ? "未标注年款" : year ? `${year.split(":")[1]} 年${year.startsWith("vehicle:") ? "款" : "发行"}` : "";
  const selectedFilters = [
    { key: "brand", label: selectedBrand?.name_zh || params.get("brand"), name: "品牌" },
    { key: "year", label: yearLabel, name: "年份" },
    { key: "match_year", label: params.get("match_year") ? `${params.get("match_year")} 年款 · 含未标注` : "", name: "匹配年款" },
    { key: "type", label: selectedType?.label || params.get("type"), name: "车辆类型" },
  ].filter(filter => filter.label);
  return <div className="catalog-page content-width official-library">
    <div className="catalog-title"><p className="eyebrow">车辆模型库</p><h1>车型档案</h1><p className="secondary">{data ? `${data.collection_total.toLocaleString()} 个 Studio 模型 · ${data.facets.brands.length} 个品牌` : "正在加载模型库"}</p></div>
    <div className="catalog-toolbar library-toolbar" role="search" aria-label="筛选车型档案">
      <label className="search-field library-search"><Search size={19} aria-hidden="true"/><input aria-label="搜索车型" placeholder="搜索车型、型号或套装编号" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button className="icon-button" title="清空搜索" aria-label="清空搜索" onClick={() => setQuery("")}><X size={16}/></button>}</label>
      <BrandSelector value={params.get("brand") || ""} custom={false} filterBrands={brands}
        disabled={!data} onChange={value => change("brand", value)} />
      <label className="field library-filter"><span>年份</span><select aria-label="筛选年份" value={year || ""} onChange={e => change("year", e.target.value)}><option value="">全部年份</option><optgroup label="车辆年款">{data?.facets.vehicle_years.map(y => <option key={y} value={`vehicle:${y}`}>{y} 年款</option>)}<option value="vehicle:unknown">未标注年款</option></optgroup><optgroup label="套装发行年">{data?.facets.release_years.map(y => <option key={y} value={`release:${y}`}>{y} 年发行</option>)}</optgroup></select><ChevronDown size={17} aria-hidden="true"/></label>
      <label className="field library-filter"><span>车辆类型</span><select aria-label="筛选车辆类型" value={params.get("type") || ""} onChange={e => change("type", e.target.value)}><option value="">全部类型</option>{data?.facets.types.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select><ChevronDown size={17} aria-hidden="true"/></label>
    </div>
    <div className="results-count library-results"><span aria-live="polite">{loading ? <><LoaderCircle size={14} className="spin"/> 正在加载</> : `${data?.total.toLocaleString() || 0} 个模型`}</span>
      <div className="library-selected-filters" aria-label="已选筛选条件">{selectedFilters.map(filter => <span key={filter.key}>{filter.label}<button type="button" className="icon-button" aria-label={`清除${filter.name}筛选`} title={`清除${filter.name}筛选`} onClick={() => change(filter.key, "")}><X size={13}/></button></span>)}</div>
      {hasFilters && <button className="text-link library-clear" onClick={clear}>清除筛选<X size={14}/></button>}</div>
    {error ? <div className="empty-state" role="alert"><p>{error}</p><button className="button secondary-button" onClick={() => setRetry(v => v+1)}>重新加载</button></div> : <>
      <div className="catalog-grid" aria-busy={loading}>{data?.models.map(model => <article className="catalog-item" key={model.id}>
        <Link href={`/sets/${model.id}${queryString ? "?" + queryString : ""}`} className="library-cover" aria-label={`预览 ${model.name_zh}`}>
          {model.thumbnail_url ? <ProductImage src={apiFileUrl(model.thumbnail_url)} alt={model.name_zh} className="catalog-image"/> : <div className="catalog-no-image"><span>{model.brands.map(b => b.zh).join(" · ")}</span><strong>{model.name_zh}</strong></div>}
          <span className="library-image-kind">{model.image_kind === "model_thumbnail" ? "模型预览" : "套装图片"}</span>
        </Link>
        <div className="catalog-kicker"><span>{model.vehicle_type_labels.join(" / ")}</span><span>{model.release_year ? `${model.release_year} 年发行` : "发行年未标注"}</span></div>
        <h2><Link href={`/sets/${model.id}${queryString ? "?" + queryString : ""}`}>{model.name_zh}</Link></h2>
        <p className="library-brand">{model.brands.map(b => b.zh).join(" · ")}{model.vehicle_year ? ` · ${model.vehicle_year} 年款` : ""}</p>
        <div className="catalog-meta"><span>LEGO {model.set_num}</span><span>{model.parts_count.toLocaleString()} 件</span></div>
        {model.unresolved_parts.length > 0 && <p className="library-warning">{model.unresolved_parts.length} 项零件引用待补齐</p>}
        <div className="library-actions"><Link className="text-link" href={`/sets/${model.id}${queryString ? "?" + queryString : ""}`}>查看模型<ArrowRight size={15}/></Link><a className="text-link" href={apiFileUrl(model.download_url)} download={model.filename}><Download size={16}/>下载 .io</a></div>
      </article>)}</div>
      {!loading && !data?.models.length && <div className="empty-state"><Search size={30}/><h2>没有相关车型</h2><button className="text-link" onClick={clear}>清除筛选</button></div>}
      {data && data.pages > 1 && <nav className="library-pagination" aria-label="车型分页"><button className="icon-button" title="上一页" aria-label="上一页" disabled={data.page <= 1 || loading} onClick={() => change("page", String(data.page-1))}><ChevronLeft size={20}/></button><span>第 {data.page} / {data.pages} 页</span><button className="icon-button" title="下一页" aria-label="下一页" disabled={data.page >= data.pages || loading} onClick={() => { change("page", String(data.page+1)); window.scrollTo({top: 100, behavior: "smooth"}); }}><ChevronRight size={20}/></button></nav>}
    </>}
  </div>;
}

export default function BrowsePage() {
  return <Suspense fallback={<div className="empty-state"><LoaderCircle className="spin"/>正在加载车型库</div>}><LibraryBrowse/></Suspense>;
}
