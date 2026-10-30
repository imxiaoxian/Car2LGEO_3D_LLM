"use client";

import { useId, useRef, useState } from "react";
import { Check, ChevronDown, LayoutGrid, Plus, Search, X } from "lucide-react";
import { findVehicleBrand, normalizeBrand, popularBrands, searchVehicleBrands, vehicleBrands, VehicleBrand } from "@/lib/vehicle-brands";

export function BrandLogo({ brand }: { brand: VehicleBrand }) {
  const [failed, setFailed] = useState(false);
  return <span className="brand-logo" data-brand={brand.id} aria-hidden="true">
    {failed || !brand.logo ? <span className="brand-logo-fallback">{brand.name.slice(0, 2).toUpperCase()}</span>
      // Local catalog assets already have fixed dimensions and are validated at import.
      // eslint-disable-next-line @next/next/no-img-element
      : <img src={brand.logo} alt="" width={48} height={32} loading="lazy" onError={() => setFailed(true)} />}
  </span>;
}

interface BrandSelectorProps {
  value: string;
  custom: boolean;
  onChange: (value: string, custom: boolean) => void;
  optional?: boolean;
  disabled?: boolean;
  filterBrands?: VehicleBrand[];
}

export default function BrandSelector({ value, custom, onChange, optional = false, disabled = false, filterBrands }: BrandSelectorProps) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"popular" | "all">("popular");
  const filtering = filterBrands !== undefined;
  const selected = filtering ? filterBrands.find(brand => brand.id === value) : custom ? undefined : findVehicleBrand(value);
  const available = filterBrands ?? vehicleBrands;
  const popular = filtering ? available.filter(brand => brand.popular_rank !== null)
    .sort((a, b) => a.popular_rank! - b.popular_rank!) : popularBrands;
  const brands = query.trim() ? filtering ? available.filter(brand => [brand.name, brand.name_zh, ...brand.aliases]
    .some(name => normalizeBrand(name).includes(normalizeBrand(query)))) : searchVehicleBrands(query)
    : tab === "popular" ? popular : available;

  function open() {
    setQuery("");
    setTab(filtering ? "all" : "popular");
    dialog.current?.showModal();
    searchInput.current?.focus();
  }

  function close() {
    dialog.current?.close();
    trigger.current?.focus();
  }

  function choose(brand?: VehicleBrand) {
    onChange((filtering ? brand?.id : brand?.name) || "", !filtering && !brand);
    close();
  }

  return <div className="brand-selection">
    <div className="brand-field-wrap">
      <button ref={trigger} type="button" className="field brand-trigger" disabled={disabled}
        aria-label={filtering ? `筛选品牌：${selected?.name_zh || "全部品牌"}` : `选择品牌${value ? `：${selected?.name_zh || value}` : ""}`} aria-haspopup="dialog"
        aria-controls={`${id}-dialog`} onClick={open}>
        <span className="brand-field-copy"><span className="brand-field-label">品牌{optional ? "（可选）" : ""}</span>
          <span className={`brand-field-value ${!filtering && !value && !custom ? "brand-placeholder" : ""}`}>
            {selected && <BrandLogo key={selected.id} brand={selected} />}
            {filtering && !selected && <LayoutGrid size={20} className="brand-all-icon" aria-hidden="true" />}
            <span>{filtering ? selected?.name_zh || "全部品牌" : custom ? "其他" : selected?.name_zh || "选择品牌"}</span>
          </span>
        </span><ChevronDown size={17} aria-hidden="true" />
      </button>
      {optional && (value || custom) && <button type="button" className="brand-clear icon-button" aria-label="清除品牌"
        title="清除品牌" disabled={disabled} onClick={() => onChange("", false)}><X size={14} /></button>}
    </div>
    {!filtering && custom && <label className="field custom-brand-field" htmlFor={`${id}-custom`}><span>自定义品牌</span>
      <input id={`${id}-custom`} name="customBrand" aria-label="自定义品牌名称" value={value}
        onChange={event => onChange(event.target.value, true)} placeholder="品牌名称" autoComplete="off"
        maxLength={100} required={!optional} disabled={disabled} />
    </label>}
    <dialog ref={dialog} id={`${id}-dialog`} className="brand-dialog" aria-labelledby={`${id}-title`}
      onCancel={event => { event.preventDefault(); close(); }}
      onClick={event => { if (event.target === event.currentTarget) close(); }}>
      <div className="brand-dialog-content">
        <div className="brand-dialog-heading"><h2 id={`${id}-title`}>选择品牌</h2>
          <button type="button" className="icon-button" aria-label="关闭品牌选择" title="关闭" onClick={close}><X size={20} /></button>
        </div>
        <label className="search-field brand-search"><Search size={18} aria-hidden="true" />
          <input ref={searchInput} aria-label="搜索品牌" placeholder="搜索中文名或英文名" value={query}
            onChange={event => setQuery(event.target.value)} autoComplete="off"
            onKeyDown={event => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                event.preventDefault();
                if (brands.length === 1) choose(brands[0]);
              }
            }} />
          {query && <button type="button" className="icon-button" title="清空品牌搜索" aria-label="清空品牌搜索"
            onClick={() => { setQuery(""); searchInput.current?.focus(); }}><X size={16} /></button>}
        </label>
        <div className="brand-list-heading"><div className="segmented" role="group" aria-label="品牌范围">
          <button type="button" aria-pressed={tab === "popular"} onClick={() => setTab("popular")}>热门</button>
          <button type="button" aria-pressed={tab === "all"} onClick={() => setTab("all")}>全部品牌</button>
        </div><span role="status">{brands.length} 个品牌</span></div>
        <div className="brand-options-scroll" key={`${tab}-${query}`}>
          <div className="brand-options" aria-label="品牌列表">
            {brands.map(brand => <button type="button" className="brand-option" key={brand.id}
              aria-label={`${brand.name_zh} ${brand.name}`} aria-pressed={selected?.id === brand.id} onClick={() => choose(brand)}>
              <BrandLogo brand={brand} /><span className="brand-option-names"><strong>{brand.name_zh}</strong><span>{brand.name}</span></span>
              {selected?.id === brand.id && <Check size={16} className="brand-option-check" aria-hidden="true" />}
            </button>)}
          </div>
          {!brands.length && <div className="brand-empty"><Search size={26} strokeWidth={1.4} /><p>没有匹配的品牌</p></div>}
        </div>
        {filtering ? <button type="button" className="brand-other brand-all-option" onClick={() => choose()} aria-label="显示全部品牌">
          <span className="brand-other-icon"><LayoutGrid size={22} strokeWidth={1.5} /></span>
          <strong>全部品牌</strong>{!value && <Check size={17} className="brand-all-check" aria-hidden="true" />}
        </button> : <button type="button" className="brand-other" onClick={() => choose()} aria-label="其他，自定义品牌">
          <span className="brand-other-icon"><Plus size={22} strokeWidth={1.5} /></span>
          <span><strong>其他</strong><span>自定义品牌名称</span></span><ChevronDown size={17} className="brand-other-chevron" />
        </button>}
      </div>
    </dialog>
  </div>;
}
