# Project isolation

> Historical record (2026-10-16). The user's 2026-10-29 consolidation decision supersedes the two-repository and desktop-only boundaries below. Current routing and recovery locations are documented in [unified-workflows.md](unified-workflows.md). `D:/lego` remains read-only.

This is the independently versioned LLM + Blender implementation. Its exact repository name is **Car2LGEO_3D_LLM**.

| Project | Modelling method | Local checkout | UI / API |
| --- | --- | --- | --- |
| Car2LEGO_3D | GPT references -> Hunyuan3D-2mv -> mesh -> LEGO | `D:/Car2LEGO_3D` | 8768 / 8767 |
| Car2LGEO_3D_LLM | GPT references -> LLM + Blender MCP -> mesh -> LEGO | `D:/Car2LGEO_3D_LLM` | 8778 / 8777 |

Each project owns its Git database, main branch, Python environment, frontend dependencies, configuration, builds and `runs/`. The desktop workflow is fixed to `gpt-blender`; it cannot silently fall back to Hunyuan3D. CLI legacy backends require explicit selection.

The Python import namespace `car2lego3d` and existing API routes stay compatible. The distribution is `car2lgeo-3d-llm`, with an additional `car2lgeo3dllm` entry point. Always activate this checkout's `.venv` when running it.

The existing `D:/lego` source is reused read-only for GPT references and presentation services. Its source files, database and pending local changes are preserved. Local secrets and runtime artifacts are never published.

Latest pre-migration Blender changes are saved as `archive/llm-blender-before-isolation-20261016` in the source repository. The original Car2LEGO Git history is preserved as a merge parent and an archival ref before the remote main branch is updated.

## Recovery and historical data

- `archive/car2lego-before-llm-migration-20261016` retains the original remote Car2LEGO main branch.
- `archive/car2lego-local-before-llm-migration-20261016` retains the original local Car2LEGO main commit, including the unpublished optional-year work.
- `archive/llm-blender-source-20261016` retains the complete pre-isolation Blender source, including the final reference-group regression test.
- The original local `D:/lego` source and pending changes stay untouched. It is now a legacy service checkout; its old origin URL redirects to this repository. Do not push that checkout's old main branch over this project's main branch.
- Existing generated runs remain under `D:/Car2LEGO_3D-worktrees/gpt61-blender/runs`; new runs belong to this checkout's own `runs/` directory. Local outputs and secrets are not migrated to GitHub.

To inspect the original project, create a separate checkout of the appropriate archival branch. Never reset a working directory that contains pending changes.

Repository: https://github.com/imxiaoxian/Car2LGEO_3D_LLM
Open-source implementation: https://github.com/imxiaoxian/Car2LEGO_3D
