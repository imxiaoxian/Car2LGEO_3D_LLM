# Frontend startup incident, 2026-10-22

Status: managed startup and restart verified on 2026-10-22. The cause of
the earlier host command rejection remains unknown.

## Scope

Project: `D:\Car2LGEO_3D_LLM`.
Requested frontend build: `.next-delivery-20261020-v29`.
Expected build ID: `xxFHpe7KamVSJyF1z54K7`.
Frontend/API addresses: `127.0.0.1:8778` / `127.0.0.1:8777`.

The supported startup command is:

```powershell
.\scripts\manage-frontend.ps1 -Action Start -BuildDirectory '.next-delivery-20261020-v29'
```

Its earlier execution was rejected before process creation with
`blocked by policy`. No specific rejection reason was supplied. This is
not a PowerShell exception emitted by the script or a Next.js startup error.

## Current observations

Read-only diagnostics at `2026-10-22T12:11:39.5403297Z` found:

- Node exists at `C:\Program Files\nodejs\node.exe`.
- The local Next.js CLI and requested build exist.
- The build ID matches the expected v29 build.
- Port 8778 has no listener. There is no competing listener to terminate.
- The previously recorded frontend PID 44316 is no longer running.
- Frontend and API HTTP probes both fail to connect.
- Diagnosed issues: `managed_frontend_stopped`,
  `frontend_http_unavailable`, `api_http_unavailable`.
- The historical stdout log records Next.js 14.2.35 becoming ready in four
  seconds. Its stderr contains a Node deprecation warning, without a
  recorded fatal startup error. These logs describe the old process.
- Desktop log searches for command rejection/approval errors did not
  establish a specific cause.
- Fetches of the official Codex security and app troubleshooting pages
  returned HTTP 403. No documentation-based explanation was established.

No service was started or stopped during these read-only diagnostics.
The managed-start script already launches a hidden process and records
identity and logs; a persistent visible PowerShell window is not part of
its design.

## Earlier resolution requirements

The host must make a specific rejection reason or supported remedy
available. Repository diagnostics cannot observe or change that decision.
No Codex security settings, approval rules or global configuration were
changed. Repackaging the rejected launch through another shell or helper
does not establish that the underlying problem is repaired.

After a supported resolution, verify the original managed start command,
recorded process identity, v29 build ID, HTTP readiness after the invoking
terminal exits, and managed stop/restart. Verify API health independently.
Only those live results establish automated startup recovery. Vehicle
generation and delivery acceptance remain separate checks.

## Recovery verification

A later direct invocation of the same supported command succeeded on
2026-10-22. No alternate shell, encoded launcher, approval-rule change or
global security-setting change was used. This establishes current startup
capability; it does not explain the historical rejection.

- `Start` launched hidden Node PID 32904 at `2026-10-22T12:36:17Z`.
- A separate `Status` invocation, after the launching command had exited,
  verified that process was running and listening on 8778.
- `Restart` stopped the recorded frontend and launched PID 36540 at
  `2026-10-22T12:36:52Z`, retaining the v29 build ID above.
- The stopped API was restored separately with the project's virtualenv
  `python.exe -u -m car2lego3d desktop-api --port 8777` as a hidden process
  (launcher PID 25272). No assembly worker was started or stopped.
- `Diagnose` at `2026-10-22T12:37:30Z` reported `frontend_ready=true`,
  `full_stack_ready=true`, and an empty issues list.
- The homepage, Countach design page, direct design API and proxied design
  API all returned HTTP 200. Both API responses had 95,430 bytes.

Verified URLs:

```text
http://127.0.0.1:8778/
http://127.0.0.1:8778/designs/9f4244f9ec5d4d23af3dc1ba6384427f
http://127.0.0.1:8777/api/v1/designs/9f4244f9ec5d4d23af3dc1ba6384427f
http://127.0.0.1:8778/api/v1/designs/9f4244f9ec5d4d23af3dc1ba6384427f
```

The final frontend service identity and per-start log paths are recorded
in `runs/local-services/frontend-8778/service.json`. The launching
PowerShell command can exit; no user-maintained terminal window is needed.
This verification covers local service lifecycle and HTTP routing, not
browser rendering, vehicle generation, or vehicle delivery acceptance.
