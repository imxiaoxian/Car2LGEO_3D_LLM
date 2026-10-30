---
name: vehicle-io-creation
description: Create and repair vehicle IO assemblies using references, semantic modules, corpus retrieval and deterministic assembly tools. Used by the website assembly planners for local Codex and local trained models.
---

# Vehicle IO creation

The website owns tool execution, revisions, retries and artifacts. Propose one structured tool action at a time using the supplied protocol. Treat tool results and geometry evidence as authoritative. Local Codex and the trained assembly model are independent alternatives; neither certifies the other's output.

The user-selected knowledge-base IO is the detail standard. Match its recognizable appearance and represented structures, not every real-car detail. Follow `vehicle_contract.detail_standard` and required roles. Hidden seat rails, pedal hardware, independent optical reflectors, extra suspension and separate door glazing are not universal requirements. A baseline open window/frame or a standard LEGO lamp is valid. Any glass actually included still fits and belongs to its moving door. Source geometry/BOM stays out of held-out fresh planner inputs. Scope exclusions are recorded separately from repaired defects; an excluded reference finding is not an image pass.

Start with the compact `guide:/start` context resource and read relevant `guide:/tools/<name>` and `guide:/operations/<op>` signatures. These signatures come from the executor schema. Read [tool-protocol.md](references/tool-protocol.md) for detailed contracts and [repair-guide.md](references/repair-guide.md) when a check fails. Full-document scanning is unnecessary. Use the supplied corpus snapshot and held-out exclusions.

For vehicle-rich context, follow the supplied `workflow` directly. Reuse `known_evidence.parts`, `modules`, `retrievals` and `part_searches`. These contain actual prior query results, including native bounds and ports; their numbered observations need not be read again. Context-read history points to the original resource, not another observation wrapper. Read a specific skill section only to resolve a missing rule. Use `query_module(module_id)` to inspect a candidate's native bounds and BOM before grafting. After grounding interfaces, place and check the next module instead of restarting catalogue research.

New module names end in `.ldr`. A single `edit` batch can create a module with `alias:"door"`, then add its panel and glass with `parent:"@door"` and a joint with `instance:"@door"`. Aliases only exist within that batch; retain the returned real IDs for later edits. Declare joint `purpose` so motion checks can identify steering/suspension pairs. Standard part dimensions and collision checks still apply.

## Workflow

For a new part with known ports, prefer `attach` (revision, part, source_port, target, target_port).
The host adds and seats it atomically with a complete native check by default; a rejected
attachment leaves no extra part or revision. An optional local matrix sets initial roll for
keyed connectors. Use `snap` for existing subtrees. Independent BrickLayer results in `check`
are digital cross-checks; unsupported parts remain unverified, never silently skipped as passed.

Use `search_mounts` for an existing subtree whose mounting pose is uncertain. Supply revision,
moving and target roots; optionally narrow source_ports/target_ports to observed {instance,port}
pairs. It searches on copies with 24 relative axis rotations and finite shaft insertion samples,
without changing the assembly. max_axial_samples defaults to 9 (maximum 33); set 1 to keep only
native seating. All candidates share max_evaluations and respect native stops and keys.
Replay a returned build_arguments at the base revision, then check and motion.
Installation pass is separate from whole-graph pass; strict=false proposals remain unfinished
drafts. Budgets, missing native evidence and limited axial/orientation coverage cannot establish
that no solution exists. Existing root joints move with the subtree; motion is still unverified.

For a complete connected module, prefer `build` with 1..64 module/add/attach/snap or edit operations.
Use `guide:/build_operations/<op>` signatures and earlier `@aliases`; the host rolls back the entire
batch on failure and publishes one revision after strict native validation. This API is object-generic.
Vehicle role bindings, wheel layout and motion remain separate requirements. Run `preview` after
checking: it returns six actual views of the resolved graph, with current pixels attached only to
vision planners. Inspect these images before revising or exporting. `render` only exports GLB.

For a moving subtree, `articulate` derives a joint axis and pivot from a verified native
axle/hinge contact. Supply instance, source/source_port inside the subtree, target/target_port
outside, limits and purpose. The same operation works inside `build` using earlier aliases.
Keep the fixed support outside the moving subtree. Rotations use degrees; rolling spans 360.
Run `motion(samples>=9)` afterward: a joint declaration alone does not verify motion.

Axle seating has an axial interval, not a single socket centre. Snap maximizes legal overlap
and prefers the initial axial position when overlap ties. Use queried port lengths, stops and
native bounds to seed a suitable axial position in the local matrix before attach/snap.
A longer axle alone does not establish wheel clearance. Check the tyre, rim, bearing and deck
together; retain full native collision and motion checks. Digital contact does not prove axial
retention or load capacity in a physical build.

Use `axial_clearance(revision,moving,source,source_port,target,target_port)` at an already
engaged native shaft contact to diagnose both straight axial release paths. The host sweeps
the entire moving subtree continuously against all outside native colliders until the selected
port intervals disengage. `max_travel` defaults to 400 LDU (maximum 2000); `max_pairs` defaults
to 100000. Clear means this geometric path is clear within the reported collider tolerance;
blocked identifies an obstruction on this path. Missing evidence, initial interference and
incomplete budgets cannot produce a clear result. Cap flags alone do not invent stop geometry.
Other contacts, friction, deformation, alternative paths, physical retention and load remain
unverified. This read-only diagnostic does not replace check, motion or structural acceptance.

1. Inspect the semantic scene, references and existing graph. Use about 1:12 as a detail budget with automatic dimensions; preserve native standard-part dimensions. Record inferred vehicle specifications as estimates. Reuse passed images and modules. Image generation is limited to five concurrent images with a whole-batch barrier.
2. Retrieve three plausible structural candidates for each critical module. `types` filters vehicle body categories, not module kinds: do not pass `["module"]`. Omit `types` when the category is unknown. Queries search model/submodel names and tags, not inferred geometric roles. Empty results include category diagnostics; correct the search or construct alternatives from queried native parts without relaxing exclusions. Set `min_parts` and `max_parts` when browsing substantial chassis or mechanisms; the default tie order starts with small subassemblies. Compare connector interfaces, native wheel/hub origins, dimensions, geometry evidence and articulation. Part count is only a search constraint, never proof of completeness. Names are search hints, not verified semantic labels. Prefer standard structures and large smooth exterior panels; include dependencies for authorized custom parts.

   A website `module_task` is a bounded assignment with prerequisites. Build and bind an actual trial, then call `candidate(revision,module,label)` to measure and save it. Compare three distinct passing structures; recoloring, renaming or moving the same structure does not count. Construct missing alternatives when retrieval has too few suitable modules. Use `restore_candidate` to select a saved trial only while the surrounding assembly remains unchanged, then check and export again. Mechanical module acceptance is provisional: independent semantic review and final vehicle checks still apply. An interrupted attempt resumes its own trace; never borrow another planner's output.
3. Assemble the chassis, steering carriers, suspension carriers when required, then wheel groups, before exterior modules. A steering carrier must be an actual joint-bearing submodel containing the carrier role part. Front wheels descend from a steering carrier; suspended wheels also descend from a suspension carrier. Use the host's `module_task.installation_targets` to install independent new module subtrees inside protected carriers. Existing parts, transforms, joints and bindings remain read-only; adding loose parts directly to a protected carrier is rejected. Carrier checks before wheels are provisional: the final check must show actual wheel-axis steering response and suspended-wheel travel. Preserve named nested submodels and steps. A door and its moving glass belong to one moving subtree; fixed quarter windows belong to the fixed body. Keep cabin/front/rear interfaces continuous while preserving intended design creases.
4. Query actual ports and use rigid transforms and connector snapping. Account for native part origins. Preserve all contacts between each pair. Keep the lamp layers and glazing represented by the baseline; extra optical internals are optional. Unknown geometry, ports or collision data remain unverified.

   Check the neutral wheel layout against native tyre surfaces, not part origins: X runs front to rear, Y down and Z left to right in LDraw/LDU. Installed tyres must share a ground plane above which the chassis remains; same-axle wheels align longitudinally with separate left/right tracks and both axles share a centerline. Different tyre radii are allowed with a shared ground plane. The host checks this during candidate capture, module checks and final completion. Missing wheels remain pending for the complete car; missing native geometry remains unverified. A layout failure requires a new structural placement or carrier repair, not a tolerance increase or scaled part. Preserve completed modules and use an independent recovery revision when their carriers are wrong.
5. Run structural checks after each module. Repair a failing module at most three times; then save a draft and precise diagnostics. Do not hide missing evidence, delete baseline-required detail to pass, or treat disconnected assemblies as complete.
6. Check at least nine positions per joint and refine near collisions. Every pose must retain verified connections as well as clearance; an isolated moving part cannot pass merely because it has no collision. Motion also samples steering/suspension pairs and nested rolling/steering or rolling/suspension pairs. Three-joint combinations and intermediate angles remain unverified. Mechanical clearance is a digital result, not a physical durability claim.
7. Render the resolved part graph used for BOM and export. Compare six whole-car and eight detail views with vehicle-specific references and baseline. Do not claim visual acceptance from numeric metrics or file existence.
8. For a whole vehicle, read `vehicle_contract`, then `bind_module` each required module to actual submodel roots and distinct native part IDs for every role. Door panel and glass must descend from the same door joint. A tyre, rim and hub are distinct role parts. Labels and part counts do not establish semantics. Run `check`, `motion` and `module_progress` after changes. Unknown geometry, connector data, unreviewed role assignments or missing interfaces prevent vehicle completion. The host performs independent role review; a planner cannot certify its own labels.
9. Export nested IO/MPD, flat LDR, graph, standard/custom BOM, steps, dependencies and checks. Report archive integrity, digital assembly, appearance, Studio compatibility and physical buildability separately. Finish a vehicle only with current-revision export and `module_progress.completion_ready=true`. Otherwise export a clearly labelled draft with unresolved modules and diagnostics.

Reference image correctness and modelling admission are separate. `reference_gate=ready_with_repairs` permits only local rim/trim issues to transfer to mandatory `repair_tasks`. Wrong identity, body variant, door/window ownership, handedness and severe camera errors block the affected references. Preserve raw failed findings and source hashes. Do not self-close a repair: the host must bind independent geometry/render evidence to the final graph. Hidden mechanisms remain inferred. A triangle-count warning calls for instance/export optimization, never deletion of styling detail.

## Evidence

Only executed successful operations with checked results may become training examples. Preserve tool arguments, observations, source hashes, model family and duplicate-module groups. Full held-out targets and duplicate modules cannot be retrieval candidates. A fixture of two connected 3001 bricks tests tools only; it is not an accepted car example.

Checks bind the exact graph, module bindings, native geometry, connectors, colliders and material files. A revision number alone cannot certify current evidence. After a dependency or binding changes, run the checks again before using a prior pass or closing a reference repair. Missing evidence remains unverified.
