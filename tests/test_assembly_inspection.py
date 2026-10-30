import numpy as np

from car2lego3d.assembly_inspection import part_bounds, planning_evidence


def test_bounds_use_the_native_origin_instead_of_recentering():
    class Library:
        def triangles(self, _):
            return np.asarray([[[10, -4, 0], [20, -4, 0], [10, 8, 3]]]), None
    class Catalog:
        library = Library()
    result = part_bounds(Catalog(), "test.dat")
    assert result["bounds"] == [[10, -4, 0], [20, 8, 3]]
    assert result["dimensions"] == [10, 12, 3]


def test_planner_memory_retains_old_parts_and_full_ports_without_recursive_input():
    port = dict(id="exact-port", position=[10, 20, 30], direction=[1, 0, 0])
    observations = {"0": dict(tool="query_part", ok=True, result=dict(part="hub.dat", ports=[port])),
        "1": dict(tool="retrieve", ok=True, result=dict(modules=[dict(id="candidate")])),
        "2": dict(tool="context", ok=True, result=dict(text="unrelated")),
        "3": dict(tool="query_part", ok=False, result={})}
    result = planning_evidence(observations)
    assert result["parts"]["hub.dat"]["ports"] == [port]
    assert result["retrievals"]["1"]["modules"][0]["id"] == "candidate"
    assert "unrelated" not in str(result)


def test_planner_gets_actionable_check_summary_and_full_report_pointer():
    operation = dict(op="transform", instance="root/rim", matrix=np.eye(4).tolist())
    report = dict(revision=12, status="fail", components=[["root/shaft"], ["root/rim"]],
        collisions=[], unknown=[], gates=dict(connections="fail"),
        shaft_diagnostics=dict(candidates=[dict(seating_options=[dict(source="root/rim", source_port="socket",
            target="root/shaft", target_port="axle", moving_parts=1, delta_mm=[0, 0, .4],
            operations=[operation], status="proposal_requires_collision_and_motion_checks")])]))
    observation = dict(tool="check", ok=True, result=report)
    result = planning_evidence({"27": observation})["diagnostics"]["check"]
    assert result["status"] == "fail" and result["components"] == 2
    assert result["summary_only"] and result["pointer"] == "/27"
    assert result["seating_suggestions"][0]["edit_arguments"] == dict(revision=12, operations=[operation])
    assert observation["result"] == report


def test_planner_motion_summary_exposes_units_and_does_not_hide_failed_poses():
    report = dict(revision=13, status="fail", joints=[dict(instance="root/wheel", limits=[-3.14, 3.14],
        unit="degrees", range_issues=["rolling_requires_360_degrees"],
        poses=[dict(value=0, status="pass"), dict(value=3.14, status="fail")])])
    result = planning_evidence({"28": dict(tool="motion", ok=True, result=report)})["diagnostics"]["motion"]
    assert result["status"] == "fail" and result["pointer"] == "/28"
    assert result["joints"][0]["range_issues"] == ["rolling_requires_360_degrees"]
    assert result["joints"][0]["failing_poses"] == [3.14]


def test_planner_motion_summary_preserves_missing_wheel_response():
    response = {"steering": dict(status="fail", wheels=[
        dict(module="wheel_front_left", status="fail", reason="wheel_not_installed")])}
    report = dict(revision=52, status="fail", joints=[dict(instance="root/carrier",
        limits=[-25, 25], unit="degrees", poses=[dict(value=0, status="pass")])],
        mechanism_response=response, combined_joint_states="not_applicable")
    motion = planning_evidence({"67": dict(tool="motion", ok=True, result=report)},
        revision=52)["diagnostics"]["motion"]
    assert motion["current"] and motion["status"] == "fail"
    assert motion["joints"][0]["failing_poses"] == []
    assert motion["mechanism_response"] == response
    assert motion["combined_joint_states"] == "not_applicable"
    assert not planning_evidence({"67": dict(tool="motion", ok=True, result=report)},
        revision=53)["diagnostics"]["motion"]["current"]


def test_motion_failure_details_keep_real_indices_pairs_and_report_pointers_after_reads():
    from copy import deepcopy

    collision = dict(a="root/tie", b="root/floor", evidence="native-collider-OBB")
    failed = dict(value=30, status="fail", positions={"root/driver": 30},
        collisions=[collision], connectivity="fail", components=[["root/tie"], ["root/floor"]],
        unknown=2, invalid=["root/bad"])
    poses = [dict(value=0, status="pass"), failed,
             dict(value=45, status="pass"), dict(failed, value=60)]
    report = dict(revision=18, status="fail", joints=[dict(instance="root/driver", poses=poses)],
        combined=[dict(instances=["root/driver", "root/wheel"], poses=poses)])
    observations = {"41": dict(tool="motion", ok=True, result=report),
        "42": dict(tool="context", ok=True, revision_after=18, result=dict(
            resource="observations", pointer="/41/result/joints/0/poses/1", offset=0,
            total=20, next_offset=None, text="failure details"))}
    original = deepcopy(observations)
    summary = planning_evidence(observations, revision=18)["diagnostics"]["motion"]
    joint = summary["joints"][0]
    assert summary["current"] and joint["failing_poses"] == [30, 60]
    assert joint["pose_count"] == 4 and joint["pose_failures_omitted"] == 0
    assert [pose["index"] for pose in joint["pose_failures"]] == [1, 3]
    detail = joint["pose_failures"][0]
    assert detail["pointer"] == "/41/result/joints/0/poses/1"
    assert detail["positions"] == failed["positions"] and detail["value"] == 30
    assert detail["status"] == "fail" and detail["connectivity"] == "fail"
    assert detail["component_count"] == 2 and detail["unknown"] == 2
    assert detail["invalid"] == ["root/bad"] and detail["invalid_omitted"] == 0
    assert detail["collision_pairs"] == [collision] and detail["collision_pairs_omitted"] == 0
    combined = summary["combined"][0]
    assert combined["instances"] == report["combined"][0]["instances"]
    assert combined["pose_failures"][1]["pointer"] == "/41/result/combined/0/poses/3"
    assert not planning_evidence(observations, revision=19)["diagnostics"]["motion"]["current"]
    assert planning_evidence(observations)["diagnostics"]["motion"]["current"] is None
    assert observations == original


def test_motion_failure_details_bound_poses_pairs_and_invalid_items():
    pairs = [dict(a=f"root/part/{i}", b="root/floor") for i in range(15)]
    poses = [dict(value=i, status="unverified", collisions=pairs, unknown=1,
                  invalid=[f"root/bad/{j}" for j in range(14)]) for i in range(16)]
    report = dict(revision=1, status="unverified", joints=[dict(instance="root/driver", poses=poses)])
    summary = planning_evidence({"9": dict(tool="motion", ok=True, result=report)})["diagnostics"]["motion"]
    joint = summary["joints"][0]
    assert joint["failing_poses"] == list(range(16))
    assert joint["pose_count"] == 16 and joint["pose_failures_omitted"] == 4
    assert len(joint["pose_failures"]) == 12
    detail = joint["pose_failures"][-1]
    assert detail["index"] == 11 and detail["pointer"] == "/9/result/joints/0/poses/11"
    assert detail["collision_pairs"] == pairs[:12] and detail["collision_pairs_omitted"] == 3
    assert len(detail["invalid"]) == 12 and detail["invalid_omitted"] == 2
    assert detail["component_count"] is None and detail["connectivity"] is None
    assert summary["combined"] == []


def test_module_task_retains_bounded_failed_selection_and_first_placement_evidence():
    from copy import deepcopy

    names = [f"part{i}.dat" for i in range(15)]
    selection = dict(status="fail", missing_prior_selection=names,
        parts={name: dict(status="fail", search=dict(step=8, query="Brick 2 x 2"),
                         query_step=9, added_step=4) for name in names},
        scope="Observed description search and native query before placement")
    observations = {"44": dict(tool="module_progress", ok=True, revision_after=18,
        result=dict(module_task=dict(status="fail", selection=selection)))}
    original = deepcopy(observations)
    task = planning_evidence(observations, revision=18)["diagnostics"]["module_task"]
    retained = task["selection"]
    assert task["revision_matches"] and task["pointer"] == "/44/result/module_task"
    assert retained["status"] == "fail" and retained["scope"] == selection["scope"]
    assert retained["missing_prior_selection"] == names[:12]
    assert retained["missing_prior_selection_omitted"] == 3
    assert retained["parts"] == {name: selection["parts"][name] for name in names[:12]}
    assert retained["parts"][names[0]]["added_step"] < retained["parts"][names[0]]["query_step"]
    assert observations == original


def test_empty_search_diagnostics_and_native_part_searches_survive_later_reads():
    empty = dict(modules=[], filters=dict(types=["module"]), diagnostics=dict(
        reason="unknown_vehicle_types", available_types=["sports_car"], filters_relaxed=False))
    search = dict(query="wheel", parts=[dict(part="rim.dat", description="Wheel rim")],
                  geometry_and_connection_status="query_part_required")
    observations = {"0": dict(tool="retrieve", ok=True, result=empty),
        "1": dict(tool="search_parts", ok=True, result=search),
        "2": dict(tool="context", ok=True, result=dict(text="read wrapper")),
        "3": dict(tool="search_parts", ok=False, result=dict(parts=["invalid"]))}
    retained = planning_evidence(observations)
    assert retained["retrievals"]["0"] == empty
    assert retained["part_searches"] == {"1": search}
    assert retained["parts"] == {}  # Search hints are not queried geometry/port evidence.
    assert "read wrapper" not in str(retained)


def test_collision_pairs_survive_intervening_context_reads_and_remain_revision_bound():
    from copy import deepcopy

    pairs = [dict(a=f"root/housing/{i}", b="root/rack", status="fail", evidence="native-collider-OBB")
             for i in range(15)]
    unknown = [dict(instance=f"root/unknown/{i}", reason="missing_geometry") for i in range(10)]
    report = dict(revision=38, status="fail", collisions=pairs, collision_limit_reached=True,
                  unknown=unknown, components=[["root/rack"]])
    observations = {"33": dict(tool="check", ok=True, revision_after=38, result=report)}
    for i in range(3):
        observations[str(34+i)] = dict(tool="context", ok=True, revision_after=38,
            result=dict(resource="observations", pointer=f"/33/result/collisions/{i}", offset=0,
                        next_offset=None, total=100, text=str(pairs[i])))
    original = deepcopy(observations)
    retained = planning_evidence(observations, revision=38)
    check = retained["diagnostics"]["check"]
    assert check["current"] and check["collisions"] == 15
    assert check["collision_pairs"] == pairs[:12] and check["collision_pairs_omitted"] == 3
    assert check["collision_limit_reached"]
    assert check["unknown_items"] == unknown[:8] and check["unknown_items_omitted"] == 2
    assert retained["context_pages"][0]["text"] == str(pairs[0])
    assert not planning_evidence(observations, revision=39)["diagnostics"]["check"]["current"]
    assert observations == original


def test_context_snapshots_are_bounded_deduplicated_and_keep_read_provenance():
    observations = {}
    for i in range(10):
        observations[str(i)] = dict(tool="context", ok=True, revision_after=i,
            result=dict(resource="graph", pointer=f"/instances/{i}", offset=0, next_offset=400,
                        total=800, text="x" * 400))
    observations["10"] = dict(tool="context", ok=True, revision_after=11,
        result=dict(resource="graph", pointer="/instances/5", offset=0, next_offset=None,
                    total=5, text="fresh"))
    observations["11"] = dict(tool="context", ok=False, result=dict(text="not evidence"))
    retained = planning_evidence(observations)["context_pages"]
    assert len(retained) == 6 and sum(len(page["text"]) for page in retained) <= 2400
    assert [page["step"] for page in retained] == ["4", "6", "7", "8", "9", "10"]
    assert retained[-1]["revision"] == 11 and retained[-1]["text"] == "fresh"


def test_mount_proposals_are_bounded_and_keep_original_observation_and_base_revision():
    result = dict(revision=8, moving="root/wheel", targets=["root/carrier"], status="proposals",
        stop_reason="proposal_limit", truncated=True, statistics=dict(evaluations=6),
        proposals=[dict(id=str(i), installation="pass") for i in range(6)])
    observations = {str(i): dict(tool="search_mounts", ok=True, result=result) for i in range(5)}
    retained = planning_evidence(observations, revision=8)["mount_searches"]
    assert list(retained) == ["2", "3", "4"]
    assert retained["4"]["revision_matches"] and retained["4"]["pointer"] == "/4"
    assert len(retained["4"]["proposals"]) == 3 and retained["4"]["proposals_omitted"] == 3
    assert not planning_evidence(observations, revision=9)["mount_searches"]["4"]["revision_matches"]
    assert len(result["proposals"]) == 6
