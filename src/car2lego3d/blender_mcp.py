"""GPT vision-directed Blender modelling over the upstream MCP server and addon."""
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import shutil

from .common import ROOT, digest, model_lock, stage, write_json
from .dialogue import ask_json, image_content, settings
from .gpt_reference import configuration
from .multiview import load_views
from .model_quality import assess_model, new_failures
from .vehicle import load_spec, spec_record


MODEL_TIMEOUT = 180
MCP_TIMEOUT = 600


PROMPT = """You are a senior automotive Blender modeller. Build an accurate smooth car from the
labelled whole-car and module-detail references supplied for this phase. Follow the supplied detail
standard: reconstruct features at the knowledge-base IO's detail level, not every real-car component.
Return only JSON: {"analysis": "concise visual analysis and modelling plan", "code": "complete bpy script"}.
Script runs via Blender MCP execute_blender_code in Blender 4.5. Use only bpy, math, mathutils, random.
No filesystem, network, shell, addon installation, file loading or saving. Host code handles export/render.
Clear all scene objects before building. Units are METRES; full-size coordinates. X increases from FRONT
bumper (x=0) to REAR bumper (x=length); Y left/right centred at 0; Z up, tyre ground contact z=0.
The labelled LEFT camera is at negative Y; name its wheels/door left. RIGHT is positive Y.
Use vehicle specs for length, body width excluding mirrors, height, wheelbase, tyres and axle positions.
The supplied specification snapshot takes precedence over conflicting dimensions in the image brief.
Values are model estimates; prioritize image geometry for low confidence details while retaining core size.
Roof/cabin MUST have a shaped fastback/SUV profile if shown. Avoid generic boxes and faceted toy shapes.
Create a continuous curved body using dense longitudinal cross-section lofts with shoulder, beltline,
sill, bonnet and fender surfaces. Use real wheel-arch cuts (EXACT boolean or purpose-built topology),
body depth, underbody and connected shell. Do NOT use a cube as final main body. No floating broad panels.
Build cabin/cowl/A-pillar roots, nose/fenders/bumper, and rear deck/buttresses/tail from shared
boundary coordinates. Weld continuous shell sections; allow small intentional door/bonnet gaps.
Do not bridge disconnected modules with a thick trim strip or hide them inside an opaque cabin block.
Windscreen and rear glass lower edges must seat into body/cowl/engine deck and upper edges into roof,
within 12 mm along the WHOLE edge. Name support roles cowl, header, cabin_buttress, front_fender,
rear_fender, front_fascia, rear_fascia, engine_deck as appropriate. For separate transition meshes,
add vertex groups join_<target car_role> containing ALL vertices along the actual shared boundary,
and set car_connection_region='cabin', 'front' or 'rear'. Host samples those topology edges every 10 mm.
If two disjoint edges meet the same role, use join_<role>__<edge_id> separately so interior cross-edges
are not mistaken for seams. These groups contain contact-edge vertices only, not all panel vertices.
For a closed solid, label its actual mounting edges rather than the decorative front-face perimeter.
Short interfaces are valid when completely sampled; do not stretch or subdivide them to pad measured length.
Include measured boundary groups for all three regions. Preserve intentional Countach-style creases;
use matched tangents only where the reference has a continuous smooth transition.
Use separate named objects for body, roof, pillars, four tyres/rims and the glazing, lamps,
intakes/grilles, rocker panels, door seams, handles, mirrors and spoiler represented by the baseline.
Name four tyre objects tyre_front_left, tyre_front_right, tyre_rear_left, tyre_rear_right.
Tyre cylinders' axes are Y, wheels at front_overhang and front_overhang+wheelbase, authentic arch clearances.
Use procedural meshes/curves for narrow vehicle-specific light signatures and vents, not only primitives.
Any included glazing must be fitted TO the cabin surfaces, with finite 3-5 mm thickness, smoked transmissive
Principled glass (Transmission Weight .65-.9, IOR 1.45-1.52, roughness .08-.16, alpha=1).
Use a moderately tinted base colour so the seats remain visible; do not use an opaque black plate.
Keep thin fitted perimeter gaskets and real cabin openings. Inspect opposite views for consistency.
Match lamp outlines and construction to the baseline detail standard. Where separate optical layers
are required, use a fitted recess, housing, reflector and finite-thickness lens; do not invent layers
absent from the baseline. Fit any included lens perimeter to its housing, not a floating
emissive rectangle. Preserve the reference's lamp outline, dividers and separate indicator segments.
Each opening door is ONE assembly: create an EMPTY with car_role='door_assembly', and parent its
door panel (car_role='door_panel'), window frame (car_role='door_frame'), and any included side glass
(car_role='side_window') to it while preserving their world transforms. Separate fixed quarter glass
from the moving door glass. The bottom/front/rear glass edges must meet its own frame/panel within
15 mm; parenting alone is not a physical connection. Use shared boundary coordinates for door and glass.
Set car_role='body' on the main body shell (excluding mirrors), 'roof' on roof geometry.
Set car_role='windscreen', 'quarter_window', 'rear_window' on other glass as applicable.
Avoid parenting with a stale matrix_world: update the view layer BEFORE reading parent matrices,
store each child's world matrix, assign parent and its inverse, then restore the child's world matrix.
The closed door/window assembly stays at the cabin, never above the roof. Cut window openings in
the cabin shell or build only roof and pillars; no opaque cabin volume may cover the glazing.
All closed lofts need outward face winding and positive volume. Recalculate normals before booleans.
Use exactly four separately named tyre meshes, with no tread geometry outside their intended diameter.
Do not distort the cabin to obey low-confidence door/glass estimates: infer its silhouette from the
side views, especially the cowl, roof onset, rear cabin edge and windscreen rake. Include these choices
in analysis. For angular cars retain angular surfaces; dense lofts do not imply rounding sharp creases.
Model the sharp styling identity and image colours. Good topology, smooth shading, bevels where appropriate.
Treat 250000 evaluated triangles as a performance advisory. Preserve styling within the agreed standard; use
instances and shared mesh data for repeats. Do not simplify surfaces just to meet a triangle count.
Keep booleans limited and don't subdivide Boolean results arbitrarily.
Materials use Principled BSDF Base Color/Metallic/Roughness. Emit lenses via Emission Color/Strength.
No text labels, ground plane, camera or light. The host adds a common render rig. No need to print vertices.
Keep the script compact: use reusable mesh loft/arch helpers and roughly 120-220 lines of Python.
Resolve the major silhouettes and distinct styling before fine trim; avoid lengthy speculative analysis.
"""


def modelling_prompt(standard=None):
    return PROMPT + ("\nDETAIL STANDARD (takes precedence over generic detail suggestions):\n"
        + json.dumps(standard, sort_keys=True) if standard else "")


def validate_code(code):
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name.split(".")[0] for n in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
            if any(n not in {"bpy", "math", "mathutils", "random"} for n in names):
                raise ValueError("Generated model imports outside Blender/math are unsupported")
        if isinstance(node, ast.Name) and node.id in {"open", "exec", "eval", "compile", "__import__"}:
            raise ValueError("Generated modelling code cannot perform file or dynamic-code operations")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr in {
                "save_as_mainfile", "open_mainfile", "read_factory_settings", "quit_blender"}):
            raise ValueError("Scene lifecycle is managed by the host")
    return code


def upstream():
    spec = model_lock()["blender_mcp_code"]
    source = ROOT / "external/mcp-for-blender"
    if not source.exists():
        subprocess.run(["git", "clone", spec["url"], str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", spec["commit"]], check=True)
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != spec["commit"]:
        raise ValueError("Blender MCP checkout differs from the pinned commit")
    return source


def call(tool, arguments, directory, name, port=9877):
    from dotenv import dotenv_values
    source, python = configuration()
    local = dotenv_values(ROOT / ".env")
    python = os.environ.get("CAR2LEGO_MCP_PYTHON") or local.get("CAR2LEGO_MCP_PYTHON") or str(python)
    request, response = directory / f"{name}-mcp-request.json", directory / f"{name}-mcp-response.json"
    write_json(request, dict(tool=tool, arguments=arguments))
    timeout = float(os.environ.get("CAR2LEGO_MCP_TIMEOUT")
                   or local.get("CAR2LEGO_MCP_TIMEOUT") or MCP_TIMEOUT)
    if timeout <= 0:
        raise ValueError("CAR2LEGO_MCP_TIMEOUT must be positive")
    result = subprocess.run([python, "-B", str(ROOT / "scripts/blender_mcp_worker.py"),
        "--input", str(request), "--output", str(response), "--source", str(upstream()), "--port", str(port),
        "--timeout", str(timeout)],
        capture_output=True, cwd=ROOT, timeout=timeout + 30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    (directory / f"{name}-mcp.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        details = response.read_text(encoding="utf-8") if response.is_file() else result.stderr.decode(errors="replace")
        raise RuntimeError(details[-12000:])
    data = json.loads(response.read_text(encoding="utf-8"))
    texts = [c.get("text", "") for c in data["result"]["content"] if c["type"] == "text"]
    text = "\n".join(texts)
    if text.startswith("Error") or "Error executing" in text:
        raise RuntimeError(text)
    return data


class BlenderSession:
    def __init__(self, directory, port=9877):
        self.directory, self.port = Path(directory), port
        self.process = self.log = self.gpu_lock = None

    def __enter__(self):
        from dotenv import dotenv_values
        local = dotenv_values(ROOT / ".env")
        binary = os.environ.get("CAR2LEGO_BLENDER") or local.get("CAR2LEGO_BLENDER")
        if not binary:
            source, _ = configuration()
            binary = source / "backend/storage/tool_research/blender-4.5.14-windows-x64/blender.exe"
        self.directory.mkdir(parents=True, exist_ok=True)
        config_directory = self.directory / "blender-user-config"
        config_directory.mkdir(exist_ok=True)
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                raise ValueError(f"Experiment Blender port {self.port} is already in use")
        from .gpu_lock import GPULock
        self.gpu_lock = GPULock(purpose="blender", timeout=600)
        self.gpu_lock.__enter__()
        try:
            return self._start(binary, config_directory)
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _start(self, binary, config_directory):
        self.log = (self.directory / "blender.log").open("wb")
        startup = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
        self.process = subprocess.Popen([str(binary), "--factory-startup", "--python",
            str(ROOT / "scripts/blender_bootstrap.py"), "--", str(upstream() / "addon.py"), str(self.port)],
            cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT, startupinfo=startup,
            env={**os.environ, "BLENDER_MCP_DISABLE_TELEMETRY": "true",
                 "BLENDER_USER_CONFIG": str(config_directory.resolve())})
        for _ in range(120):
            if self.process.poll() is not None:
                raise RuntimeError("Blender startup failed; inspect blender.log")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return self
            time.sleep(.5)
        raise TimeoutError("Blender MCP startup timed out")

    def execute(self, code, name):
        guard = (ROOT / "scripts/blender_mesh_guard.py").read_text(encoding="utf-8")
        boundary = (ROOT / "scripts/blender_boundary_fit.py").read_text(encoding="utf-8")
        return call("execute_blender_code", {"code": guard + "\n" + boundary + "\n" + code, "user_prompt": "Car2LEGO multiview modelling experiment"},
                    self.directory, name, self.port)

    def __exit__(self, *args):
        try:
            if self.process is not None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        finally:
            self.process = None
            if self.log is not None:
                self.log.close()
                self.log = None
            if self.gpu_lock is not None:
                self.gpu_lock.__exit__(*args)
                self.gpu_lock = None


def render_code(directory, export=True, provenance=None):
    template = (ROOT / "scripts/blender_export_render.py").read_text(encoding="utf-8")
    return ("OUTPUT_DIRECTORY = " + repr(str(Path(directory).resolve())) + "\nEXPORT = " + repr(export)
            + "\nSCENE_PROVENANCE = " + repr(provenance or {}) + "\n" + template)


def modelling_model():
    return settings(model_key="CAR2LEGO_BLENDER_MODEL", default_model="gpt-6-astra")["model"]


def reconstruct(manifest_path, directory, vehicle_spec, review=True):
    if vehicle_spec is None:
        raise ValueError("GPT Blender modelling requires a physical specification snapshot")
    directory = Path(directory).resolve()
    manifest, paths = load_views(manifest_path)
    if manifest.get("schema_version") == 2:
        from .staged_model import reconstruct as reconstruct_staged
        return reconstruct_staged(manifest_path, directory, vehicle_spec, review)
    spec = load_spec(vehicle_spec)
    from .baseline_detail import detail_standard
    standard = detail_standard(spec)
    prompt = modelling_prompt(standard)
    images = dict(paths)
    if (Path(manifest_path).parent / "reference.png").is_file():
        images["overview"] = Path(manifest_path).parent / "reference.png"
    model = modelling_model()
    inputs = dict(backend="gpt-blender", model=model, views=manifest["views"],
                  detail_standard=standard, modelling_prompt_sha256=sha256(prompt.encode()).hexdigest(),
                  manifest_sha256=digest(manifest_path), vehicle_spec=spec_record(vehicle_spec),
                  exporter_sha256=digest(ROOT / "scripts/blender_export_render.py"),
                  mesh_guard_sha256=digest(ROOT / "scripts/blender_mesh_guard.py"),
                  boundary_fit_sha256=digest(ROOT / "scripts/blender_boundary_fit.py"))
    with stage(directory, "reconstruction", inputs) as record:
        user = [{"type": "text", "text": json.dumps({"vehicle": spec, "brief": manifest.get("brief")}, ensure_ascii=False)},
                *image_content(images)]
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
        response = ask_json(messages, directory, "model", max_tokens=24000, timeout=MODEL_TIMEOUT,
                            reuse_completed=True, model=model, stream=True, reasoning_effort="medium", transient_retries=2)
        with BlenderSession(directory) as blender:
            for attempt in range(3):
                try:
                    code = validate_code(response["code"])
                    (directory / f"model-{attempt + 1}.py").write_text(code, encoding="utf-8")
                    blender.execute(code, f"model-{attempt + 1}")
                    break
                except (RuntimeError, ValueError, SyntaxError) as error:
                    if attempt == 2:
                        raise
                    messages += [{"role": "assistant", "content": json.dumps(response)},
                        {"role": "user", "content": "Execution failed. Return a corrected FULL script: " + str(error)[-7000:]}]
                    response = ask_json(messages, directory, f"repair-{attempt+1}", max_tokens=24000,
                                        timeout=MODEL_TIMEOUT, model=model, stream=True, reasoning_effort="medium", transient_retries=2)
            blender.execute(render_code(directory), "export-render")
            body_style = (manifest.get("brief") or {}).get("body_style")
            quality = assess_model(directory, spec, body_style=body_style)
            if review:
                before = directory / "before-review"
                before.mkdir(exist_ok=True)
                for path in directory.iterdir():
                    if path.suffix in {".png", ".glb", ".obj", ".mtl", ".blend"} or path.name in {"scene-metrics.json", "model-quality.json"}:
                        shutil.copyfile(path, before / path.name)
                review_prompt = prompt.replace("Clear all scene objects before building.", "Preserve the existing scene.")
                try:
                    feedback = ask_json([{"role": "system", "content": review_prompt + "\nReview the reference vs current renders. "
                        "Return JSON {analysis, code}; code is a corrective PATCH to the existing scene, not a fresh scene. "
                        "Do not clear all objects. If no concrete improvement is possible, code is an empty string."},
                        {"role": "user", "content": [{"type": "text", "text": "Vehicle specifications: " + json.dumps(spec)},
                            *image_content(images), *image_content({"CURRENT_" + v: directory / f"render-{v}.png"
                                for v in ("left", "front", "back", "three-quarter")}),
                            {"type": "text", "text": "Measured geometry diagnostics (fix failures): " + json.dumps(quality)
                             + "\nCurrent script: " + response["code"]}]}],
                        directory, "visual-review", max_tokens=18000, timeout=MODEL_TIMEOUT, model=model, stream=True,
                        reasoning_effort="medium", transient_retries=2)
                except Exception as error:
                    write_json(directory / "review-failure.json", dict(error=str(error), scene_preserved=True))
                    feedback = {"code": ""}
                if feedback.get("code", "").strip():
                    try:
                        (directory / "visual-refine.py").write_text(feedback["code"], encoding="utf-8")
                        blender.execute(validate_code(feedback["code"]), "visual-refine")
                        blender.execute(render_code(directory), "refined-export-render")
                        refined = assess_model(directory, spec, body_style=body_style)
                        regressions = new_failures(quality, refined)
                        if regressions:
                            raise ValueError("Visual patch introduced geometry failures: " + ", ".join(regressions))
                        quality = refined
                        write_json(directory / "review-selection.json", dict(selected="refined", new_geometry_failures=[]))
                    except (RuntimeError, ValueError, SyntaxError) as error:
                        # Keep the saved successful pre-review scene if a patch failed midway.
                        write_json(directory / "review-failure.json", dict(error=str(error)))
                        rejected = directory / "rejected-review"
                        rejected.mkdir(exist_ok=True)
                        for path in before.iterdir():
                            if (directory / path.name).is_file():
                                shutil.copyfile(directory / path.name, rejected / path.name)
                        for path in before.iterdir():
                            shutil.copyfile(path, directory / path.name)
                        write_json(directory / "review-selection.json", dict(selected="before-review", reason=str(error)))
                        blender.execute("import bpy\nbpy.ops.wm.open_mainfile(filepath=" + repr(str(directory / "model.blend")) + ")", "restore")
            record.update(analysis=response["analysis"], model=model,
                addon=dict(commit=model_lock()["blender_mcp_code"]["commit"], sha256=digest(upstream() / "addon.py")),
                vehicle_spec=spec_record(vehicle_spec), glb_units="metres", obj_units="mm",
                physical_measurement_verified=False, appearance_accepted=False,
                geometry_diagnostics=dict(status=quality["status"], failed_checks=quality["failed_checks"],
                                          report="model-quality.json", sha256=digest(directory / "model-quality.json")),
                outputs={p.name: digest(p) for p in directory.iterdir() if p.suffix in {".glb", ".obj", ".blend", ".png"}})
    return directory / "calibrated.glb"
