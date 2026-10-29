"""Read native dimensions and retrieved module geometry before changing an assembly."""
import numpy as np

from .assembly_catalog import NativeCatalog
from .assembly_graph import Assembly


def part_bounds(catalog, name):
    triangles, _ = catalog.library.triangles(name)
    points = triangles.reshape(-1, 3)
    if not len(points) or not np.isfinite(points).all():
        raise ValueError("Part has no finite surface geometry")
    return dict(unit="LDU", bounds=[points.min(axis=0).tolist(), points.max(axis=0).tolist()],
        dimensions=np.ptp(points, axis=0).tolist(), triangles=len(triangles))


def inspect_module(tools, identity):
    if tools.corpus is None:
        raise ValueError("Module corpus not configured")
    item = tools.corpus.module(identity)
    if not tools.corpus.allowed(item, tools.exclusions):
        raise ValueError("Held-out family or duplicate module cannot be inspected")
    meta = item["metadata"]
    assembly = Assembly.read(tools.corpus.directory / meta["file"])
    catalog = NativeCatalog(tools.catalog_root, [tools.corpus.directory / meta["package"]])
    graph = assembly.graph()
    nodes = [node for node in graph["instances"] if node["kind"] == "part"]
    bounds, missing = [], []
    for node in nodes:
        try:
            geometry = part_bounds(catalog, node["part"])
            low, high = np.asarray(geometry["bounds"])
            corners = np.asarray([[x, y, z] for x in (low[0], high[0])
                for y in (low[1], high[1]) for z in (low[2], high[2])])
            matrix = np.asarray(node["world"])
            bounds.extend(corners @ matrix[:3, :3].T + matrix[:3, 3])
        except (ValueError, OSError) as error:
            missing.append(dict(instance=node["id"], part=node["part"], error=str(error)))
    resource = "module:" + identity
    tools.context_resources[resource] = graph
    points = np.asarray(bounds)
    return dict(module_id=identity, module_hash=item["hash"], source_model=item["model_id"],
        source_check=item["check_status"], part_count=len(nodes), bom=assembly.bom(),
        bounds_ldu=[points.min(axis=0).tolist(), points.max(axis=0).tolist()] if len(points) else None,
        bounds_method="conservative union of transformed native part bounds",
        missing=missing, resource=resource, graph_pointer="/instances",
        semantic_tag_status="unverified", connector_validation="check_required_after_graft")


def planning_evidence(observations, revision=None):
    """Keep useful observations addressable across the stateless CLI decisions."""
    parts, modules, searches, part_searches, diagnostics = {}, {}, {}, {}, {}
    pages, mount_searches = {}, {}
    for step, observation in observations.items():
        if not observation.get("ok"):
            continue
        tool, result = observation.get("tool"), observation.get("result", {})
        if tool == "query_part":
            parts[result["part"]] = dict(step=step, **result)
        elif tool == "query_module":
            modules[result["module_id"]] = dict(step=step, **result)
        elif tool == "retrieve":
            searches[step] = result
        elif tool == "search_parts":
            part_searches[step] = result
        elif tool == "search_mounts":
            mount_searches[step] = dict(step=step, revision=result["revision"],
                revision_matches=revision == result["revision"] if revision is not None else None,
                evidence_current="recheck_native_dependencies_before_replay",
                moving=result["moving"], targets=result["targets"], status=result["status"],
                stop_reason=result["stop_reason"], truncated=result["truncated"],
                statistics=result["statistics"], proposals=result["proposals"][:3],
                proposals_omitted=max(0, len(result["proposals"])-3),
                full_resource="observations", pointer="/" + str(step), summary_only=True)
            if len(mount_searches) > 3:
                mount_searches.pop(next(iter(mount_searches)))
        elif tool == "context" and result.get("resource") not in {None, "guide"}:
            key = (result["resource"], result["pointer"], result["offset"])
            pages.pop(key, None)
            pages[key] = dict(step=step, revision=observation.get("revision_after"), **result)
            if len(pages) > 6:
                pages.pop(next(iter(pages)))
        elif tool == "check":
            suggestions = []
            for pair in result.get("shaft_diagnostics", {}).get("candidates", []):
                options = pair.get("seating_options", [])
                if options and options[0]["moving_parts"] <= 8 and len(suggestions) < 3:
                    option = options[0]
                    suggestions.append(dict(source=option["source"], target=option["target"],
                        source_port=option["source_port"], target_port=option["target_port"],
                        delta_mm=option["delta_mm"], status=option["status"],
                        edit_arguments=dict(revision=result["revision"], operations=option["operations"])))
            collisions = result.get("collisions", [])
            unknown = result.get("unknown", [])
            diagnostics["check"] = dict(step=step, revision=result["revision"], status=result["status"],
                current=revision == result["revision"] if revision is not None else None,
                gates=result.get("gates", {}), components=len(result.get("components", [])),
                collisions=len(collisions), collision_pairs=collisions[:12],
                collision_pairs_omitted=max(0, len(collisions)-12),
                collision_limit_reached=result.get("collision_limit_reached", False),
                unknown=len(unknown), unknown_items=unknown[:8], unknown_items_omitted=max(0, len(unknown)-8),
                bricklayer={key: result.get("bricklayer", {}).get(key) for key in
                    ("status", "complete", "collision_count", "floating_count", "component_count", "reason")},
                seating_suggestions=suggestions, full_resource="observations", pointer="/" + str(step),
                summary_only=True, guidance="Proposals preserve connected parts but are not verified repairs. "
                    "Collision pairs retain the checker evidence; no penetration depth is inferred. "
                    "Do not move protected modules. Check current revision, collisions and full motion after editing.")
        elif tool == "motion":
            diagnostics["motion"] = dict(step=step, revision=result["revision"], status=result["status"],
                current=revision == result["revision"] if revision is not None else None,
                mechanism_response=result.get("mechanism_response", {}),
                combined_joint_states=result.get("combined_joint_states"),
                joints=[dict(instance=row["instance"], limits=row.get("limits"), unit=row.get("unit"),
                    range_issues=row.get("range_issues", []),
                    failing_poses=[p["value"] for p in row["poses"] if p["status"] != "pass"])
                    for row in result["joints"]], full_resource="observations", pointer="/" + str(step), summary_only=True)
    return dict(parts=parts, modules=modules, retrievals=searches, part_searches=part_searches,
                diagnostics=diagnostics, mount_searches=mount_searches, context_pages=list(pages.values()))
