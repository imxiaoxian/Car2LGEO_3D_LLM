# Local frontend service

Manage an already-built frontend independently of the API and assembly worker:

```powershell
cd D:\Car2LGEO_3D_LLM
.\scripts\manage-frontend.ps1 -Action Start -BuildDirectory '.next-delivery-20261020-v29'
.\scripts\manage-frontend.ps1 -Action Status
.\scripts\manage-frontend.ps1 -Action Diagnose
.\scripts\manage-frontend.ps1 -Action Restart -BuildDirectory '.next-delivery-20261020-v29'
.\scripts\manage-frontend.ps1 -Action Stop
```

The service runs as a hidden Node process on `127.0.0.1:8778`. The launching
terminal can exit. No visible PowerShell window needs to remain open.
`-BuildDirectory` selects an existing build; this script does not install
dependencies or rebuild the application. The default API origin is
`http://127.0.0.1:8777`.

PID, creation time, executable, build ID and log paths are recorded in
`runs/local-services/frontend-8778/service.json`. Each start has separate
stdout and stderr logs in the same directory. These are runtime records,
not delivery acceptance evidence.

Stop and restart verify the process creation time, Node executable and
absolute Next.js CLI path before stopping the recorded PID. An occupied
port belonging to an unrecorded process is reported without killing it.
When handing over a manually started service, first verify its identity
and stop that exact frontend process. Do not terminate the API, assembly
worker, other Node processes or an entire process tree.

The script changes no Codex permission, approval, firewall or global
configuration. Historical automatic-review denials have no established
root cause. A successful historical start does not establish that the
current session can start a service.

## Diagnosing a stopped service

`-Action Diagnose` reads process identity, listeners, build availability
and frontend/API HTTP health without starting or stopping a process. When
no build is supplied, it uses the last recorded build. The JSON separates
`frontend_ready` from `full_stack_ready`; an available frontend does not
prove the API is running. Health requests use HTTP loopback addresses and
do not follow redirects. Diagnosis does not submit generation jobs.

Issues include a stopped managed frontend, an unmanaged listener, missing
Node/Next/build files, a mismatched recorded build, and unavailable HTTP
endpoints. A missing listener alone does not establish why a process exited.
Read the per-start logs and the command execution result separately.

## Host approval rejection

If command execution returns `blocked by policy` before process creation,
this is a host approval rejection. The project script did not execute, so
it cannot repair, explain or override that rejection. Its diagnostics report
approval status as `not_observable_from_project_script`.

Preserve the rejected command and error. Do not retry it through another
shell, detached helper, encoded command or changed approval rule. Do not
change Codex sandbox/approval settings or global security configuration as
a project repair. Review the denial in the host application; if the reason
is unavailable, report the blocker and seek host support. A user-controlled
terminal is an explicit operational fallback, not evidence that automated
startup has been repaired.

On 2026-10-22, direct invocation of this script with `-Action Start` and the
v29 build was rejected with `blocked by policy` before process creation.
The host supplied no specific reason. Read-only inspection found the v29
build and a historical Next.js ready log, but no active 8777/8778 listener.
This does not establish a Windows execution-policy or Next.js failure.
The official documentation endpoints returned HTTP 403 during this audit,
so no current documentation claim is inferred from them.

Later on 2026-10-22 the same managed `Start` command and a subsequent
`Restart` both succeeded. Separate invocations confirmed the process
survived the launching command's exit. After the API was restored,
`Diagnose` reported both readiness flags true and no issues; the homepage,
Countach design page, direct API and API proxy returned HTTP 200. The
historical rejection cause remains unknown. See
`docs/frontend-startup-incident-20261022.md` for the measured process,
build and endpoint evidence. No manual terminal window is required.

Historically verified on 2026-10-20: v29 start, HTTP readiness, status after the launching
command exited, and managed restart. The live Countach page no longer
overflows horizontally, and the 1440/1920 Playwright module-list cases pass.
The existing API and assembly worker remained running throughout.
