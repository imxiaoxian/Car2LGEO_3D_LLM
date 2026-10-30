"""Website-owned assembly loop with interchangeable, independently evaluated planners."""
from __future__ import annotations

import argparse
from collections import deque
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import tomllib

from jsonschema import Draft202012Validator

from .assembly_corpus import Corpus
from .assembly_context import action_progress, progress as context_progress, repeated_cycle
from .assembly_protocol import BUILD_OPERATIONS, DECISION, OPERATIONS, SCHEMAS, protocol, protocol_hash
from .assembly_prompt_codec import VERSION as PORT_ENCODING_VERSION, pack_payload
from .assembly_tools import AssemblyTools
from .common import ROOT, digest, write_json
from .job_runtime import AssemblyControlError

PLANNERS = ("codex", "local_base", "local_finetuned")


class PlannerUnavailable(RuntimeError):
    """The planner service failed before supplying an assembly decision."""


class AssemblyBudgetExceeded(AssemblyControlError):
    """Stop at a safe checkpoint without accepting a late decision."""


def skill_bundle(root=ROOT):
    folder = Path(root) / "skills/vehicle-io-creation"
    files = [folder / "SKILL.md", *sorted((folder / "references").glob("*.md"))]
    contents = {str(p.relative_to(folder)): p.read_text(encoding="utf-8") for p in files}
    return dict(files=contents, sha256=sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest())


def parse_decision(value):
    Draft202012Validator(DECISION).validate(value)
    arguments = json.loads(value["arguments_json"])
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    if value["tool"] == "finish" and arguments:
        raise ValueError("Finish accepts no arguments")
    return value["tool"], arguments


def wire_json(value):
    """One exact text format for every planner and training example."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


DECISION_TOKENS = 256
CONTEXT_VERSION = "assembly-context.v24"


def planner_context_contract(backend, *, shared_text=False):
    vehicle = getattr(backend, "vehicle_context", None)
    contract = dict(context_version=CONTEXT_VERSION, profile="compact-navigable.v1",
                    image_transport="none")
    if vehicle:
        contract.update(profile="vehicle-scoped.v3",
            vehicle_context_sha256=sha256(wire_json(vehicle).encode()).hexdigest(),
            port_encoding=PORT_ENCODING_VERSION,
            image_transport="attached_pixels" if isinstance(backend, CodexPlanner) else "text_paths_only")
    elif not shared_text and getattr(backend, "context_profile", None) == "object-scoped.v1":
        contract.update(profile="object-scoped.v1", port_encoding=PORT_ENCODING_VERSION,
            image_transport="attached_pixels" if isinstance(backend, CodexPlanner) else "text_paths_only")
    return contract


def context_guide():
    """Small navigable signatures derived from the actual executor schemas."""
    def shape(schema):
        if "const" in schema:
            return wire_json(schema["const"])
        if "enum" in schema:
            return "|".join(map(str, schema["enum"]))
        if "oneOf" in schema:
            return "operation"
        kind = schema.get("type", "value")
        if kind == "array":
            size = schema.get("maxItems")
            child = schema["items"]
            if schema.get("minItems") == size == 4 and child.get("maxItems") == 4:
                return "number[4][4]"
            return shape(child) + (f"[{size}]" if schema.get("minItems") == size else "[]")
        bound = schema.get("maxLength", schema.get("maximum"))
        suffix = f"<={bound}" if bound is not None else ""
        if "pattern" in schema:
            suffix += f"(pattern={schema['pattern']})"
        return kind + suffix

    def signature(schema):
        required = set(schema.get("required", []))
        return ",".join(f"{key}{'' if key in required else '?'}:{shape(value)}"
                        for key, value in schema["properties"].items()) or "no arguments: {}"

    return dict(start="Read needed signatures at guide:/tools/<name> and /operations/<op>. "
        "Inspect state; read semantic pointers; edit using current revision; check; repair <=3/module; "
        "motion for moving modules; preview; export; finish {}. For planning retrieve 3 candidates/module. LDU; rigid standard parts; "
        "unknown=unverified. Full rules: skill; schemas: protocol. Use remember for notes, not exact geometry.",
        workflow=[
            "Use the supplied task, signatures, known_evidence and graph first. Read a specific reference section only for an unresolved rule; do not scan the full skill in consecutive 400-character pages.",
            "Retrieve up to three suitable structures, query_module for native bounds/interfaces, or construct missing alternatives from search_parts and query_part. retrieve.types means vehicle body categories, never ['module']; omit it when unknown. Empty results are not candidates; inspect diagnostics and change the search.",
            "Use build for a connected module: batch add/module/attach/snap/articulate with observed ports and @aliases, then one complete native check and revision. @aliases exist only inside that one build call; for later calls use returned real instance IDs or inspect the graph. After a successful build, do not rebuild the module; check it, run motion(samples>=9) for moving modules, preview it, export it, then finish. Read guide:/build_operations/<op>. Preserve prerequisites; door and moving glass share a subtree. Derive joints with articulate at verified contacts; fixed support stays outside the moving subtree. Rolling joints span 360 degrees.",
            "Build chassis, steering carriers, required suspension carriers, then wheels. Use module_task.installation_targets to install a new independent module subtree inside a protected moving carrier; existing geometry and bindings remain read-only. Carrier-only checks are provisional: final motion must turn the actual wheel axes and move suspended wheels.",
            "For an existing module with uncertain mounting placement, use search_mounts(revision,moving,targets). Narrow observed source_ports/target_ports when available. It samples shaft insertion positions as well as 24 rotations; max_axial_samples defaults to 9 (1 for native seating only), with a shared evaluation budget. Replay a proposal's build_arguments at its base revision, then check and motion. Installation pass can coexist with an unfinished disconnected whole graph; strict=false proposals are drafts. Search is bounded, not proof that no mount exists.",
            "Use axial_clearance at an engaged shaft contact to diagnose straight axial release paths of its moving subtree. It continuously sweeps native colliders to interval disengagement in both directions. Clear/blocked describe geometric paths only; missing evidence and exhausted budgets remain unverified. Native cap flags alone, motion pass and blocked paths do not establish friction, physical retention or load capacity.",
            "For a moving shaft assembly use axial_motion(joint,moving,source,source_port,target,target_port,revision,samples>=9). Check the complete joint subtree and separate descendant shaft, bush and hub cuts at their observed contacts. A module can be blocked while an individual part slides out. Results cover sampled joint poses with other joints neutral, not intermediate angles, insertion order, friction or load. Preserve unresolved component paths instead of accepting only the module result.",
            "For closed-contact mechanisms use linkage_motion(driver,passive,samples>=9) with actual revolute joint IDs. The host solves passive angles within limits while preserving every original native contact and insertion, then checks collisions. Optional rolling joints sweep each solved driver pose. The separate linkage-motion.json is sampled digital evidence; it does not replace ordinary motion or vehicle completion requirements. Free passive joints, exhausted budgets and missing native evidence remain unverified.",
            "Reuse an existing subassembly with build/edit clone(source,parent?,matrix,alias?). matrix is the new parent-local placement. Descendant IDs are returned in clones[].instances and edit independently. Internal descendant joints are preserved; the source root joint is omitted because its support lies outside the copy. Mount and articulate the new root at verified native contacts, then recheck the combined assembly and motion. No source acceptance or vehicle bindings are copied.",
            "Bind actual roles with bind_module; check and motion; save a real trial with candidate. Compare three distinct passing structures, restore the selected trial, then recheck and export before finish. Repair at most three times per module; otherwise preserve a diagnostic draft.",
            "After a structural check, call preview to inspect six actual native-part views before extending or finalizing the build. Geometry or colours missing means no visual pass. A preview is a current observation, not independent appearance acceptance. Vision inputs identify ACTUAL assembly separately from REFERENCE images.",
            "Match vehicle_contract.detail_standard and required roles. Baseline-excluded real-car details are optional. Missing geometry, connectors or required interfaces remain unverified; export success is not vehicle or appearance acceptance."],
        tool_usage={"retrieve": "query searches names/tags, not geometric role labels. types contains vehicle body categories; omit it unless known. Empty results include diagnostics and do not relax held-out exclusions."},
        tools={**{name: signature(schema) for name, schema in SCHEMAS.items()}, "finish": "no arguments: {}"},
        operations={schema["properties"]["op"]["const"]: signature(schema) for schema in OPERATIONS},
        build_operations={schema["properties"]["op"]["const"]: signature(schema) for schema in BUILD_OPERATIONS})


def execution_hash():
    files = ("assembly_agent.py", "assembly_context.py", "assembly_tools.py", "assembly_graph.py",
             "assembly_catalog.py", "assembly_corpus.py", "native_ports.py", "assembly_vehicle_context.py",
             "assembly_requirements.py", "baseline_detail.py", "delivery_targets.py", "assembly_module_review.py", "assembly_evidence.py",
             "assembly_inspection.py", "assembly_search.py", "assembly_axial.py", "assembly_motion.py", "assembly_linkage.py", "assembly_materials.py", "part_library.py", "ldraw_document.py",
             "assembly_schedule.py", "assembly_candidates.py", "assembly_recovery.py", "assembly_mounts.py", "assembly_layout.py", "job_runtime.py",
             "assembly_prompt_codec.py", "assembly_reference_context.py", "bricklayer_audit.py", "assembly_presentation.py",
             "assembly_website.py", "shape_evidence.py")
    identity = {name: digest(Path(__file__).with_name(name)) for name in files}
    identity["render_assembly_preview.py"] = digest(ROOT / "scripts/render_assembly_preview.py")
    try:
        sdk_version = metadata.version("bricklayer-sdk")
    except metadata.PackageNotFoundError:
        sdk_version = None
    identity["independent_validator"] = dict(
        mode=os.getenv("CAR2LEGO_BRICKLAYER_AUDIT", "auto"), sdk_version=sdk_version)
    return sha256(wire_json(identity).encode()).hexdigest()


class CodexPlanner:
    """Local authenticated CLI proposes JSON; it never owns assembly mutations."""
    name = "codex"
    context_profile = "object-scoped.v1"

    def __init__(self, directory, *, executable=None, model=None, timeout=1200):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.executable = executable or os.getenv("CODEX_CLI_PATH") or shutil.which("codex")
        if not self.executable:
            raise ValueError("Local Codex executable is not configured")
        self.model, self.timeout = model or os.getenv("ASSEMBLY_CODEX_MODEL"), timeout

    def cli_options(self):
        # Keep provider/auth configuration while removing unrelated CLI extensions.
        options = []
        for feature in ("apps", "plugins", "memories", "shell_tool", "multi_agent", "shell_snapshot"):
            options.extend(["--disable", feature])
        options.extend(["--enable", "skip_host_skill_discovery", "-c", 'web_search="disabled"'])
        home = Path(os.getenv("CODEX_HOME", Path.home() / ".codex"))
        config = home / "config.toml"
        if config.is_file():
            servers = tomllib.loads(config.read_text(encoding="utf-8")).get("mcp_servers", {})
            for name in sorted(servers):
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    raise ValueError("Codex assembly CLI requires MCP names supported by dotted config overrides")
                options.extend(["-c", f"mcp_servers.{name}.enabled=false"])
        return options

    def propose(self, payload, step, *, timeout=None):
        timeout = self.timeout if timeout is None else min(self.timeout, timeout)
        if timeout <= 0:
            raise AssemblyBudgetExceeded("Assembly decision has no remaining time")
        folder = self.directory / f"{step:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        schema, output = folder / "decision.schema.json", folder / "decision.json"
        write_json(schema, DECISION)
        write_json(folder / "input.json", payload)
        command = [str(self.executable), "exec", "--ignore-rules", "--ephemeral",
            "--sandbox", "read-only", "--skip-git-repo-check", "--cd", str(folder),
            "--json", "--color", "never", "--output-schema", str(schema), "-o", str(output)]
        command.extend(self.cli_options())
        if self.model:
            command.extend(["--model", self.model])
        for attachment in payload.get("image_inputs", []):
            image = Path(attachment["file"])
            if digest(image) != attachment["sha256"]:
                raise ValueError("Planner image input changed")
            command.extend(["--image", str(image)])
        command.append("-")
        prompt = wire_json(payload)
        # A timeout kills the entire Windows process tree, including CLI descendants.
        with (folder / "events.jsonl").open("wb") as stdout, (folder / "stderr.log").open("wb") as stderr:
            try:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                    env={**os.environ, "PYTHONUTF8": "1"}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                raise PlannerUnavailable(f"Local Codex could not start; inspect {folder}") from exc
            try:
                process.communicate(prompt.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.kill()
                process.wait()
                raise PlannerUnavailable(f"Local Codex decision deadline exceeded; inspect {folder}") from exc
        if process.returncode or not output.is_file():
            raise PlannerUnavailable(f"Local Codex decision failed ({process.returncode}); inspect {folder}")
        value = json.loads(output.read_text(encoding="utf-8-sig"))
        parse_decision(value)
        return value


class LocalPlanner:
    """A separately served base or LoRA model using exactly the same text/tool context."""
    def __init__(self, name, *, url=None, model=None, timeout=600):
        from urllib.parse import urlsplit
        if name not in {"local_base", "local_finetuned"}:
            raise ValueError("Unknown local planner")
        prefix = "ASSEMBLY_" + name.upper()
        self.name = name
        self.url = url or os.getenv(prefix + "_URL", "")
        self.model = model or os.getenv(prefix + "_MODEL", name)
        if urlsplit(self.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Assembly model service must be explicitly configured on localhost")
        self.timeout = timeout

    def propose(self, payload, step, *, timeout=None):
        import httpx
        timeout = self.timeout if timeout is None else min(self.timeout, timeout)
        if timeout <= 0:
            raise AssemblyBudgetExceeded("Assembly decision has no remaining time")
        try:
            response = httpx.post(self.url.rstrip("/") + "/v1/chat/completions", timeout=timeout, trust_env=False,
                json=dict(model=self.model, temperature=0, max_tokens=DECISION_TOKENS,
                    messages=[dict(role="user", content=wire_json(payload))],
                    response_format=dict(type="json_schema", json_schema=dict(name="assembly_decision", strict=True, schema=DECISION))))
        except httpx.RequestError as exc:
            raise PlannerUnavailable(f"Local planner transport failed ({type(exc).__name__}): {exc}") from exc
        if response.is_error:
            try:
                error = response.json().get("error", {})
            except (ValueError, AttributeError):
                error = {}
            message = ""
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"][:2000]
                category = str(error.get("type", "request_failed"))[:128]
                message = f" ({category}): {message}"
            failure = RuntimeError if response.status_code in {400, 422} else PlannerUnavailable
            raise failure(f"Local planner HTTP {response.status_code}{message}")
        response.raise_for_status()
        value = json.loads(response.json()["choices"][0]["message"]["content"])
        parse_decision(value)
        return value


def planner(name, directory):
    return CodexPlanner(directory) if name == "codex" else LocalPlanner(name)


def selected_planner(requested=None):
    name = requested or os.getenv("ASSEMBLY_PLANNER", "legacy")
    if name == "auto":
        path = ROOT / "data/assembly-agent/winner.json"
        if not path.exists():
            raise ValueError("No accepted comparison selects a default assembly planner")
        evidence = json.loads(path.read_text(encoding="utf-8"))
        from .assembly_evaluation import validate_selection
        if not validate_selection(evidence, path.parent):
            raise ValueError("Planner selection is missing current protocol evidence")
        name = evidence["winner"]
    if name not in {*PLANNERS, "legacy"}:
        raise ValueError("Unknown assembly planner")
    return name


def planners_status():
    path = ROOT / "data/assembly-agent/winner.json"
    evidence = {}
    if path.is_file():
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    from .assembly_evaluation import validate_selection
    accepted = bool(evidence) and validate_selection(evidence, path.parent)
    return dict(default=os.getenv("ASSEMBLY_PLANNER", "legacy"), protocol_hash=protocol_hash(),
        alternatives=[dict(id=n, configured=bool(os.getenv("CODEX_CLI_PATH") or shutil.which("codex")) if n == "codex"
                           else bool(os.getenv("ASSEMBLY_" + n.upper() + "_URL"))) for n in PLANNERS],
        comparison_status="accepted" if accepted else "requires_actual_evaluation",
        winner=evidence.get("winner") if accepted else None, skill_hash=skill_bundle()["sha256"])


def context_file(path, limit=150000):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError("Semantic context exceeds limit; provide a scoped module description")
    return dict(sha256=digest(path), data=json.loads(path.read_text(encoding="utf-8")))


class AssemblyAgent:
    def __init__(self, tools, backend, mission, *, max_steps=160, max_seconds=14400, checkpoint=None,
                 directory=None, finish_check=None, finalize=True):
        if not 1 <= max_steps <= 2000 or not 1 <= max_seconds <= 86400:
            raise ValueError("Invalid agent budget")
        self.tools, self.backend, self.mission = tools, backend, mission
        tools.vehicle_contract = mission.get("vehicle_contract")
        self.max_steps, self.max_seconds = max_steps, max_seconds
        self.checkpoint = checkpoint or (lambda: None)
        tools.checkpoint = self.checkpoint
        self.directory = Path(directory) if directory else tools.directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.finish_check, self.finalize = finish_check, finalize
        self.skill = skill_bundle()
        initial = self.directory / "initial-assembly.json"
        if not initial.exists():
            write_json(initial, dict(text=tools.assembly.text(), revision=tools.assembly.revision,
                joints=tools.assembly.joints, source_sha256=tools.assembly.source_hash,
                packages=[str(p.relative_to(tools.directory)) for p in tools.catalog.packages],
                dependencies={name: sha256(data).hexdigest() for name, data in tools.dependencies().items()}))
        self.contract = dict(protocol_hash=protocol_hash(), skill_hash=self.skill["sha256"], mission=mission,
            execution_hash=execution_hash(), decision_tokens=DECISION_TOKENS,
            initial_assembly_sha256=digest(initial),
            context_version=CONTEXT_VERSION, planner_context=planner_context_contract(backend,
                shared_text=mission.get("comparison_context_profile") == "shared-text"),
            snapshot=tools.corpus.snapshot() if tools.corpus else None, exclusions=tools.exclusions)
        if getattr(backend, "vehicle_context", None):
            self.contract["vehicle_context_sha256"] = sha256(wire_json(backend.vehicle_context).encode()).hexdigest()
            self.contract["planner_context_profile"] = "vehicle-scoped.v3"
            tools.context_resources["vehicle_context"] = backend.vehicle_context
        self.input_hash = sha256(json.dumps(self.contract, sort_keys=True).encode()).hexdigest()
        tools.context_resources.update({name: item.get("data", item) if isinstance(item, dict) else item
                                        for name, item in mission.items()})
        tools.context_resources.update({"skill": self.skill["files"], "protocol": protocol(), "guide": context_guide()})
        tools.context_resources["graph"] = tools.assembly.graph
        tools.context_resources["observations"] = {}
        self.note_path = self.directory / "planner-note.json"
        tools.note_path = self.note_path

    def context(self, history, remaining):
        graph = self.tools.assembly.graph()
        previous = history[-1] if history else None
        last = None
        if previous:
            observation = previous["observation"]
            tool = previous["decision"]["tool"]
            if tool != "context":
                self.tools.context_resources["observation"] = observation
            last = dict(tool=tool, ok=observation["ok"])
            if not observation["ok"]:
                error = observation.get("error", "Failed")
                self.tools.context_resources["error"] = error
                if observation.get("diagnostic"):
                    last["diagnostic"] = observation["diagnostic"]
                if len(wire_json(error)) <= 128:
                    last["error"] = error
                else:
                    last["resource"] = "error"
            # Context pages are explicit and always delivered, even at the page limit.
            elif tool == "context" or len(wire_json(observation)) <= 400:
                last["data"] = observation.get("result")
            else:
                last["resource"] = "observation"
        request = self.tools.context_resources.get("request", {})
        task = {k: request[k] for k in ("brand", "model", "year", "goal") if k in request}
        if len(wire_json(task)) > 160:
            task = {"resource": "request"}
        navigation = dict(start="guide:/start", tools="guide:/tools/<name>",
            operations="guide:/operations/<op>", build_operations="guide:/build_operations/<op>")
        semantic = self.tools.context_resources.get("semantic", {})
        if isinstance(semantic, dict) and isinstance(semantic.get("operations"), list):
            navigation["placements"] = dict(count=len(semantic["operations"]), pointer="semantic:/operations/<index>")
        memory = context_progress(self.tools.context_resources["observations"], graph["revision"])
        payload = dict(instruction="The website executes tools. Propose one action; do not run shell commands, access files, or invoke other tools. "
            "Return {tool,arguments_json:stringified object,reason}. "
            "context(resource,pointer,offset,limit<=400); remember(text<=128). "
            "Use memory,last; read guide:/start if unread; reuse signatures. "
            "recent_reads=[resource,pointer,offset,next]; skill/protocol=full references. "
            "Export current revision before finish."
            + (" Finish the assigned module_task; the host checks this module, not the unfinished car."
                if self.finish_check else " Vehicle tasks also require module_progress completion_ready=true."
                if self.tools.vehicle_contract else ""),
            task=task, navigation=navigation,
            resources=["guide", "skill", "protocol", *self.mission, "graph", "observation", "observations", "error"],
            note=json.loads(self.note_path.read_text(encoding="utf-8"))["text"] if self.note_path.is_file() else "",
            state=dict(revision=graph["revision"], instances=len(graph["instances"])),
            memory=memory, last=last, remaining_steps=remaining)
        if self.mission.get("shape_evidence"):
            payload["shape_evidence"] = self.mission["shape_evidence"]
        preview_path = self.tools.directory / "preview.json"
        if preview_path.is_file():
            from .assembly_evidence import check_current
            actual = json.loads(preview_path.read_text(encoding="utf-8"))
            current = check_current(graph, actual)
            payload["actual_preview"] = dict(revision=actual.get("revision"), current=current,
                graph_sha256=actual.get("graph_sha256"), appearance="unverified",
                resource="actual_preview", image_transport="attached_pixels" if isinstance(self.backend, CodexPlanner) else "text_paths_only")
            self.tools.context_resources["actual_preview"] = actual
            if current and isinstance(self.backend, CodexPlanner):
                payload["image_inputs"] = [{**actual["image"], "label": "ACTUAL assembly, current revision"}]
        if self.finish_check:
            payload["module_task"] = dict(self.mission["module_task"], resource="module_task",
                finish="Export current revision, then finish; module acceptance is provisional mechanical evidence.")
            from .assembly_candidates import records
            payload["module_task"]["saved_candidates"] = [dict(
                id=row["id"], label=row["label"], status=row["measurement"]["status"], current=row["current"])
                for row in (records(self.tools, self.mission["module_task"]["id"])
                    if self.tools.vehicle_contract else [])]
        vehicle = getattr(self.backend, "vehicle_context", None)
        if vehicle:
            from .assembly_inspection import planning_evidence
            from .assembly_vehicle_context import module_vehicle_context
            read_streak = 0
            for record in reversed(history):
                if record["observation"].get("revision_after") != record["observation"].get("revision_before"):
                    break
                read_streak += 1
            payload["resources"].append("vehicle_context")
            payload["instruction"] = payload["instruction"].replace(
                "Use memory,last; read guide:/start if unread; reuse signatures.",
                "Use the inline workflow, signatures, memory and last result directly; read references only for specific unresolved rules.")
            scoped_vehicle = module_vehicle_context(vehicle, self.mission["module_task"]) if self.finish_check else vehicle
            guide = context_guide()
            specialized = {
                4: "For uncertain mounts use search_mounts on observed ports; bounded search is not proof of absence. Read guide:/workflow/4 before use.",
                5: "Use axial_clearance for geometric straight release paths; blocked paths do not prove friction, retention or load. Read guide:/workflow/5 before use.",
                6: "Use axial_motion for whole modules AND descendant shaft/bush/hub cuts at sampled joint poses. Internal parts may release independently; retain unknowns. Read guide:/workflow/6 before use.",
                7: "Closed contacts: use linkage_motion for driver/passive joints and optional rolling. Ordinary motion and vehicle gates still apply. Read guide:/workflow/7 before use.",
                8: "Clone subassemblies with independent IDs; acceptance and root joints are not copied. Recheck combined assembly and motion. Read guide:/workflow/8 before use.",
            }
            payload.update(vehicle=scoped_vehicle,
                tool_signatures=guide["tools"], operation_signatures=guide["operations"],
                build_operation_signatures=guide["build_operations"],
                workflow=[specialized.get(i, item) for i, item in enumerate(guide["workflow"])], tool_usage=guide["tool_usage"],
                known_evidence=planning_evidence(self.tools.context_resources["observations"], graph["revision"]),
                assembly_guidance="Follow workflow directly; full skill/protocol resources resolve specific uncertainties. "
                    "Reuse known_evidence parts, modules, retrievals and part_searches; do not re-read numbered observation pages for these results. "
                    "Use query_module to compare retrieved candidates before grafting. Module names end .ldr. "
                    "Batch module and part creation using alias and @alias parent/instance references within one edit. "
                    "Use known_evidence.diagnostics for collision pairs and seating proposals; check its revision/current flag. "
                    "context_pages retain recent read snapshots with source revision; full reports remain addressable. "
                    "Joint rotations use degrees: rolling needs a full 360-degree span, not +/-pi. "
                    "Build and check the next grounded module after inspecting its interfaces; repeated planning without placement is not progress.",
                consecutive_read_only_decisions=read_streak,
                image_inputs=[scoped_vehicle["contact_sheet"], *payload.get("image_inputs", [])],
                recent_history=[self.history_summary(record) for record in list(history)[-4:]],
                graph_summary=dict(bom=self.tools.assembly.bom(),
                    instances=graph["instances"][-100:], joints=graph["joints"],
                    omitted_instances=max(0, len(graph["instances"])-100)))
            payload["action_progress"] = action_progress(self.tools.context_resources["observations"], graph["revision"])
            for record in reversed(history):
                if record["decision"]["tool"] == "view_reference" and record["observation"]["ok"]:
                    selected = record["observation"]["result"]
                    payload["image_inputs"].append({key: selected[key] for key in ("file", "sha256")})
                    break
            return pack_payload(payload)
        if self.contract["planner_context"]["profile"] == "object-scoped.v1":
            from .assembly_inspection import planning_evidence
            guide = context_guide()
            payload["instruction"] = payload["instruction"].replace(
                "Use memory,last; read guide:/start if unread; reuse signatures.",
                "Use the inline task, signatures, known_evidence and action_progress directly. "
                "Query native parts before choosing connectors; retain prior evidence and actual IDs. "
                "Read references only for unresolved rules. Repeated reading is not assembly progress.")
            if len(wire_json(request)) <= 8192:
                payload["task"] = request
            payload.update(tool_signatures=guide["tools"], operation_signatures=guide["operations"],
                build_operation_signatures=guide["build_operations"],
                known_evidence=planning_evidence(self.tools.context_resources["observations"], graph["revision"]),
                action_progress=action_progress(self.tools.context_resources["observations"], graph["revision"]),
                graph_summary=dict(bom=self.tools.assembly.bom(), instances=graph["instances"][-100:],
                    joints=graph["joints"], omitted_instances=max(0, len(graph["instances"]) - 100)),
                assembly_guidance="Build a grounded connected subassembly with observed native connectors. "
                    "graph_summary.joints retains all current joint IDs, axes, pivots and limits. "
                    "Use known_evidence.diagnostics.module_task and linkage_motion for remaining issues and failed contacts; "
                    "revision matches alone do not certify native evidence freshness. "
                    "Use build to combine module/add/attach/snap operations and @aliases in one atomic revision. "
                    "@aliases exist only within that build call; use the returned real instance IDs in later calls, "
                    "and inspect the graph when an ID is missing. If build succeeded and action_progress lists its "
                    "module/parts, do not recreate it. For a moving module, use articulate with a verified "
                    "native source/target contact to derive the joint axis/origin, then motion(samples>=9). "
                    "The moving subtree contains the rotating parts; its support stays outside. "
                    "Rolling angles use degrees and span 360. Run check, preview, export and finish. "
                    "Module names must end in .ldr; use a filename such as mounted_native_wheel.ldr. "
                    "Strict builds validate the complete graph; unfinished drafts must explicitly set strict=false. "
                    "After building, check, preview and inspect actual images, repair observed defects, export, "
                    "then explicitly finish. Successful export does not certify appearance or physical buildability.")
            return pack_payload(payload)
        return payload

    @staticmethod
    def history_summary(record):
        observation = record["observation"]
        if record["decision"]["tool"] == "context" and observation["ok"]:
            page = observation["result"]
            location = dict(ok=True, **{key: page[key] for key in
                ("resource", "pointer", "offset", "next_offset")})
        else:
            location = dict(ok=observation["ok"], resource="observations",
                pointer="/" + str(record.get("step")), full_result_requires_context=True)
        return dict(step=record.get("step"), decision=record["decision"], observation=location)

    def run(self, *, retry=False):
        path = self.directory / "agent.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if previous and (previous["input_hash"] != self.input_hash or previous["planner"] != self.backend.name):
            raise ValueError("Cannot resume assembly with changed inputs or planner")
        if previous.get("status") in {"draft", "failed"} and not retry:
            return previous
        started = time.monotonic()
        elapsed = previous.get("elapsed_seconds", 0)

        def remaining_seconds():
            return self.max_seconds - elapsed - (time.monotonic() - started)

        def budget_checkpoint():
            self.checkpoint()
            if remaining_seconds() <= 0:
                raise AssemblyBudgetExceeded("Assembly time budget exhausted")

        state = dict(schema="assembly-agent.v1", planner=self.backend.name, input_hash=self.input_hash,
            contract=self.contract, status="running", steps=previous.get("steps", 0),
            consecutive_failures=0 if retry else previous.get("consecutive_failures", 0), elapsed_seconds=elapsed,
            delivery_status="draft", appearance="unverified", studio="unverified", physical="unverified")
        trace = self.directory / "agent-decisions.jsonl"
        history = deque(maxlen=12)
        exported_revision = None
        if trace.exists():
            with trace.open(encoding="utf-8") as stream:
                for row in stream:
                    if not row.strip():
                        continue
                    record = json.loads(row)
                    history.append(record)
                    self.tools.context_resources["observations"][str(record["step"])] = record["observation"]
                    if record["decision"]["tool"] != "context":
                        self.tools.context_resources["observation"] = record["observation"]
                    if record["decision"].get("tool") == "export" and record["observation"].get("ok"):
                        exported_revision = record["observation"].get("revision_after")
        write_json(path, state)
        reason = "tool_budget_exhausted"
        previous_checkpoint = self.tools.checkpoint
        self.tools.checkpoint = budget_checkpoint
        try:
            while state["steps"] < self.max_steps:
                budget_checkpoint()
                payload = self.context(history, self.max_steps-state["steps"])
                step = state["steps"]
                input_path = self.directory / "inputs" / f"{step:04d}.json"
                write_json(input_path, payload)
                planner_failure = None
                try:
                    budget_checkpoint()
                    if isinstance(self.backend, (CodexPlanner, LocalPlanner)):
                        decision = self.backend.propose(payload, step, timeout=remaining_seconds())
                    else:
                        decision = self.backend.propose(payload, step)
                    tool, arguments = parse_decision(decision)
                except AssemblyBudgetExceeded:
                    raise
                except PlannerUnavailable as exc:
                    planner_failure = exc
                    decision = dict(tool="planner_error", reason=str(exc))
                    observation = dict(ok=False, error=f"{type(exc).__name__}: {exc}", retryable=True,
                        failure_kind="planner_unavailable")
                    tool = "planner_error"
                except Exception as exc:
                    decision = dict(tool="planner_error", reason=str(exc))
                    observation = dict(ok=False, error=f"{type(exc).__name__}: {exc}")
                    tool = "planner_error"
                budget_checkpoint()
                if tool == "finish":
                    exported_before_finish = exported_revision is not None and exported_revision == self.tools.assembly.revision
                    observation = dict(ok=exported_before_finish, result=dict(requested_finish=True))
                    if not exported_before_finish:
                        observation["error"] = "Export successfully before requesting finish"
                    elif self.finish_check:
                        observation["result"]["module_task"] = self.finish_check()
                        if observation["result"]["module_task"].get("status") != "pass":
                            observation.update(ok=False, error="Assigned module is incomplete; inspect module_task result")
                    elif self.tools.vehicle_contract:
                        modules = self.tools.call("module_progress", {})
                        if modules.get("result", {}).get("structural_ready") and not modules["result"].get("completion_ready"):
                            try:
                                self.tools.review_vehicle_modules()
                                modules = self.tools.call("module_progress", {})
                            except AssemblyControlError:
                                raise
                            except Exception as error:
                                observation["result"]["independent_review_error"] = str(error)
                        observation["result"]["module_progress"] = modules.get("result")
                        if not modules.get("ok") or not modules["result"].get("completion_ready"):
                            observation.update(ok=False, error="Vehicle is incomplete or unverified; repair required modules or export a draft")
                elif tool != "planner_error":
                    observation = self.tools.call(tool, arguments)
                    if tool == "module_progress" and observation.get("ok") and self.finish_check:
                        observation["result"]["module_task"] = self.finish_check()
                if tool == "export" and observation.get("ok"):
                    exported_revision = observation.get("revision_after")
                state["steps"] += 1
                if planner_failure is None:
                    state["consecutive_failures"] = 0 if observation["ok"] else state["consecutive_failures"] + 1
                record = dict(step=step, planner=self.backend.name, input_hash=self.input_hash,
                    input_file=str(input_path.relative_to(self.directory)), input_sha256=digest(input_path),
                    input_text=wire_json(payload), decision_text=wire_json(decision),
                    decision=decision, observation=observation)
                with trace.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                history.append(record)
                self.tools.context_resources["observations"][str(step)] = observation
                state["elapsed_seconds"] = elapsed + time.monotonic()-started
                write_json(path, state)
                budget_checkpoint()
                if planner_failure is not None:
                    raise planner_failure
                if tool == "finish" and observation["ok"]:
                    reason = "planner_finished"
                    break
                if state["consecutive_failures"] >= 3:
                    reason = "repair_budget_exhausted"
                    break
                stalled = repeated_cycle(history, self.tools.assembly.revision)
                if stalled:
                    state["stall"] = stalled
                    reason = "repeated_tool_cycle"
                    break
            budget_checkpoint()
            if not self.finalize:
                state.update(status="module_checked" if reason == "planner_finished" else "draft", stop_reason=reason,
                    module_task=self.finish_check() if self.finish_check else None)
                budget_checkpoint()
                state["elapsed_seconds"] = elapsed + time.monotonic() - started
                write_json(path, state)
                return state
            check = self.tools.call("check", {})
            budget_checkpoint()
            motion = self.tools.call("motion", {"samples": 9})
            budget_checkpoint()
            rendered = self.tools.call("render", {})
            budget_checkpoint()
            exported = self.tools.call("export", {})
            budget_checkpoint()
            modules = self.tools.call("module_progress", {})
            budget_checkpoint()
            state.update(status="draft" if exported["ok"] else "failed", stop_reason=reason,
                checks=check, motion=motion, render=rendered, export=exported,
                digital_status=check.get("result", {}).get("status", "unverified"))
            state["module_progress"] = modules.get("result")
        except AssemblyBudgetExceeded:
            state.update(status="draft", stop_reason="time_budget_exhausted")
        except PlannerUnavailable:
            state.update(status="interrupted", stop_reason="planner_unavailable",
                elapsed_seconds=elapsed + time.monotonic() - started)
            write_json(path, state)
            raise
        except BaseException:
            state.update(status="interrupted", stop_reason="checkpoint_or_execution_interrupted")
            write_json(path, state)
            raise
        finally:
            self.tools.checkpoint = previous_checkpoint
        state["elapsed_seconds"] = elapsed + time.monotonic()-started
        write_json(path, state)
        return state


def run_from_paths(*, output, request, semantic, specifications, references, backend, snapshot=None,
                   corpus_root=None, native_root=None, exclude_models=(), exclude_families=(),
                   exclude_hashes=(), max_steps=160, checkpoint=None,
                   publish_artifacts=None, retry=False):
    corpus = Corpus(corpus_root or ROOT / "data/assembly-corpus", snapshot=snapshot)
    if not snapshot and (corpus.directory / "progress.json").is_file():
        progress = json.loads((corpus.directory / "progress.json").read_text(encoding="utf-8"))
        if progress.get("status") == "running":
            corpus.db.close()
            raise ValueError("Corpus indexing is running; select a completed frozen snapshot")
    if not snapshot:
        frozen = corpus.freeze()
        corpus.db.close()
        corpus = Corpus(corpus.directory, snapshot=frozen["snapshot"])
    mission = {name: context_file(path) for name, path in dict(request=request, semantic=semantic,
               specifications=specifications, references=references).items()}
    tools = AssemblyTools(output, native_root or Path(os.getenv("LDRAW_LIBRARY_PATH", "D:/lego/Studio 2.0/ldraw")),
                          corpus, exclude_models=exclude_models, exclude_families=exclude_families,
                          exclude_hashes=exclude_hashes)
    try:
        result = AssemblyAgent(tools, planner(backend, Path(output) / "planner"), mission,
                               max_steps=max_steps, checkpoint=checkpoint).run(retry=retry)
        if publish_artifacts and result.get("export", {}).get("ok"):
            from .assembly_desktop import publish
            publish(tools, publish_artifacts)
        return result
    finally:
        corpus.db.close()


def main():
    parser = argparse.ArgumentParser()
    for name in ("request", "semantic", "specifications", "references", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--planner", choices=PLANNERS, required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--max-steps", type=int, default=160)
    parser.add_argument("--retry", action="store_true", help="Continue an existing draft with additional step budget")
    parser.add_argument("--publish-artifacts", type=Path)
    args = parser.parse_args()
    result = run_from_paths(output=args.output, request=args.request, semantic=args.semantic,
        specifications=args.specifications, references=args.references, backend=args.planner,
        snapshot=args.snapshot, max_steps=args.max_steps, publish_artifacts=args.publish_artifacts, retry=args.retry)
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
