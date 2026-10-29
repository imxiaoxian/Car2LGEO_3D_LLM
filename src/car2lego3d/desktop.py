"""Original Car2LEGO frontend contract over the isolated multiview pipeline."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from threading import Condition, RLock, Thread
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

from PIL import Image, ImageOps

from .common import ROOT, digest, write_json
from .project import desktop_backend
from .gpt_reference import configuration
from .recipes import classify, selection_record
from .requirements import SCALES, automatic_requirements, plan_design, requirements_for_scale
from .vehicle import tyre_diameter


RENDER_VIEWS = ("front", "rear", "left", "right", "top", "bottom", "front_left", "rear_right",
    "left_door_window", "right_door_window", "front_lights", "rear_lights",
    "cabin_front_connection", "cabin_rear_connection", "front_wheel", "rear_wheel")


def read(path, fallback=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else deepcopy(fallback)


def assembly_mission(request):
    """Keep planner routing and job identity out of the shared design inputs."""
    fields = ("brand", "model", "year", "variant", "scale", "scale_mode",
              "custom_request", "color", "seed", "reconstruction_backend")
    return {name: request[name] for name in fields if name in request}


def bridge(operation, output, input_path=None):
    source, python = configuration()
    command = [python, "-B", str(ROOT / "scripts/frontend_bridge_worker.py"), operation,
               "--source", str(source), "--output", str(output)]
    if input_path:
        command += ["--input", str(input_path)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=300,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    if result.returncode:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix(".log").write_bytes(result.stdout + result.stderr)
        raise ValueError(f"{operation} service failed; inspect local bridge log")
    return read(output)


def ui_policy(scale, scale_mode="appearance"):
    original = read(ROOT / "data/ui/catalog.json")["policies"][scale]
    actual = requirements_for_scale(scale)
    original.update(version=actual["version"], dimension_tolerances_percent=actual["dimension_tolerances_percent"],
        component_tolerances_percent={"tyre_diameter": actual["dimension_tolerances_percent"]["front_tyre_diameter"],
                                      "tyre_width": actual["dimension_tolerances_percent"]["front_tyre_width"]},
        required_measurements=actual["required_measurements"], reference_views=actual["reference_views"],
        max_step_parts=actual["max_parts_per_step"], scale_mode=scale_mode,
        size_margin_percent=15 if scale_mode == "appearance" else None)
    return original


def physical(spec, request, applied=False, audit=None):
    automatic = request.get("scale_mode") == "auto" or not request.get("scale")
    scale = None if automatic else request["scale"]
    denominator = None if automatic else int(scale.split(":")[1])
    sources = spec.get("sources", {})
    measurements = {}
    for name, item in spec.get("measurements", {}).items():
        if item.get("value") is None:
            continue
        source = sources.get(item.get("source"), {})
        grounded = item.get("status") in {"manufacturer", "reported", "measured"}
        value = item["value"]
        mm = value * 25.4 if item["unit"] == "inch" else value
        measurements[name] = dict(value=value, unit=item["unit"], method="sourced" if grounded else "estimated",
            grounded=grounded, conflicted=False, source_url=source.get("url"), source_kind=item.get("status"),
            source_status="fetched" if grounded else "model_estimated", vehicle_match="source_scoped" if grounded else "model_assumed",
            confidence=item.get("confidence", 1 if grounded else 0),
            target_model_mm=mm / denominator if denominator and item["unit"] in {"mm", "inch"} else None,
            evidence=item.get("evidence", ""), definition=name)
    for axle in ("front", "rear"):
        diameter = tyre_diameter(spec, axle)
        if diameter is not None:
            items = [measurements.get(f"{axle}_{field}", {}) for field in ("tyre_width", "tyre_aspect", "rim_diameter")]
            measurements[f"{axle}_tyre_diameter"] = dict(value=diameter, unit="mm", method="derived",
                grounded=all(i.get("grounded", False) for i in items), conflicted=False,
                confidence=min(i.get("confidence", 0) for i in items),
                target_model_mm=diameter / denominator if denominator else None,
                source_url=items[0].get("source_url"), evidence="Tyre specification conversion")
    required = (automatic_requirements() if automatic else requirements_for_scale(scale))["required_measurements"]
    missing = [n for n, item in spec.get("measurements", {}).items() if item.get("value") is None]
    return dict(version="car2lego3d-physical-v1", vehicle=dict(make=request.get("brand", request.get("make", "")),
        model=request["model"], year=request.get("year")), scale=scale,
        selected_variant=spec.get("variant") or request.get("variant", ""),
        scale_mode="auto" if automatic else request.get("scale_mode", "exact"), applied_to_design=applied,
        overall_grounded=all(measurements.get(n, {}).get("grounded") for n in ("length", "width", "height", "wheelbase")),
        measurements=measurements, missing=missing, required_measurements=required, conflicts=[],
        missing_required=[n for n in required if n not in measurements],
        sources=[dict(url=s.get("url", ""), title=s.get("title", ""), status="fetched",
            measurement_count=sum(m.get("source") == key for m in spec.get("measurements", {}).values()))
            for key, s in sources.items() if s.get("url")],
        acquisition=spec.get("acquisition"), manufacturer_verified=spec.get("manufacturer_verified", False),
        research=dict(skill="vehicle-dialogue" if (audit or {}).get("mode") == "model_dialogue" else "vehicle-spec-search",
            mode=(audit or {}).get("mode"), model=(audit or {}).get("model"), turns=(audit or {}).get("turns", 0),
            version="2", status="completed",
            documents_read=len((audit or {}).get("opened_pages", [])),
            queries=[dict(query=q["query"], purpose="vehicle specifications", status="searched", engine="Bing",
                results=q.get("results", [])) for q in (audit or {}).get("calls", [])]))


class Stopped(Exception):
    pass


class DesktopStore:
    def __init__(self, jobs):
        self.jobs = jobs
        self.root = jobs.directory
        self.condition = Condition(RLock())
        self.active = None
        self.worker = None
        self.process = None
        self.geometry_lock = RLock()
        self.runtime_session = uuid4().hex

    def directory(self, id):
        return self.jobs.job_directory(id)

    def metadata(self, id):
        return read(self.directory(id) / "desktop.json", {})

    def artifacts(self, id):
        directory = self.directory(id)
        name = self.metadata(id).get("artifacts", "artifacts")
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Invalid artifact directory")
        return path

    def state(self, id):
        with self.condition:
            state = read(self.directory(id) / "status.json", {})
            from .job_runtime import assembly_state
            runtime = read(self.directory(id) / "runtime.json", {})
            attached = assembly_state(self.directory(id), runtime)
            if attached and state.get("status") not in {"paused", "cancelled"}:
                state = {**state, **attached}
            status = state["status"]
            if status in {"completed", "draft"}:
                from .creation_evidence import evidence
                status = "completed" if evidence(self.artifacts(id), read(self.directory(id) / "request.json", {}))["acceptance"]["accepted"] else "draft"
            elif status in {"running", "processing", "pending"}:
                from .job_runtime import executing
                runtime = read(self.directory(id) / "runtime.json", {})
                live = self.active == id or executing(runtime)
                status = "processing" if live else "failed"
            elif status == "interrupted":
                status = "failed"
            error_message = state.get("error")
            if status == "failed" and state["status"] in {"running", "processing", "pending", "interrupted"} and not error_message:
                error_message = "执行进程已退出，已保留完成的图片和模型，可恢复任务。"
            if status == "failed" and state.get("stage") == "reference_images":
                dispatch = read(self.artifacts(id) / "01-image/car2lego/reference-dispatch.json", {})
                if dispatch.get("status") == "failed":
                    failures = dispatch.get("failed_details", {})
                    messages = [f"{view}: {failures[view]['message']}"
                                for view in dispatch.get("failed_views", [])
                                if isinstance(failures.get(view), dict) and isinstance(failures[view].get("message"), str)]
                    if messages:
                        error_message = "参考图生成失败：" + "；".join(messages)
            return dict(id=id, status=status, stage=state.get("stage", status),
                        progress=state.get("progress", 100 if status == "draft" else 0),
                        error_message=error_message, library_match=state.get("library_match"),
                        runtime=read(self.directory(id) / "runtime.json"))

    def update(self, id, **values):
        with self.condition:
            path = self.directory(id) / "status.json"
            data = read(path, {})
            data.update(values)
            write_json(path, data)

    def assert_idle(self, except_id=None):
        from .job_runtime import executing
        if (self.active and self.active != except_id) or self.jobs.active:
            raise RuntimeError("已有生成任务运行中，请等待完成。")
        for path in self.root.glob("*/runtime.json"):
            if path.parent.name != except_id and executing(read(path, {})):
                raise RuntimeError("另一个网站进程或生成子进程仍在执行任务，请等待完成。")

    def submit(self, payload, image=None, parent=None):
        from .assembly_agent import selected_planner
        assembly_planner = selected_planner(payload.get("assembly_planner"))
        reconstruction_backend = desktop_backend(payload.get("reconstruction_backend"))
        if reconstruction_backend == "hunyuan-mv" and assembly_planner != "legacy":
            raise ValueError("Hunyuan 单独重建使用轮廓拟合；原生装配代理请选择结合模式。")
        if reconstruction_backend == "hybrid" and assembly_planner == "legacy":
            raise ValueError("结合模式需要选择原生装配代理。")
        make, model = payload.get("make", ""), payload.get("model", "")
        if not isinstance(make, str) or not isinstance(model, str) or len(make) > 100 or len(model) > 100:
            raise ValueError("Invalid vehicle identity")
        if any(ord(c) < 32 for c in make + model):
            raise ValueError("车型名称不能包含控制字符。")
        if not image and (not make.strip() or not model.strip()):
            raise ValueError("请选择品牌并填写车型。")
        year = payload.get("year") or None
        if year is not None and (isinstance(year, bool) or not str(year).isdigit() or not 1900 <= int(year) <= 2030):
            raise ValueError("年款应为 1900 至 2030，或留空。")
        custom = payload.get("customization_text", "")
        if not isinstance(custom, str) or len(custom) > 6000:
            raise ValueError("改装要求超过长度限制。")
        color = payload.get("color")
        if color is not None:
            from .lego import PALETTE
            if type(color) is not int or color not in PALETTE:
                raise ValueError("不支持的乐高配色。")
        image_bytes = None
        if image:
            with Image.open(BytesIO(image)) as opened:
                if opened.format not in {"JPEG", "PNG", "WEBP"} or opened.width * opened.height > 40_000_000:
                    raise ValueError("请选择 JPG、PNG 或 WebP 车辆照片。")
                normalized = ImageOps.exif_transpose(opened).convert("RGB")
                normalized.thumbnail((2048, 2048))
                buffer = BytesIO()
                normalized.save(buffer, "PNG")
                image_bytes = buffer.getvalue()
        prefer_library = payload.get("reuse_library", True)
        if type(prefer_library) is not bool:
            raise ValueError("reuse_library must be a boolean")
        reuse_library = prefer_library and not parent and not custom.strip() and color is None
        if reuse_library and make.strip() and model.strip():
            from .official_library import official_library
            match = official_library().match_vehicle(make, model, year)
            if match:
                return dict(status="matched", library_match=match)
        with self.condition:
            self.assert_idle()
            id = uuid4().hex
            directory = self.root / id
            request = dict(brand=make.strip(), model=model.strip(), year=int(year) if year else None,
                variant=str(year or ""), scale=None, scale_mode="auto", custom_request=custom, parent_design_id=parent,
                color=payload.get("color"), reuse_library=reuse_library, assembly_planner=assembly_planner,
                reconstruction_backend=reconstruction_backend,
                seed=42, created_at=datetime.now(timezone.utc).isoformat())
            write_json(directory / "request.json", request)
            write_json(directory / "status.json", dict(status="pending", stage="starting", progress=0, error=None))
            write_json(directory / "desktop.json", dict(artifacts="artifacts", workflow="multiview_v1"))
            if image_bytes:
                (directory / "source.png").write_bytes(image_bytes)
            elif parent and (self.directory(parent) / "source.png").exists():
                shutil.copyfile(self.directory(parent) / "source.png", directory / "source.png")
            self.active = id
            from .job_runtime import save_runtime
            save_runtime(directory / "runtime.json", self.runtime_session, status="running", stage="starting")
            self.worker = Thread(target=self.run, args=(id,), daemon=True)
            self.worker.start()
        return self.detail(id)

    def checkpoint(self, id, stage, progress):
        with self.condition:
            while read(self.directory(id) / "status.json")["status"] == "paused":
                self.condition.wait(1)
            if read(self.directory(id) / "status.json")["status"] == "cancelled":
                raise Stopped()
            self.update(id, status="processing", stage=stage, progress=progress)
            from .job_runtime import save_runtime
            save_runtime(self.directory(id) / "runtime.json", self.runtime_session, status="running", stage=stage)

    def command(self, id, args):
        with (self.directory(id) / "pipeline.log").open("a", encoding="utf-8") as log:
            with self.condition:
                if read(self.directory(id) / "status.json")["status"] == "cancelled":
                    raise Stopped()
                self.process = subprocess.Popen([str(a) for a in args], cwd=ROOT, stdout=log,
                    stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
                process = self.process
                from .job_runtime import save_runtime
                save_runtime(self.directory(id) / "runtime.json", self.runtime_session, status="running",
                    stage=read(self.directory(id) / "status.json").get("stage"), child=process)
            code = process.wait()
            with self.condition:
                self.process = None
                if read(self.directory(id) / "status.json")["status"] == "cancelled":
                    raise Stopped()
            if code:
                raise ValueError("生成阶段失败，请查看任务日志后恢复。")

    def run(self, id):
        try:
            directory, artifacts = self.directory(id), self.artifacts(id)
            request = read(directory / "request.json")
            assembly_planner = request.get("assembly_planner", "legacy")
            use_agent = assembly_planner != "legacy"
            source = directory / "source.png"
            if source.exists() and not request.get("recognized"):
                self.checkpoint(id, "recognition", 2)
                features = bridge("recognize", directory / "recognition.json", source)
                request.update(brand=request["brand"] or features["make"], model=request["model"] or features["model"], recognized=True)
                # An omitted year stays omitted; image recognition is not production-year evidence.
                write_json(directory / "request.json", request)
            if request.get("reuse_library") and not request.get("parent_design_id") and not request.get("custom_request") and request.get("color") is None:
                self.checkpoint(id, "library_matching", 3)
                from .official_library import official_library
                match = official_library().match_vehicle(request["brand"], request["model"], request.get("year"))
                if match:
                    self.update(id, status="matched", stage="library_matched", progress=100, library_match=match, error=None)
                    return
            spec_path = directory / "vehicle-specs.json"
            if not spec_path.exists():
                self.checkpoint(id, "vehicle_research", 5)
                result = self.research(request)
                if result["status"] != "ready":
                    raise ValueError(result.get("error") or "车型资料不完整：" + "；".join(result.get("questions", [])))
                write_json(spec_path, result["spec"])
                write_json(directory / "research-audit.json", result.get("audit", {}))
            spec = read(spec_path)
            plan = plan_design(spec, request["scale"], scale_mode=request.get("scale_mode", "exact"))
            write_json(directory / "requirements.json", plan)
            if not use_agent and not plan["generation"]["draft_recipe_available"]:
                raise ValueError("当前装配配方不可用：" + " ".join(plan["generation"]["blockers"]))
            spec_args = ["--vehicle-specs", str(spec_path)]
            prefix = [sys.executable, "-u", "-m", "car2lego3d"]
            backend = desktop_backend(request.get("reconstruction_backend"))
            if backend == "hunyuan-mv" and use_agent or backend == "hybrid" and not use_agent:
                raise ValueError("Reconstruction backend is incompatible with selected assembly planner")
            request["reconstruction_backend"] = backend
            write_json(directory / "request.json", request)
            views, mesh, lego, studio = (artifacts / n for n in ("01-image", "02-mesh", "03-lego", "04-studio"))
            prompt = f"{request['brand']} {request['model']} {request.get('year') or ''}. {request.get('custom_request', '')}"
            if request.get("color") is not None:
                prompt += f" LEGO body color ID {request['color']}."
            stages = [
                ("reference_images", 12, views / "views.json", [*prefix, "image", "--prompt", prompt,
                    "--output", views, *spec_args, *(["--reference-image", source] if source.exists() else [])]),
                ("reconstruction", 45, mesh / "calibrated.glb", [*prefix, "reconstruct", "--views", views / "views.json",
                    "--output", mesh, "--backend", "gpt-blender" if backend == "hybrid" else backend,
                    "--resolution", "256", "--reconstruction-steps", "50", *spec_args]),
                ("assembly", 75, lego / "model.ldr", [*prefix, "mesh-envelope" if backend == "hunyuan-mv" else "lego", "--mesh", mesh / "calibrated.glb",
                    *(["--scale-mode", request.get("scale_mode", "exact")] if backend != "hunyuan-mv" else []),
                    "--output", lego, *(["--scale", request["scale"]] if request.get("scale") else []),
                    "--reference-views", views / "views.json", *spec_args,
                    *(["--color", request["color"]] if request.get("color") is not None else [])]),
                ("exporting", 90, studio / "model.io", [*prefix, "studio", "--ldraw", lego / "model.ldr", "--output", studio]),
            ]
            if use_agent:
                mission_path = directory / "assembly-mission.json"
                write_json(mission_path, assembly_mission(request))
                stages = stages[:2]
                if backend == "hybrid":
                    stages.append(("shape_reconstruction", 60, artifacts / "02-shape/calibrated.glb",
                        [*prefix, "reconstruct", "--views", views / "views.json", "--output", artifacts / "02-shape",
                         "--backend", "hunyuan-mv", "--resolution", "256", "--reconstruction-steps", "50", *spec_args]))
                stages.append(("assembly_agent", 75, lego / "agent/agent.json",
                    [sys.executable, "-u", "-m", "car2lego3d.assembly_website", "--planner", assembly_planner,
                     "--request", mission_path, "--semantic", mesh / "semantic-scene.json",
                     "--specifications", spec_path, "--references", views / "views.json",
                     "--output", lego / "agent", "--publish-artifacts", artifacts,
                     *(["--shape-evidence", artifacts / "02-shape"] if backend == "hybrid" else [])]))
            done = read(directory / "checkpoints.json", {})
            upstream_changed = False
            for stage, progress, output, command in stages:
                self.checkpoint(id, stage, progress)
                if stage == "assembly" and request.get("color") is None:
                    color_path = directory / "reference-color.json"
                    color_record = read(color_path, {})
                    image_hash = digest(views / "reference.png")
                    if color_record.get("image_sha256") != image_hash:
                        features = bridge("recognize", directory / "reference-appearance.json", views / "reference.png")
                        from .lego import PALETTE
                        selected = features["closest_lego_color"]
                        if selected not in PALETTE:
                            raise ValueError("识别到的车身颜色尚无对应配方，请补充可用的乐高颜色。")
                        color_record = dict(color=selected, image_sha256=image_hash, source="GPT reference appearance analysis")
                        write_json(color_path, color_record)
                    command += ["--color", color_record["color"]]
                module_draft = (stage == "assembly_agent" and output.is_file()
                    and read(output, {}).get("stop_reason") == "module_blocked")
                if upstream_changed or module_draft or not (output.exists() and done.get(stage) == digest(output)):
                    if stage == "assembly_agent" and output.exists():
                        command.append("--retry")
                    self.command(id, command)
                    upstream_changed = True
                    done[stage] = digest(output)
                    write_json(directory / "checkpoints.json", done)
                if stage == "reference_images":
                    from .multiview import load_views
                    manifest, _ = load_views(output)
                    selection = selection_record(classify(spec, manifest.get("brief")))
                    write_json(artifacts / "00-recipe/recipe.json", selection)
                    if not use_agent and selection["builder"] is None:
                        raise ValueError("该车身类型尚无可用配方，已保留参考图。")
            if use_agent:
                agent = read(lego / "agent/agent.json", {})
                if not agent.get("export", {}).get("ok") or not (studio / "model.io").is_file():
                    raise ValueError("装配方案尚未生成可导出的草稿，请查看 Agent 诊断。")
                self.checkpoint(id, "validation", 98)
                self.geometry(id)
                self.update(id, status="draft", stage="draft", progress=100, error=None)
                return
            self.checkpoint(id, "rendering", 94)
            self.geometry(id)
            source_root, source_python = configuration()
            presentation = artifacts / "05-presentation/presentation.json"
            assembly_hash = digest(lego / "assembly.json")
            if read(presentation, {}).get("assembly_sha256") != assembly_hash:
                self.command(id, [source_python, "-B", ROOT / "scripts/frontend_bridge_worker.py", "presentation",
                    "--source", source_root, "--input", lego / "assembly.json", "--request", directory / "request.json",
                    "--output", presentation])
            self.checkpoint(id, "validation", 98)
            audit = lego / "native-audit.json"
            if read(audit, {}).get("assembly_sha256") != assembly_hash:
                self.command(id, [source_python, "-B", ROOT / "scripts/native_audit_worker.py", "--source", source_root,
                                  "--assembly", lego / "assembly.json", "--output", audit])
            self.checkpoint(id, "draft", 100)
            self.update(id, status="draft", error=None)
        except Stopped:
            pass
        except Exception as error:
            with self.condition:
                if read(self.directory(id) / "status.json")["status"] != "cancelled":
                    self.update(id, status="failed", error=str(error))
        finally:
            with self.condition:
                from .job_runtime import save_runtime
                save_runtime(self.directory(id) / "runtime.json", self.runtime_session, status="stopped",
                    stage=read(self.directory(id) / "status.json", {}).get("stage"))
                self.active = None
                self.condition.notify_all()

    def research(self, request):
        identity = dict(brand=request.get("brand", request.get("make")), model=request["model"],
                        variant=str(request.get("year") or request.get("variant") or ""))
        result = self.jobs.research.start(identity)
        while result["status"] == "running":
            time.sleep(.5)
            result = self.jobs.research.get(result["id"])
        return result

    def appearance_review(self, id, payload):
        from .vehicle_delivery import core_case, record_appearance
        with self.condition:
            if self.state(id)["status"] not in {"draft", "completed"}:
                raise ValueError("请等待完整装配与检查完成后再确认外观。")
            request = read(self.directory(id) / "request.json")
            case = core_case(request["brand"], request["model"])
            if not case or not isinstance(payload.get("inputs"), dict):
                raise ValueError("缺少有效的车型或受检文件版本。")
            result = record_appearance(case, self.artifacts(id), status=payload.get("status"),
                inputs=payload["inputs"], notes=payload.get("notes", ""))
            self.update(id, status="completed" if result["accepted"] else "draft")
            return result

    def run_assembly(self, id, arguments):
        try:
            self.checkpoint(id, "lego", 68)
            command = [sys.executable, "-u", "-m", "car2lego3d.assembly_website"]
            for name, value in arguments.items():
                if value is None or value is False:
                    continue
                flag = "--" + ("planner" if name == "backend" else name.replace("_", "-"))
                command.extend([flag] if value is True else [flag, value])
            self.command(id, command)
            artifacts = self.artifacts(id)
            result = read(artifacts / "03-lego/agent/agent.json", {})
            if not result.get("export", {}).get("ok") or not (artifacts / "04-studio/model.io").is_file():
                raise ValueError("装配尚未完成导出，已保留模块检查点和诊断。")
            self.geometry(id)
            self.update(id, status="draft", stage="draft", progress=100, error=None)
        except Stopped:
            pass
        except Exception as error:
            if read(self.directory(id) / "status.json", {}).get("status") != "cancelled":
                self.update(id, status="failed", error=str(error))
        finally:
            with self.condition:
                from .job_runtime import save_runtime
                save_runtime(self.directory(id) / "runtime.json", self.runtime_session, status="stopped",
                    stage=read(self.directory(id) / "status.json", {}).get("stage"))
                self.active = None
                self.condition.notify_all()

    def control(self, id, action, module=None):
        with self.condition:
            state = self.state(id)
            from .job_runtime import executing, controllable_assembly
            runtime = read(self.directory(id) / "runtime.json", {})
            if self.active != id and controllable_assembly(self.directory(id), runtime):
                if action == "pause" and state["status"] == "processing":
                    self.update(id, status="paused")
                elif action == "resume" and state["status"] == "paused":
                    self.update(id, status="processing", error=None)
                elif action == "cancel":
                    self.update(id, status="cancelled", stage="cancelled")
                else:
                    raise ValueError("装配进程仍在执行；请使用暂停、继续或取消，不能重复启动。")
                return self.state(id)
            if self.active != id and executing(runtime):
                raise RuntimeError("任务由另一个存活的网站进程执行，请在该服务中操作。")
            if action == "pause" and self.active == id:
                self.update(id, status="paused")
            elif action == "cancel":
                self.update(id, status="cancelled", stage="cancelled")
                if self.active == id and self.process and self.process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
                    else:
                        self.process.terminate()
                self.condition.notify_all()
            elif action in {"resume", "retry"}:
                self.assert_idle(except_id=id)
                if self.active == id:
                    self.update(id, status="processing", error=None)
                    self.condition.notify_all()
                else:
                    if state["status"] not in {"failed", "paused", "draft", "cancelled"}:
                        raise ValueError("当前任务不能恢复。")
                    assembly_arguments = None
                    launch = self.artifacts(id) / "03-lego/agent/launch.json"
                    pending = launch.with_name("pending-recovery.json")
                    if runtime.get("kind") == "standalone_assembly" or launch.is_file() or pending.is_file():
                        from .job_runtime import bound_assembly
                        from .assembly_recovery import resume_arguments
                        if runtime.get("kind") == "standalone_assembly" and bound_assembly(self.directory(id), runtime) is None:
                            raise ValueError("装配任务的输入或修订已改变，不能恢复旧执行。")
                        assembly_arguments = resume_arguments(self.directory(id), self.artifacts(id))
                        write_json(self.directory(id) / "desktop.json", {**self.metadata(id),
                            "artifacts": assembly_arguments["publish_artifacts"].relative_to(self.directory(id).resolve()).as_posix()})
                    elif action == "retry" or not self.metadata(id):
                        old = self.artifacts(id)
                        revision = "revisions/" + uuid4().hex + "/artifacts"
                        target = self.directory(id) / revision
                        done = {}
                        for stage, folder, file in (("reference_images", "01-image", "views.json"),
                                                     ("reconstruction", "02-mesh", "calibrated.glb")):
                            if (old / folder / file).is_file():
                                shutil.copytree(old / folder, target / folder)
                                done[stage] = digest(target / folder / file)
                        write_json(self.directory(id) / "desktop.json", dict(artifacts=revision,
                            workflow="multiview_v1", retry_scope="full assembly", requested_module=module))
                        write_json(self.directory(id) / "checkpoints.json", done)
                    self.active = id
                    from .job_runtime import save_runtime
                    save_runtime(self.directory(id) / "runtime.json", self.runtime_session, status="running", stage="resuming")
                    self.update(id, status="pending", error=None)
                    self.worker = Thread(target=self.run_assembly if assembly_arguments else self.run,
                        args=(id, assembly_arguments) if assembly_arguments else (id,), daemon=True)
                    self.worker.start()
            else:
                raise ValueError("当前状态不支持该操作。")
        return self.state(id)

    def scene(self, id):
        presentation = self.artifacts(id) / "05-presentation/scene.json"
        if presentation.is_file():
            return read(presentation)
        path = self.artifacts(id) / "03-lego/assembly.json"
        if not path.is_file():
            raise FileNotFoundError()
        rows = read(path)["parts"]
        parts = [dict(id=f"p{i}", part_num=p["part"] + ".dat", color_id=p["color"], module=p["module"],
                      feature="", transform=dict(position=p["position"], rotation=p["rotation"])) for i, p in enumerate(rows)]
        steps = sorted({p["step"] for p in rows})
        return dict(version="car2lego3d-scene-v1", parts=parts,
            modules=[dict(id=m, depends_on=[]) for m in sorted({p["module"] for p in rows})],
            steps=[dict(index=i+1, module="assembly", parts=[f"p{j}" for j,p in enumerate(rows) if p["step"] == step])
                   for i,step in enumerate(steps)], joints=[])

    def geometry(self, id):
        with self.geometry_lock:
            assembly = self.artifacts(id) / "03-lego/assembly.json"
            if read(assembly, {}).get("schema") == "assembly-desktop.v1":
                path = assembly.parent / "agent/geometry.json"
                if not path.is_file():
                    raise FileNotFoundError("Assembly geometry has not been resolved")
                return path
            from .part_library import assembly_library
            from hashlib import sha256
            source, _ = configuration()
            library = assembly_library(assembly, source / 'Studio 2.0/ldraw')
            geometry = library.geometry(read(assembly)['parts'])
            dependencies = library.dependency_record()
            key = sha256(json.dumps(dict(assembly=digest(assembly), dependencies=dependencies), sort_keys=True).encode()).hexdigest()
            path = assembly.parent / ('geometry-' + key[:16] + '.json')
            if not path.is_file():
                write_json(path, geometry)
                write_json(path.with_suffix('.dependencies.json'), dependencies)
            return path

    def detail(self, id):
        from .creation_evidence import evidence
        with self.condition:
            directory, artifacts = self.directory(id), self.artifacts(id)
            request, state = read(directory / "request.json"), self.state(id)
            rows = read(artifacts / "03-lego/assembly.json", {}).get("parts", [])
            parts = [dict(part_num=part, ldraw_color_id=color, quantity=count)
                     for (part,color),count in Counter((p["part"], p["color"]) for p in rows).items()]
            base = f"/api/v1/designs/{id}"
            files = {kind: f"/api/v1/export/{kind}/{id}" for kind, relative in
                {"io": "04-studio/model.io", "ldr": "03-lego/model.ldr", "csv": "03-lego/inventory.csv",
                 "mpd": "05-presentation/model.mpd", "pdf": "05-presentation/instructions.pdf",
                 "graph": "03-lego/agent/assembly-graph.json", "checks": "03-lego/agent/checks.json",
                 "steps": "03-lego/agent/steps.json", "glb": "03-lego/agent/assembly.glb",
                 "agent": "03-lego/agent/agent.json", "motion": "03-lego/agent/motion.json",
                 "dependencies": "03-lego/agent/dependencies.json", "custom_parts": "03-lego/agent/custom-parts.zip",
                 "module_tasks": "03-lego/agent/module-tasks.json",
                 "blend": "02-mesh/model.blend", "semantic": "02-mesh/semantic-scene.json",
                 "reconstruction": "02-mesh/calibrated.glb", "assembly_blend": "06-acceptance/assembly.blend",
                 "presentation": "06-acceptance/presentation.json", "delivery": "06-acceptance/delivery-report.json",
                 "bom": "03-lego/agent/bom.json", "modules": "03-lego/agent/module-progress.json",
                 "reference_gate": "01-image/reference-gate.json", "repairs": "01-image/repair-tasks.json"}.items()
                if (artifacts / relative).is_file()}
            if rows:
                files.update(xml=f"/api/v1/export/xml/{id}", quality=base+"/quality")
            reference_records = self.reference_records(id)
            references = [dict(view="rear" if view == "back" else view, model=item.get("model", "unknown"),
                url=base+"/reference-images/"+view, synthetic=True,
                label=item.get("label") or ("副驾座椅与座舱" if view == "passenger_seat" else None),
                category="detail" if item.get("category") == "detail" else "exterior",
                purpose=item.get("focus", item.get("purpose")), state=item.get("state"),
                modules=item.get("modules", []), consistency=item.get("consistency"))
                for view,item in reference_records.items()]
            image_record = read(artifacts / "01-image/image.json", {})
            if "overview" not in reference_records and (artifacts / "01-image/reference.png").exists():
                references.insert(0, dict(view="overview", model=image_record.get("model", "unknown"),
                    url=base+"/reference-images/overview", synthetic=True, category="exterior"))
            spec = read(directory / "vehicle-specs.json", {})
            scale = request.get("scale")
            automatic = request.get("scale_mode") == "auto" or not scale
            car = dict(id=id, make=request["brand"], model=request["model"], year=request.get("year"),
                       created_at=request.get("created_at", datetime.fromtimestamp(directory.stat().st_ctime, timezone.utc).isoformat()))
            quality = self.quality(id) if rows else None
            dimensions, deviations = self.native_dimensions(id)
            selection = read(artifacts / "03-lego/recipe-selection.json", {})
            calls = Counter(read(artifacts / "01-image/car2lego/service-record.json", {}).get("calls", []))
            return dict(id=id, **{k:v for k,v in state.items() if k != "id"}, car=car, parts=parts,
                **evidence(artifacts, request),
                parts_count=len(rows), created_at=car["created_at"], file_urls=files,
                workflow_version="assembly_agent_v1" if (artifacts / "03-lego/agent/agent.json").is_file() else "multiview_v1", requested_scale=scale,
                assembly_planner=request.get("assembly_planner", "legacy"),
                assembly_agent=read(artifacts / "03-lego/agent/agent.json"),
                delivery_report=read(artifacts / "06-acceptance/delivery-report.json"),
                scale_mode="auto" if automatic else request.get("scale_mode", "exact"),
                scale_requirements=None if automatic else ui_policy(scale, request.get("scale_mode", "exact")) | {"applied_to_design": bool(spec)},
                physical_specifications=physical(spec, request, bool(rows), read(directory / "research-audit.json", {})) if spec else None,
                scene_url=base+"/scene" if rows else None,
                assembly_preview_url=base+"/assembly-preview" if (artifacts / "03-lego/native-ldraw.png").exists() else None,
                quality_report=quality, reference_images=references,
                inspection_images=[dict(view=view, category=item["category"], synthetic=False,
                    url=base+"/inspection-images/"+view) for view, item in self.inspection_records(id).items()],
                reference_dispatch=read(artifacts / "01-image/car2lego/reference-dispatch.json"),
                actual_dimensions_mm=dimensions, scale_deviation_percent=deviations,
                official_build_references=[dict(set_num=g["set"], name=g["name"], instructions_url=g["url"],
                    product_url=g["url"]) for g in selection.get("official_references", [])],
                renders=[dict(view=v, url=base+"/renders/"+v) for v in RENDER_VIEWS
                         if (artifacts / f"05-presentation/render_{v}.png").exists()],
                parent_design_id=request.get("parent_design_id"), customization_request=request.get("custom_request"),
                job=dict(state=state["status"], stage=state["stage"], progress=state["progress"],
                    calls=dict(text=calls.get("text", 0), image=calls.get("image", 0)), limits=dict(text=None, image=None), module_repairs={}))

    def reference_records(self, id):
        directory = self.artifacts(id) / "01-image"
        manifest = read(directory / "views.json", {})
        dispatch = read(directory / "car2lego/reference-dispatch.json", {})
        worker = read(directory / "car2lego/references/manifest.json", {})
        if worker.get("images") and (dispatch.get("status") == "running" or not manifest):
            return {item["view"]: {**item, "file": "car2lego/" + item["path"]}
                    for item in worker["images"]}
        if manifest.get("shots"):
            return manifest["shots"]
        records = dict(manifest.get("views", {}))
        # During generation only the worker manifest exists; expose published images.
        if not records:
            records = {item["view"]: {**item, "file": "car2lego/" + item["path"]}
                       for item in worker.get("images", [])}
        return records

    def inspection_records(self, id):
        directory = self.artifacts(id) / "06-acceptance"
        presentation = read(directory / "presentation.json", {})
        glb = self.artifacts(id) / "03-lego/agent/assembly.glb"
        if not glb.is_file() or presentation.get("inputs", {}).get("glb_sha256") != digest(glb):
            return {}
        records = {}
        for item in presentation.get("images", []):
            view = item.get("view", "")
            path = (directory / item.get("file", "")).resolve()
            if (re.fullmatch(r"[a-z0-9_]{1,80}", view) and view not in records
                    and item.get("category") in {"whole", "detail"}
                    and path.is_relative_to(directory.resolve()) and path.is_file()
                    and digest(path) == item.get("sha256")):
                records[view] = item
        return records

    def inspection_file(self, id, view):
        item = self.inspection_records(id).get(view)
        if not item:
            raise FileNotFoundError("Missing or changed inspection image")
        return self.artifacts(id) / "06-acceptance" / item["file"]

    def reference_file(self, id, view):
        directory = (self.artifacts(id) / "01-image").resolve()
        record = self.reference_records(id).get(view)
        if record:
            path = (directory / record["file"]).resolve()
            if not path.is_relative_to(directory) or not path.is_file():
                raise FileNotFoundError("Missing reference image")
            if record.get("sha256") and digest(path) != record["sha256"]:
                raise ValueError("Reference image hash mismatch")
            return path
        if view == "overview" and (directory / "reference.png").is_file():
            return directory / "reference.png"
        raise FileNotFoundError("Unknown reference image")

    def native_audit(self, id):
        assembly = self.artifacts(id) / "03-lego/assembly.json"
        audit = read(self.artifacts(id) / "03-lego/native-audit.json", {})
        return audit if assembly.exists() and audit.get("assembly_sha256") == digest(assembly) else {}

    def native_dimensions(self, id):
        audit = self.native_audit(id)
        if "bounds_ldu" not in audit or audit.get('bounds_coverage', 'complete') != 'complete':
            return {}, {}
        low, high = audit["bounds_ldu"]
        actual = {key: round((high[axis]-low[axis])*.4, 3) for key,axis in (("length",0), ("width",2), ("height",1))}
        spec = read(self.directory(id) / "vehicle-specs.json", {})
        request = read(self.directory(id) / "request.json")
        scale = request.get("scale")
        if request.get("scale_mode") == "auto" or not scale:
            return actual, {}
        deviations = {key: round((value/(spec["measurements"][key]["value"]/int(scale.split(":")[1]))-1)*100, 2)
                      for key,value in actual.items() if spec.get("measurements", {}).get(key, {}).get("value")}
        return actual, deviations

    def quality(self, id):
        agent = read(self.artifacts(id) / "03-lego/agent/agent.json", {})
        if agent:
            check = agent.get("checks", {}).get("result", {})
            status = lambda value: {"pass": "passed", "fail": "failed"}.get(value, "unverified")
            unknown = check.get("unknown", [])
            return dict(digital_status="draft", precision_verified=False,
                gates=dict(**{k: status(check.get("gates", {}).get(k))
                              for k in ("parts", "transforms", "connections", "collisions")},
                    mechanisms=status(agent.get("motion", {}).get("result", {}).get("status")),
                    appearance="unverified", dimensions="unverified"),
                studio=dict(status="unverified"), physical=dict(status="unverified"),
                issues=[dict(kind=json.dumps(item, ensure_ascii=False), parts=[], modules=[]) for item in unknown])
        report = read(self.artifacts(id) / "03-lego/requirements-report.json", {})
        audit = self.native_audit(id)
        checks = report.get("dimension_checks", [])
        dimensions, deviations = self.native_dimensions(id)
        request = read(self.directory(id) / "request.json")
        automatic = request.get("scale_mode") == "auto" or not request.get("scale")
        tolerances = {} if automatic else requirements_for_scale(request["scale"])["dimension_tolerances_percent"]
        if automatic:
            checks = []
        native_failures = [key for key,value in deviations.items() if abs(value) > tolerances[key]]
        return dict(digital_status="draft", precision_verified=False,
            gates=dict(parts="unverified", connections=audit.get("connectivity", {}).get("status", "unverified"),
                collisions=audit.get('collision_status') or (("passed" if audit["collision_count"] == 0 else "failed") if "collision_count" in audit else "unverified"),
                dimensions=("measured" if dimensions else "unverified") if automatic else
                    "failed" if native_failures or any(c["status"] == "out_of_tolerance" for c in checks) else "unverified",
                appearance="unverified", mechanisms="unverified"),
            studio=dict(status="unverified"), physical=dict(status="unverified"),
            issues=[dict(kind=f"{c['field']} 超出比例容差", parts=[], modules=["body_and_chassis"])
                    for c in checks if c["status"] == "out_of_tolerance"] +
                   [dict(kind=f"{key} 原生零件外包尺寸超差", parts=[], modules=["body_and_chassis"]) for key in native_failures])

    def export(self, id, kind):
        paths = {"io": "04-studio/model.io", "ldr": "03-lego/model.ldr", "csv": "03-lego/inventory.csv",
                 "mpd": "05-presentation/model.mpd", "pdf": "05-presentation/instructions.pdf",
                 "graph": "03-lego/agent/assembly-graph.json", "checks": "03-lego/agent/checks.json",
                 "steps": "03-lego/agent/steps.json", "glb": "03-lego/agent/assembly.glb",
                 "agent": "03-lego/agent/agent.json", "motion": "03-lego/agent/motion.json",
                 "dependencies": "03-lego/agent/dependencies.json", "custom_parts": "03-lego/agent/custom-parts.zip",
                 "module_tasks": "03-lego/agent/module-tasks.json",
                 "blend": "02-mesh/model.blend", "semantic": "02-mesh/semantic-scene.json",
                 "reconstruction": "02-mesh/calibrated.glb", "assembly_blend": "06-acceptance/assembly.blend",
                 "presentation": "06-acceptance/presentation.json", "delivery": "06-acceptance/delivery-report.json",
                 "bom": "03-lego/agent/bom.json", "modules": "03-lego/agent/module-progress.json",
                 "reference_gate": "01-image/reference-gate.json", "repairs": "01-image/repair-tasks.json"}
        if kind in paths:
            return self.artifacts(id) / paths[kind]
        if kind != "xml":
            raise FileNotFoundError()
        root = Element("INVENTORY")
        for part in self.detail(id)["parts"]:
            item = SubElement(root, "ITEM")
            for key, value in dict(ITEMTYPE="P", ITEMID=part["part_num"], MINQTY=part["quantity"]).items():
                SubElement(item, key).text = str(value)
            # Studio's table supplies the different BrickLink and LDraw namespaces.
            colors = read(ROOT / "data/ui/catalog.json")["colors"]
            color = colors.get(str(part["ldraw_color_id"]))
            if color is None:
                raise ValueError("该颜色尚未配置 BrickLink 映射，不能导出采购清单。")
            SubElement(item, "COLOR").text = str(color["bricklink"])
        path = self.artifacts(id) / "03-lego/wanted.xml"
        path.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))
        return path

    def export_filename(self, id, kind):
        from .vehicle_delivery import core_case
        request = read(self.directory(id) / "request.json")
        case = core_case(request["brand"], request["model"])
        names = {"countach": "兰博基尼康塔什 Countach 5000 Quattrovalvole", "f40": "法拉利 F40",
            "nissan-r34": "《速度与激情2》日产 Skyline GT-R (R34) 跑车",
            "bugatti-chiron-pur-sport": "布加迪 Chiron Pur Sport 超级跑车"}
        name = names.get(case, request["brand"] + " " + request["model"])
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:160]
        path = self.export(id, kind)
        return name + ("" if kind == "io" else "-" + kind) + path.suffix


def dispatch_get(handler, path):
    store = handler.desktop
    if path == "/api/v1/designs/assembly-planners":
        from .assembly_agent import planners_status
        return handler.json_response(planners_status())
    if path == "/api/v1/sets/library" or path.startswith("/api/v1/sets/library/"):
        from .official_library import official_library
        library = official_library()
        if path == "/api/v1/sets/library":
            return handler.json_response(library.listing(parse_qs(urlsplit(handler.path).query)))
        match = re.fullmatch(r"/api/v1/sets/library/([a-f0-9]{24})(?:/(download|thumbnail|preview|model.glb))?", path)
        if not match:
            raise FileNotFoundError()
        id, action = match.groups()
        if not action:
            return handler.json_response(library.public(library.record(id)))
        if action in {"download", "thumbnail"}:
            return handler.send_file(library.file(id, thumbnail=action == "thumbnail"))
        if action == "preview":
            params = parse_qs(urlsplit(handler.path).query)
            state = library.preview(id, retry=params.get("retry") == ["1"])
            return handler.json_response(state, 202 if state["status"] == "processing" else 200)
        target, report = library.preview_paths(id)
        if not report.is_file() or read(report).get("status") != "ready":
            raise FileNotFoundError()
        return handler.send_file(target)
    if path == "/api/v1/designs/scales":
        return handler.json_response(dict(default="auto", scales=[dict(ratio=s, mechanisms=list(requirements_for_scale(s)["required_mechanisms"]),
            status="experimental", recipe_available=s == "1:24", precision_supported=False, requirements=ui_policy(s)) for s in SCALES]))
    if path == "/api/v1/mods":
        return handler.json_response(read(ROOT / "data/ui/catalog.json")["mods"])
    if path in {"/api/v1/sets/vehicles", "/api/v1/sets", "/api/v1/sets/known-cars"}:
        vehicles = read(ROOT / "data/ui/catalog.json")["vehicles"]
        for v in vehicles:
            selection = selection_record(classify(dict(model=f"{v['make']} {v['model']}", body_style=v["body_style"])))
            v["recipe_available"] = selection["builder"] is not None
        return handler.json_response(dict(vehicles=vehicles) if path.endswith("vehicles") else vehicles)
    match = re.fullmatch(r"/api/v1/export/(\w+)/([a-f0-9]{32})", path)
    if match:
        return handler.send_file(store.export(match[2], match[1]), filename=store.export_filename(match[2], match[1]))
    match = re.fullmatch(r"/api/v1/designs/([a-f0-9]{32})(?:/(.*))?", path)
    if not match:
        raise FileNotFoundError()
    id, action = match.groups()
    if action is None:
        return handler.json_response(store.detail(id))
    if action == "status":
        return handler.json_response(store.state(id))
    if action == "parts":
        return handler.json_response(store.detail(id)["parts"])
    if action == "scene":
        return handler.json_response(store.scene(id))
    if action == "geometry":
        return handler.send_file(store.geometry(id))
    if action == "quality":
        return handler.json_response(store.quality(id))
    if action == "ldr":
        return handler.send_file(store.export(id, "ldr"))
    if action == "assembly-preview":
        return handler.send_file(store.artifacts(id) / "03-lego/native-ldraw.png")
    if action in {"renders/" + view for view in RENDER_VIEWS}:
        return handler.send_file(store.artifacts(id) / f"05-presentation/render_{action.removeprefix('renders/')}.png")
    if action.startswith("reference-images/"):
        view = action.split("/")[-1]
        return handler.send_file(store.reference_file(id, view))
    if action.startswith("inspection-images/"):
        return handler.send_file(store.inspection_file(id, action.removeprefix("inspection-images/")))
    raise FileNotFoundError()


def dispatch_post(handler, path):
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 0 or length > 21 * 1024 * 1024:
        raise ValueError("上传文件不能超过 20 MB。")
    raw = handler.rfile.read(length)
    payload, image = {}, None
    if path == "/api/v1/designs/from-image":
        content_type = handler.headers.get("Content-Type", "")
        message = BytesParser(policy=default).parsebytes(("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
        if not message.is_multipart():
            raise ValueError("请选择车辆照片。")
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name == "image":
                image = part.get_payload(decode=True)
            elif name == "reuse_library":
                value = part.get_payload(decode=True).decode("utf-8")
                if value not in {"true", "false"}:
                    raise ValueError("reuse_library must be true or false")
                payload[name] = value == "true"
            elif name in {"make", "model", "year", "scale", "assembly_planner", "reconstruction_backend"}:
                payload[name] = part.get_payload(decode=True).decode("utf-8")
        if not image or len(image) > 20 * 1024 * 1024:
            raise ValueError("请选择 20 MB 以内的车辆照片。")
    elif raw:
        if len(raw) > 32000 or handler.headers.get_content_type() != "application/json":
            raise ValueError("无效的 JSON 请求。")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("无效的 JSON 请求。")
    store = handler.desktop
    if path in {"/api/v1/designs", "/api/v1/designs/from-image"}:
        result = store.submit(payload, image)
        return handler.json_response(result, 200 if result.get("status") == "matched" else 201)
    if path == "/api/v1/research/physical-specifications":
        result = store.research(payload)
        if result["status"] != "ready":
            raise ValueError(result.get("error") or "未找到可用的车型尺寸。")
        return handler.json_response(physical(result["spec"], payload, audit=result.get("audit")))
    match = re.fullmatch(r"/api/v1/designs/([a-f0-9]{32})/(customize|studio-open|appearance-review|control/(\w+))", path)
    if not match:
        raise FileNotFoundError()
    id, action, control = match.groups()
    if control:
        return handler.json_response(store.control(id, control, parse_qs(urlsplit(handler.path).query).get("module", [None])[0]))
    if action == "appearance-review":
        return handler.json_response(store.appearance_review(id, payload))
    if action == "customize":
        if store.state(id)["status"] not in {"draft", "completed"}:
            raise ValueError("请等待基础设计生成完成。")
        request = read(store.directory(id) / "request.json")
        result = store.submit(dict(make=request["brand"], model=request["model"], year=request.get("year"),
            assembly_planner=request.get("assembly_planner", "legacy"),
            customization_text=payload.get("customization_text", "")), parent=id)
        return handler.json_response(dict(id=result["id"], status=result["status"], parent_design_id=id, message="改装设计已创建"), 201)
    file = store.export(id, "io")
    if not file.exists():
        raise FileNotFoundError()
    source, _ = configuration()
    studio = source / "Studio 2.0/Studio.exe"
    if not studio.is_file():
        raise ValueError("未找到 BrickLink Studio。")
    subprocess.Popen([str(studio), str(file.resolve())], cwd=studio.parent)
    return handler.json_response(dict(success=True, io_path=str(file), studio_opened=True, file_opened=False))
