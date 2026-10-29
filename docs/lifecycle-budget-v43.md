# Lifecycle budget diagnostic v43

This experiment does not replace v42 results or select a production planner.
The adapter, executor, skill, source placements, native dependencies and held-out
exclusions remain unchanged. New outputs must use new directories.

The prospective budget is 64 decisions and 1500 seconds per task for every
planner. The source demonstration requires 44 actions before pagination for a
16-part module. The original 40-decision suite could not accommodate that
strategy. The extra budget tests whether lifecycle termination succeeds after
placement; it does not change geometric or completion criteria.

Hypotheses:

- If the adapter still reads beyond the placement count or cannot issue
  check/export/finish, termination training remains deficient.
- If it completes under this budget, v42 remains a valid efficiency failure;
  report the extra cost and compare competitors under the same new contract.
- If Codex repeats identical context pages, retained context and navigation
  need diagnosis independently of its final part count.

Both tasks remain supplied-placement module reconstruction. They cannot prove
image understanding, shape fitting, mechanisms or complete vehicle delivery.
No task result from this experiment is automatically eligible as approved
training data. Production Countach work continues against its own frozen
contract.

Before promotion, require independent module and whole-car quality evidence,
target-family isolation, comparable runtime contracts and actual website
execution. Output-token cap parity is still unverified.

## Task 0 adapter outcome

The independent local_finetuned run terminated as a draft after 42 decisions
and 915.063 seconds with repair_budget_exhausted. It installed all 16 expected
parts with exact transforms; host digital checking and archive roundtrip passed.
The planner itself never called check, export or finish. At decisions 39, 40
and 41 it requested semantic:/operations/16 from a 16-element list.

The decision-39 input explicitly contained placements.count=16,
state.instances=16, remaining_steps=25, and the instruction to check at count,
then export and finish. Therefore the observed failure is not explained by
the original 40-decision cap or missing placement-count evidence. Preserve
this run as a failed autonomous lifecycle, not an approved training trajectory.

The next adapter revision needs independently executed terminal-state and
error-recovery demonstrations, including varying part counts and held-out
families. Evaluate check/export/finish transitions separately from placement
accuracy before another full benchmark. Do not hardcode the held-out index
or silently finish on the planner's behalf. The Countach runtime and frozen
v43 experiment remain unchanged while their workers run.

## Independent Codex task 0 outcome

The process exited with code 1 at planner decision 24. The CLI exhausted five
stream reconnection attempts and reported service temporarily unavailable;
the agent recorded interrupted/planner_unavailable at 25 steps. There is no
benchmark-result.json and no successful execution claim. Its preserved trace
contains 21 context calls, three edit calls and one planner_error. The exact
semantic operation-0 page at offset 0 was requested five times. Transport
failure and redundant navigation are separate findings; neither proves the
quality of an otherwise completed assembly. No planner is promoted.

## Recovery-data audit

The lifecycle diagnostic now separates planner check/export/finish actions
from host post-run validation. Both task-0 traces have no planner lifecycle
actions. The adapter has three out-of-range context failures; Codex has two
disconnected-placement transaction failures and a final transport failure.

`training/assembly_model/loop_data.py` replay rejects any observation with
`ok=false`, even if later actions recover. The current pipeline cannot admit
executed error-recovery demonstrations. This is a coverage limitation, not
evidence of completed recovery training.

A future recovery-data path must replay the failed action, verify rollback,
execute the correction, and require final exact geometry, digital validation,
current export and planner finish. Preserve benchmark failures as diagnosis,
not approved training samples. Vary placement counts, exclude target families,
and do not special-case benchmark index 16. Keep the live Countach runtime
and hashed inputs unchanged.

## Website observation

The managed v29 website was opened in an isolated agent-browser session at
http://127.0.0.1:8778/ and the Countach detail route. The page loaded and showed
the active left-front-wheel task, missing modules and unverified acceptance.
At viewport width 1264, document scrollWidth was 1249: no document-level
horizontal overflow. Screenshot: countach-v29-browser.png in this experiment
directory. This is a live status/layout check only, not fresh-generation,
model-preview, download-hash or vehicle acceptance evidence.
