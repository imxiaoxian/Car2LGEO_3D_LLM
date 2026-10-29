# Countach left-front wheel pivot diagnosis

Revision 66 trial C passed static validation but failed 114 of 115 sampled
rolling poses. The actual failing joint was the trial-C subtree ending in
`2d0f1c33eddfa162`; it was not merely the missing right-front wheel response.
Failed poses contained both native-collider collisions and disconnected parts.

Its parent-local joint used origin `[0,0,0]` and axis `[0,0,1]`, despite the
wheel module being positioned at `[100,10,0]` in that parent. An isolated
experiment changed only the joint to origin `[100,10,0]`, axis `[-1,0,0]`.
All nine rolling poses passed. Evidence is in
`runs/delivery-implementation-20261020/wheel-pivot-revision66/diagnosis.json`
and `motion.json`; source state hash was checked unchanged during the probe.
This is engineering diagnosis, not planner evaluation or vehicle acceptance.

Independently, the live planner at step 68 changed its joint to origin
`[100,10,0]`, axis `[1,0,0]`, producing revision 67. Axis sign reverses the
rotation direction for this symmetric full-range sweep. Its own subsequent
checks and candidate measurements remain necessary; the isolated probe must
not be substituted for them. Failed trial snapshots remain preserved.

## Executed planner result and cache blocker

The planner's revision-67 motion report now shows zero failing poses for
each of five joints sampled at 73 positions. Trial C's rolling joint is
included, and left-front wheel steering response passes. The overall report
still fails because the right-front wheel has not been installed. This is
module-level evidence, not whole-vehicle acceptance.

The read-only `scripts/audit_candidate_remeasurement.py` reproduced a cache
blocker against revision 67. Its geometry fingerprint matches the failed
revision-66 snapshot, while the owned rolling joint changed axis and origin.
`capture()` rejects that existing geometry directory even though its saved
measurement no longer describes the corrected joint. The audit output is
`runs/delivery-implementation-20261020/wheel-pivot-revision66/candidate-remeasurement.json`.
The source-state hash was stable during the audit.

Repair requirements after a safe runtime checkpoint:

- Keep geometry identity for counting three distinct structural candidates;
  joint-only corrections must not count as extra structural alternatives.
- Version measurements separately by module signature and native evidence.
  Retain failed snapshots, and allow a corrected trial to acquire a new
  measurement without overwriting its previous result.
- Select and restore an exact measurement snapshot. Require its current
  surroundings, dependency hashes, and subsequent checks.
- Regress failure-to-pass joint correction, duplicate unchanged capture,
  recolor/name invariance, stale evidence, and restoration of each revision.

The previous worker was stopped through the website control endpoint with
its saved state intact before executor changes. Candidate v2 now separates
the part-structure ID from the articulation ID. Corrected articulation gets
a new immutable candidate snapshot; comparison counts distinct structure
IDs and requires the selected measurement's exact module signature.
Legacy snapshots remain readable, and revision recovery checks structure ID
separately from measurement ID. The skill and vehicle inputs are unchanged.

## Resumed module protection

Recovery revision `assembly-recovery-85d1f9fbefff4dd294d174e877be0ada`
reached a separate blocker: restoring passing wheel candidate B was rolled
back as a change to completed chassis/steering. Protection included every
descendant of completed carrier roots, including the unfinished wheel
subtree retained at recovery. Thus it prevented the active module from
repairing its own existing parts.

After cancelling that worker and confirming it stopped, protection was
changed to exclude only the active module's bound subtree. Completed module
roots and role parts cannot be excluded; their transforms, geometry and
joints remain protected. A checkpoint replay reproduced rejection with the
old rule and acceptance with the corrected rule (35 protected instances).
The candidate, recovery, schedule and mount regressions passed: 77 tests.

Recovery revision `assembly-recovery-a7b2e37214b843cea3d5b8533c09a568`
is executing with this correction. Actual candidate restoration and final
wheel acceptance remain pending; the replay and regressions do not certify
either the wheel or the vehicle.

The live recovery subsequently restored candidate A
`171a26ae6041a05710e4fdce0014b810a39f7d2ca5ae98b4d0e17254827b03d3`
at planner step 10, producing revision 68 without a protection rollback.
Steps 11 and 12 successfully rebound its tyre/rim/hub and executed `check`.
This verifies the restoration path in the actual website worker. Three
passing candidate structures, independent role review, module completion
and whole-vehicle acceptance still require their own evidence.

## Scoped feedback and third-candidate recovery

Recovery `assembly-recovery-5d0ca6a913614c87a2b99b4416a93e4a`
exposes the assigned module check in `module_progress.result.module_task`.
The actual step-0 observation reports three required structures, two passing,
and the retained failed C snapshot. This verifies the scoped-feedback path,
not module completion. The related agent/schedule/candidate regression run
passed 44 tests.

Step 4 successfully restored C at revision 69; step 5 executed its static
check. The saved rolling joint again has axis `[0, 0, 1]` and origin
`[0, 0, 0]`, because restoration intentionally restores the failed snapshot.
Step 6 is evaluating its motion. The corrected pivot must be applied and
remeasured as a new immutable articulation snapshot before C can contribute
to the three passing structures. No third-candidate pass or vehicle
acceptance is established by these observations.

The same worker subsequently corrected C at step 11, revision 70, to axis
`[-1, 0, 0]` and origin `[100, 10, 0]`. Step 12 executed 73-sample motion;
step 13 passed the static check. Step 14 saved passing measurement
`421012aea0c3793e30f27c0c62ae5b0fe50d53302904fc83d24614e2f9d68166`
for structure C. The original failed C snapshot remains present. Together
with A and B this provides three distinct saved passing structures; selected
candidate freshness, module finish, semantic review and whole-vehicle
acceptance remain separate gates. The overall motion result still reports
failure for the absent right-front wheel, outside this assigned module.

The planner selected A after querying the native tyre dimensions of all
three trials. Restoration produced revision 71; static check, 37-sample
motion, module progress and export were executed again. The website
scheduler then advanced from 2/17 to 3/17 and started `wheel_front_right`.
This establishes provisional completion of the assigned left-front wheel
and the actual module handoff. Final independent semantic review, vehicle
checks and appearance acceptance are still outstanding.

## Right-front wheel motion and hierarchy repair

The same recovery reached revision 72 with a passing static check: 29
parts, 34 connections, one component, no collisions and no unknown data.
Its 73-requested-sample motion check nevertheless failed. The right-wheel
joint used the vertical axis and included its hub; adaptive sampling found
282 nonpassing poses of 285. Static connectivity did not establish rolling.

Step 15 attempted to replace the trial but reused an existing model-document
name. The transaction rejected the duplicate name and retained revision 72.
Step 18 used unique `.ldr` names and successfully produced revision 73:
the hub remains in the wheel installation root while rim and tyre descend
from a separate rolling submodel. Step 19 bound their actual role IDs,
producing revision 74. Checks from revision 72 are now stale. Fresh static,
motion and candidate measurements remain required; the right-front wheel
is not complete. The worker PID 6392 was verified alive at planner step 20.

Step 20 then passed the revision-74 static check with the same 29 parts,
34 connections, one component and no collisions or unknown data. Step 21
executed `motion(samples=181)` and returned a current-revision pass: all six
declared joints had zero nonpassing poses, and both installed front-wheel
steering responses passed their nine-sample response checks. This confirms
the repaired trial's digital motion, not three-candidate comparison, final
module acceptance, appearance or whole-vehicle delivery. The live worker
advanced to planner step 22 after the motion operation completed.

The right-wheel planner saved passing A (5L axle `32073.dat`), B (6L axle
`3706.dat`) and C (4L axle `3705.dat`) as distinct structures. B at revision
75 and C at revision 76 each passed static checks and 37-sample motion.
It selected A to match the left wheel, restored it at revision 77 and
repeated binding, static checks, motion and export. Step 38 finished the
assigned module with provisional mechanical acceptance. The actual website
scheduler then advanced to `wheel_rear_left`, reporting 4/17 modules complete.
The worker PID 6392 was verified live at the new module's planner step 1.
Whole-vehicle geometry, independent semantic review, appearance, website
delivery and Studio compatibility remain unaccepted.
