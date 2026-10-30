import pytest

from car2lego3d.assembly_module_review import validate_review


def fixture():
    contract = dict(modules=dict(wheel=dict(roles=["tyre", "rim"])), reference_repairs=[
        dict(id="holes", source_sha256="source")])
    bindings = dict(wheel=dict(roles=dict(tyre=["t"], rim=["r"])))
    response = dict(modules=dict(wheel=dict(status="pass", issues=[], roles=dict(
        tyre=dict(instances=["t"], evidence="Native tyre profile encloses rim at same axle"),
        rim=dict(instances=["r"], evidence="Native rim has five circular openings in the resolved geometry")))),
        repairs=dict(holes=dict(status="pass", source_sha256="source", evidence=["Five openings visible on the actual rim"])))
    return response, contract, bindings


def test_complete_independent_review_is_schema_valid_but_not_full_appearance_acceptance():
    args = fixture()
    assert validate_review(*args) == args[0]


@pytest.mark.parametrize("fault", ["omit_role", "wrong_part", "no_evidence", "pass_with_issue", "wrong_source", "omit_repair"])
def test_self_ratings_and_unbound_review_cannot_close_actual_requirements(fault):
    response, contract, bindings = fixture()
    if fault == "omit_role":
        del response["modules"]["wheel"]["roles"]["rim"]
    if fault == "wrong_part":
        response["modules"]["wheel"]["roles"]["rim"]["instances"] = ["different"]
    if fault == "no_evidence":
        response["modules"]["wheel"]["roles"]["rim"]["evidence"] = ""
    if fault == "pass_with_issue":
        response["modules"]["wheel"]["issues"] = ["tyre is not fitted"]
    if fault == "wrong_source":
        response["repairs"]["holes"]["source_sha256"] = "different"
    if fault == "omit_repair":
        response["repairs"] = {}
    with pytest.raises(ValueError):
        validate_review(response, contract, bindings)
