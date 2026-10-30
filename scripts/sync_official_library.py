"""Localize the collected vehicle models and publish a reproducible website catalog."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.dialogue import ask_json


TYPES = {
    "sedan": "轿车", "sports_car": "跑车", "suv": "SUV", "hatchback": "两厢车",
    "coupe": "轿跑", "convertible": "敞篷车", "wagon": "旅行车", "pickup": "皮卡",
    "f1": "F1 赛车", "race_car": "赛车", "kart": "卡丁车", "offroad_buggy": "越野赛车",
    "truck": "卡车", "van": "厢式车", "mpv": "MPV", "bus": "巴士", "motorcycle": "摩托车",
    "construction": "工程车辆", "agricultural": "农业车辆", "train": "轨道车辆",
    "trailer": "拖车", "emergency": "应急车辆", "fictional": "虚构车辆", "other": "其他车辆",
}
WORK = ROOT / "runs/official-library-sync"
CATALOG = ROOT / "data/official-library/catalog.zh.json"
VARIANT_LABELS = {"Targa": "Targa 敞篷版", "911 Turbo": "911 Turbo 涡轮增压版", "Turbo": "Turbo 涡轮增压版"}
VARIANT_TYPES = {"Targa": ["convertible", "sports_car"], "911 Turbo": ["sports_car"], "Turbo": ["sports_car"], "Cabrio": ["convertible"]}
PROMPT = """你是汽车和乐高套装中文编目员。将输入的官方套装名称转成准确、自然的简体中文车辆名称。
只输出 JSON 对象 {"items":[{"key":原key,"name_zh":中文名称,"brands":[{"en":英文品牌,"zh":中文品牌}],"types":[类型代码],"identity":"real_vehicle|generic_vehicle|fictional_vehicle|mixed_set","vehicle_year":整数或null,"year_evidence":原名中明确出现的年款文本或空字符串}]}。
品牌用规范名称，例如 Ferrari 法拉利、BMW 宝马、Mercedes-Benz 梅赛德斯-奔驰、McLaren 迈凯伦、MINI 迷你、Land Rover 路虎、Lotus 莲花、Lamborghini 兰博基尼。
车型代号原样保留，例如 F40、GT40 MKII、911 GT3 RS、Countach 5000 Quattrovalvole。Countach 中文名康塔什，保留其英文车型代号；其他有通行中文名的车型也可中英并存。
品牌、车队、角色的中文名称要翻译，真实型号的字母数字不要翻成汉字。不要泛化具体车型，不要补造实车型号。
对于没有现实品牌的货车、警车、赛车等套装，brands 为 [{"en":"LEGO","zh":"乐高原创"}]，identity 为 generic_vehicle；不要猜成某个汽车品牌。
壳牌、埃克森、美孚、马士基等涂装不是汽车制造商；车队也不是制造商。未注明真实制造商时仍用乐高原创。
多车套装可列多个实际品牌和类型。保留联合套装所有车型、不要删掉一个车型。虚构影视车辆用明确角色中文名，品牌无法判定时用 LEGO。
年份只在输入名字本身明确含有实车年份时填写，并且必须在 year_evidence 原样给出。不要用套装发行年猜实车年款，不要根据记忆猜年份；否则 null。
轮式工程、农业、火车分别归 construction/agricultural/train。F1/fomula 赛车归 f1，GT/勒芒赛车归 race_car；普通跑车归 sports_car；越野 SUV 归 suv。
中文名不要包含套装编号，不包含“模型文件”等额外文字。不加 Markdown，不省略记录。
允许的类型：""" + json.dumps(TYPES, ensure_ascii=False)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def key_for(name):
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def enrich(result):
    manifest = read(result / "_reports/manifest.json")
    names = sorted({r["name"] for r in manifest} | {r.get("variant", "") for r in manifest} - {""})
    cache = WORK / "names.zh.json"
    baseline = ROOT / "data/official-library/names.zh.json"
    existing = read(cache) if cache.is_file() else read(baseline) if baseline.is_file() else {}
    pending = [dict(key=key_for(n), name=n) for n in names if n not in existing]
    batches = [pending[i:i+40] for i in range(0, len(pending), 40)]

    def translate(batch):
        identity = hashlib.sha256(json.dumps(batch, ensure_ascii=False).encode()).hexdigest()[:16]
        answer = ask_json([dict(role="system", content=PROMPT), dict(role="user", content=json.dumps(batch, ensure_ascii=False))],
                          WORK / "dialogue", identity, max_tokens=14000, timeout=300, total_timeout=900,
                          reuse_completed=True, model="gpt-6.1-sol", stream=True, reasoning_effort="low", transient_retries=2)
        rows = answer.get("items", [])
        expected = {r["key"]: r["name"] for r in batch}
        if len(rows) != len(batch) or {r.get("key") for r in rows} != set(expected):
            raise ValueError("Translation response does not exactly cover its input")
        output = {}
        for item in rows:
            name = expected[item.pop("key")]
            if not re.search(r"[\u4e00-\u9fff]", item["name_zh"]):
                # Pure variant codes get their Chinese context at publication.
                if name not in {"A", "B", "C", "D", "Turbo", "Targa", "911 Turbo"} and not re.fullmatch(r"[\d A-Z()V-]+", name):
                    raise ValueError("Not a Chinese vehicle name: " + name)
            if not item.get("brands") or not item.get("types") or any(t not in TYPES for t in item["types"]):
                raise ValueError("Missing brand or invalid type: " + name)
            year, evidence = item.get("vehicle_year"), item.get("year_evidence", "")
            if year is not None and (type(year) is not int or str(year) not in name or not evidence or evidence not in name):
                raise ValueError("Ungrounded vehicle year: " + name)
            output[name] = item
        return output

    failures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(translate, batch): batch for batch in batches}
        for i, future in enumerate(as_completed(jobs), 1):
            try:
                existing.update(future.result())
                write_json(cache, existing)
                print(f"Localized {len(existing)}/{len(names)} names ({i}/{len(batches)} batches)", flush=True)
            except Exception as error:
                failures.append(str(error))
                print(f"Translation failed: {error}", flush=True)
    if failures:
        raise RuntimeError(f"{len(failures)} batches failed; rerun enrich to resume")


def filename(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")[:150]


def publish(result, metadata):
    report_dir = result / "_reports"
    manifest = read(report_dir / "manifest.json")
    names = read(WORK / "names.zh.json")
    sets = read(metadata) if metadata else {}
    rows, plan = [], []
    brands = read(ROOT / "frontend/lib/data/vehicle-brands.json")["brands"]
    brand_lookup = {b["name"].casefold(): b for b in brands}
    for item in manifest:
        title = names[item["name"]]
        variant = item.get("variant", "")
        variant_name = VARIANT_LABELS.get(variant, names[variant]["name_zh"]) if variant else ""
        if re.fullmatch(r"[A-Z](?:-Model)?", variant):
            variant_name = variant[0] + " 方案"
        if variant_name and not re.search(r"[\u4e00-\u9fff]", variant_name):
            variant_name += " 方案"
        name = title["name_zh"] + (" - " + variant_name if variant_name else "")
        target = filename(name) + f" [{item['set_num']}].io"
        original = item.get("original_file", item["file"])
        source = result / item["file"]
        destination = result / target
        if not source.is_file() and destination.is_file():
            source = destination
        if not source.is_file() or digest(source) != item["sha256"]:
            raise ValueError(f"Source missing or modified: {source}")
        if destination.exists() and destination != source and digest(destination) != item["sha256"]:
            raise FileExistsError(destination)
        plan.append(dict(source=source.name, target=target, sha256=item["sha256"]))
        set_info = sets.get(item["set_num"], {})
        release_year = int(set_info.get("year") or item.get("year")) if set_info.get("year") or item.get("year") else None
        id = hashlib.sha256((item["set_num"] + "\0" + variant).encode()).hexdigest()[:24]
        translated_brands = []
        for brand in title["brands"]:
            known = brand_lookup.get(brand["en"].casefold(), {})
            translated_brands.append(dict(en=brand["en"], zh=brand["zh"],
                                         id=known.get("id", brand["en"].casefold().replace(" ", "-")), logo=known.get("logo")))
        for brand in translated_brands:
            if brand["en"] == "AEC":
                brand["zh"] = "英国联合设备（AEC）"
        # Generic alternate builds must not inherit a donor vehicle's real identity.
        types = title["types"]
        if variant and variant not in {"Base", "Blue", "Orange", "Official Variants"}:
            v = names[variant]
            if v["types"] != ["other"] and not re.fullmatch(r"(?:[A-Z]-Model|Model [A-Z]|[A-Z](?:\(V2\))?)", variant):
                types = v["types"]
        types = VARIANT_TYPES.get(variant, types)
        thumb = result / "_previews" / (Path(item["file"]).stem + ".png")
        if not thumb.exists():
            thumb = result / "_previews" / (Path(original).stem + ".png")
        preview_name = id + ".png"
        if thumb.exists():
            (result / "_previews" / preview_name).write_bytes(thumb.read_bytes())
        validation = item["validation"]
        row = dict(id=id, name_zh=name, original_name=item["name"], model=title["name_zh"],
                   brands=translated_brands, vehicle_types=types, vehicle_type_labels=[TYPES[t] for t in types],
                   identity=title["identity"], vehicle_year=title.get("vehicle_year"),
                   vehicle_year_evidence=title.get("year_evidence", ""), release_year=release_year,
                   variant=variant, variant_zh=variant_name, set_num=item["set_num"],
                   parts_count=validation["part_instances"], steps_count=validation.get("steps", 0),
                   file=target, original_file=original, sha256=item["sha256"],
                   bytes=source.stat().st_size, source_url=item["source_url"], author=item["author"], provider=item.get("provider"),
                   unresolved_parts=validation.get("unresolved_parts", []),
                   missing_parts=item.get("missing_parts", "unknown"), missing_patterns=item.get("missing_patterns", "unknown"),
                   missing_stickers=item.get("missing_stickers", "unknown"),
                   thumbnail_file="_previews/" + preview_name if thumb.exists() else None,
                   image_url=set_info.get("img_url") or item.get("img_url"),
                   image_kind="model_thumbnail" if thumb.exists() else "set_photo",
                   description_zh=f"{name}。{'、'.join(b['zh'] for b in translated_brands)}，{'、'.join(TYPES[t] for t in types)}。"
                       + f"LEGO {item['set_num']}" + (f"，{release_year} 年发行" if release_year else "")
                       + f"；数字模型含 {validation['part_instances']:,} 个零件实例。"
                       + ("官方替代搭建：" + variant_name + "。" if variant_name else "")
                       + "官方套装的社区数字复刻。")
        rows.append(row)
        item.update(original_file=original, file=target, name_zh=name, library_id=id)
    targets = [p["target"].casefold() for p in plan]
    if len(targets) != len(set(targets)):
        raise ValueError("Chinese filenames are not unique")
    # Persist a recovery plan before touching filenames; a repeated run resumes by hash.
    write_json(report_dir / "chinese-rename-plan.json", plan)
    if not (report_dir / "manifest.before-chinese.json").exists():
        shutil.copy2(report_dir / "manifest.json", report_dir / "manifest.before-chinese.json")
    for entry in plan:
        source, destination = result / entry["source"], result / entry["target"]
        if source != destination:
            if destination.exists():
                source.unlink()
            else:
                source.rename(destination)
    write_json(report_dir / "manifest.json", manifest)
    if (report_dir / "final_integrity.json").is_file():
        integrity = read(report_dir / "final_integrity.json")
        mapping = {p["source"]: p["target"] for p in plan}
        for row in integrity["files"]:
            row["file"] = mapping.get(row["file"], row["file"])
        write_json(report_dir / "final_integrity.json", integrity)
    rows.sort(key=lambda r: (r["brands"][0]["en"] == "LEGO", r["brands"][0]["zh"], r["name_zh"], r["set_num"]))
    write_json(ROOT / "data/official-library/names.zh.json", names)
    catalog = dict(schema="car2lego.official-library.v1", types=TYPES, models=rows,
                   provenance="Community digital recreations of official sets", root_env="CAR2LEGO_MODEL_LIBRARY")
    write_json(CATALOG, catalog)
    write_json(result / "车辆信息.json", catalog)
    verified = []
    for row in rows:
        if digest(result / row["file"]) != row["sha256"]:
            raise ValueError("Hash changed while renaming: " + row["file"])
        verified.append(row["id"])
    actual = {p.name for p in result.glob("*.io")}
    expected = {r["file"] for r in rows}
    if actual != expected:
        raise ValueError("Renamed IO directory differs from catalog")
    write_json(report_dir / "chinese-sync-validation.json", dict(files_verified=len(verified),
        filenames_are_chinese=all(re.search(r"[\u4e00-\u9fff]", r["file"]) is not None for r in rows),
        catalog_ids_unique=len(verified) == len(set(verified)), io_content_unchanged=True,
        unindexed_files=sorted(actual - expected), missing_files=sorted(expected - actual)))
    with (result / "models.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["文件名", "车辆型号", "品牌", "车辆年款", "套装发行年", "车辆类型", "套装编号", "搭建方案", "零件实例数", "未解析零件", "作者", "来源", "原文件名", "SHA256"])
        for row in rows:
            writer.writerow([row["file"], row["name_zh"], "、".join(b["zh"] for b in row["brands"]), row["vehicle_year"], row["release_year"],
                             "、".join(row["vehicle_type_labels"]), row["set_num"], row["variant_zh"], row["parts_count"],
                             "; ".join(row["unresolved_parts"]), row["author"], row["source_url"], row["original_file"], row["sha256"]])
    print(json.dumps(dict(models=len(rows), sets=len({r['set_num'] for r in rows}), renamed=sum(p['source'] != p['target'] for p in plan),
                          missing_release_year=sum(r['release_year'] is None for r in rows), catalog=str(CATALOG)), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["enrich", "publish"])
    parser.add_argument("--result", type=Path, default=Path(r"D:\lego\result"))
    parser.add_argument("--set-metadata", type=Path)
    args = parser.parse_args()
    enrich(args.result) if args.stage == "enrich" else publish(args.result, args.set_metadata)
