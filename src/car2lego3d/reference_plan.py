"""Camera and dependency contracts for a consistent, modular vehicle reference set."""
from dataclasses import asdict, dataclass, replace
import hashlib
import json


@dataclass(frozen=True)
class Shot:
    id: str
    camera: str
    modules: tuple
    state: str = "closed; wheels straight"
    parents: tuple = ("overview",)
    category: str = "detail"
    focus: str = ""

    def record(self):
        return asdict(self)

    def instructions(self):
        framing = ("Show the complete car with a 15 percent margin. "
                   if self.category in {"anchor", "whole"} else
                   "Show this single detail with enough surrounding bodywork to establish its attachment. ")
        return (f"ONE image, ONE camera: {self.camera}. State: {self.state}. {framing}"
                f"Focus: {self.focus}. Use the exact identity, trim, paint, wheels and interior of the "
                "supplied identity anchor and parent reference images. Preserve asymmetric features. "
                "Use a white studio background and uniform sharp lighting. No contact sheet, extra views, "
                "captions, dimensions, diagrams or duplicated cars. This is a real vehicle appearance "
                "reference; LEGO construction is designed separately. Hidden mechanisms are uncertain "
                "design inferences, never measured evidence. Keep adjacent panel edges visible.")


def reference_plan(identity=""):
    shots = [Shot("overview", "front-left three-quarter, elevation 15 degrees, long focal length",
                  ("identity",), category="anchor", parents=(), focus="exact vehicle and variant")]
    for name, camera in [("front", "orthographic front at hub height"),
                         ("back", "orthographic rear at hub height"),
                         ("left", "orthographic left side, nose points image LEFT"),
                         ("right", "orthographic right side, nose points image RIGHT"),
                         ("top", "orthographic top, nose points image TOP"),
                         ("front_left", "front-left three-quarter, nose points image LEFT, elevation 25 degrees with hood and roof clearly visible"),
                         ("front_right", "front-right three-quarter, nose points image RIGHT, elevation 25 degrees with hood and roof clearly visible"),
                         ("rear_left", "rear-left three-quarter, nose extends image LEFT, elevation 25 degrees with rear deck and roof clearly visible"),
                         ("rear_right", "rear-right three-quarter, nose extends image RIGHT, elevation 25 degrees with rear deck and roof clearly visible")]:
        parents = ("overview",)
        if "_" in name:
            end, side = name.split("_")
            parents += (side, "back" if end == "rear" else "front", "top")
        shots.append(Shot(name, camera, ("body", "cabin", "front", "rear"), category="whole", parents=parents))
    details = [
        ("windscreen", "front-left oblique close-up", ("glazing", "cabin"), ("front_left", "top"), "closed", "windscreen thickness, A pillars, cowl and roof header"),
        ("door_left_closed", "left door exterior at beltline", ("doors", "glazing"), ("left",), "left door closed", "door panel, glass, frame, seals and fixed quarter glass ownership"),
        ("door_right_closed", "right door exterior at beltline", ("doors", "glazing"), ("right",), "right door closed", "door panel, glass, frame, seals and fixed quarter glass ownership"),
        ("door_left_open", "left front oblique door jamb, nose points image LEFT", ("doors", "hinges", "cabin"), ("door_left_closed", "left", "front_left"), "left door fully open in its real opening direction; right door closed", "open the SAME left door shown closed in the side reference; glass and mirror stay attached to moving door; hinge, sill, latch and fixed body context. Retain the cabin's steering side"),
        ("door_right_open", "right front oblique door jamb, nose points image RIGHT", ("doors", "hinges", "cabin"), ("door_right_closed", "right", "front_right"), "right door fully open in its real opening direction; left door closed", "open the SAME right door shown closed in the side reference, not the anchor's left door; glass and mirror stay attached to moving door; hinge, sill, latch and fixed body context. On a left-hand-drive car, the steering wheel stays on the FAR side across the cabin, not beside this open passenger doorway; never mirror the cabin"),
        ("headlamps", "low front-left close-up", ("lights", "front"), ("front", "front_left"), "closed; lamps off", "lens outline, housing depth, reflectors, segment dividers and bumper mounting"),
        ("taillamps", "rear-left close-up", ("lights", "rear"), ("back", "rear_left"), "closed; lamps off", "tail lamp lenses, recesses, dividers, fascia and hatch edge"),
        ("front_cabin_join", "front-left oblique cowl and fender close-up", ("front", "cabin"), ("left", "front_left"), "closed", "continuous hood-fender-cowl-A pillar boundary and intentional crease lines"),
        ("rear_cabin_join", "rear-left oblique buttress close-up", ("rear", "cabin"), ("left", "rear_left"), "closed", "rear glass seating, buttress, rear deck and continuous quarter panel boundary"),
        ("dashboard", "through open driver door toward dashboard", ("interior", "cabin"), ("front_left", "left"), "driver door open", "dashboard, gauges, steering wheel, console and interior colours"),
        ("seats_pedals", "oblique close-up from the forward edge of the open driver doorway, looking REARWARDS at the driver seat rather than forwards at the dashboard", ("interior", "cabin"), ("dashboard",), "driver door open; seats in normal installed positions", "driver seat cushion and front face of its backrest, headrest, console side, visible floor and sill. The driver seat is the primary subject and must fit in frame. The opposite seat may be naturally occluded and has its own passenger_seat reference. Pedals and seat-mount details have separate close-ups; do not remove or move seats, roof or pillars to expose hidden surfaces"),
        ("passenger_seat", "oblique close-up from the forward edge of the open passenger doorway, looking REARWARDS at the passenger seat rather than forwards at the dashboard", ("interior", "cabin"), ("door_right_open",), "passenger door open; seats in normal installed positions", "passenger seat cushion and front face of its backrest, headrest, console side, visible floor, sill and door attachment context. The passenger seat is the primary subject and must fit in frame. Preserve the actual steering side across the cabin; do not mirror the interior or add passenger pedals. The opposite driver seat may be naturally occluded and has its own seats_pedals reference. Keep roof, pillars and both seats installed"),
        ("pedal_footwell", "low close-up through open driver doorway into the footwell below the steering column", ("interior", "cabin"), ("dashboard", "seats_pedals"), "driver door open", "driver pedal faces, pedal arms where visible, footwell, steering-column lower end, console side and adjacent seat-base context; passenger footwell has no driving pedals"),
        ("seat_mounts", "floor-level close-up through open driver doorway along the driver seat base and sill", ("interior", "cabin"), ("seats_pedals",), "driver door open; seats in normal installed positions", "seat cushion lower edge, visible mounting rails or brackets, floor attachment and sill boundary. Show only naturally visible mounting hardware; covered attachments remain uncertain rather than inventing or removing upholstery"),
        ("front_running_gear", "low left front wheel and wheelwell close-up", ("wheels", "steering", "suspension"), ("left", "front_left"), "front wheel steered 20 degrees, normal ride height", "tyre and rim dimensions, hub, visible suspension and arch clearance; hidden parts uncertain"),
        ("rear_running_gear", "low left rear wheel and wheelwell close-up", ("wheels", "suspension"), ("left", "rear_left"), "normal ride height", "tyre, rim, hub, visible suspension and rear arch clearance"),
        ("front_cover_open", "front-left oblique open bonnet", ("front", "hinges"), ("front_left", "top"), "front cover fully open", "moving cover perimeter, hinge region, compartment and stationary cowl"),
        ("rear_cover_open", "rear-left oblique open rear cover", ("rear", "hinges"), ("rear_left", "top"), "rear cover fully open", "exact moving panel grouping, hinges and stationary cabin; preserve full clamshell where applicable"),
        ("powertrain_cargo", "overhead close-up into open rear service compartment", ("rear", "powertrain"), ("rear_left", "top"), "service cover open", "correct powertrain or storage layout for this variant; no invented ICE for an electric car"),
    ]
    shots += [Shot(name, camera, modules, state, ("overview", *parents), focus=focus)
              for name, camera, modules, parents, state, focus in details]
    name = identity.lower().replace(" ", "")
    if "countach" in name:
        shots += [Shot("scissor_hinge", "left door hinge exterior oblique close-up", ("doors", "hinges"),
                       "left scissor door raised approximately 35 degrees from its CLOSED pose; intermediate travel, not upright",
                       ("overview", "door_left_closed"),
                       focus="front pivot and adjoining door/body attachment, glass and mirror moving with door, lower trailing corner lifted clear of sill; retain the real hinge location. The angle is a requested synthetic inspection pose, not a measured mechanism limit"),
                  Shot("side_intake", "left rear-quarter side intake close-up", ("body", "rear"),
                       parents=("overview", "rear_left"), focus="Countach intake mouth, scoops and adjacent buttress"),
                  Shot("popup_lamps", "front-left close-up", ("front", "lights", "hinges"),
                       "both pop-up headlamps raised", ("overview", "headlamps"), focus="headlamp pods and pivots with front bonnet context")]
    elif "f40" in name:
        shots.append(Shot("rear_clamshell", "rear-left wide oblique", ("rear", "hinges"),
                          "entire rear clamshell raised", ("overview", "rear_cover_open"),
                          focus="wing, rear glass and rear fenders move together; fixed cockpit stays stationary"))
    elif "forme" in name or "for-me" in name:
        shots.append(Shot("rear_doors", "left rear door exterior oblique", ("doors", "interior"),
                          "rear passenger door fully open", ("overview", "left"),
                          focus="four-door SUV rear door glass belongs to rear door; rear seats and fixed C pillar"))
    shots = baseline_shots(shots, identity)
    validate_plan(shots)
    return shots


def baseline_shots(shots, identity):
    """Apply the agreed IO detail budget without changing historical manifests."""
    from .baseline_detail import detail_standard
    standard = detail_standard(dict(model=identity))
    if standard is None:
        return shots
    focuses = {
        "seats_pedals": "driver seat cushion and front face of its backrest, headrest, console side, visible floor and sill. The driver seat is the primary subject and must fit in frame. The opposite seat may be naturally occluded and has its own passenger_seat reference. Keep roof, pillars and both seats installed. Match the visible cabin detail of the knowledge-base IO; hidden pedal hardware and seat rails are outside this reference's scope",
        "headlamps": "visible lamp outline, recess, housing and bumper attachment at the knowledge-base IO detail level. Separate optical reflectors, lens internals and hidden actuators are not required",
        "taillamps": "visible tail lamp outline, colour divisions, recess, fascia and hatch edge at the knowledge-base IO detail level. Do not require hidden optical layers",
    }
    result = []
    for shot in shots:
        if shot.id in {"pedal_footwell", "seat_mounts"}:
            continue
        if shot.id in focuses:
            shot = replace(shot, focus=focuses[shot.id])
        if shot.id in {"front_running_gear", "rear_running_gear"}:
            modules = tuple(module for module in shot.modules
                if (module != "suspension" or standard["suspension_required"])
                and (module != "steering" or standard["steering_required"]))
            state = shot.state
            if shot.id == "front_running_gear" and not standard["steering_required"]:
                state = "front wheels straight, normal ride height"
            mechanisms = "; visible suspension" if standard["suspension_required"] else ""
            shot = replace(shot, modules=modules, state=state,
                focus="tyre and rim dimensions, seated hub, axle mounting and arch clearance"
                    + mechanisms + ". Match the knowledge-base IO mechanisms; hidden structures remain uncertain")
        if (shot.id in {"front_cover_open", "rear_cover_open", "powertrain_cargo"}
                and standard["cover_scope"] == "baseline_review_required"):
            shot = replace(shot, focus=shot.focus + ". This synthetic pose explores a design option; "
                "cover articulation in the source IO remains unverified and must be reviewed before setting a mechanism requirement")
        result.append(shot)
    return result


def validate_plan(shots):
    table = {shot.id: shot for shot in shots}
    if len(table) != len(shots) or "overview" not in table:
        raise ValueError("Duplicate shots or missing identity anchor")
    visited, active = set(), set()

    def visit(name):
        if name not in table or name in active:
            raise ValueError("Missing or cyclic reference parent")
        if name in visited:
            return
        active.add(name)
        for parent in table[name].parents:
            visit(parent)
        active.remove(name)
        visited.add(name)
    for name in table:
        visit(name)


def shot_fingerprint(shot, parents):
    payload = dict(version=2, shot=shot.record(), parents=parents)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
