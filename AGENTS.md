# Project Boundary

- This repository owns both the LLM + Blender and open-source reconstruction workflows. Its exact name is `Car2LGEO_3D_LLM`.
- Default CLI and desktop reconstruction to `gpt-blender`; Hunyuan and hybrid modes require explicit selection.
- Keep the migrated advisory-size fitter in `car2lego3d.mesh_envelope`; do not weaken the main automatic/exact sizing contracts.
- Hybrid mode keeps Blender semantics and binds calibrated Hunyuan geometry as shape evidence. Meshes never supply native ports or mechanical acceptance.
- Legacy repository recovery archives live outside the checkout in `D:/lego-project-archives`. They can contain secrets and must never be published.
- Use this checkout's environments, configuration, caches, runs and frontend build output. Default UI/API ports are 8778/8777.
- `D:/lego` is a read-only service provider. Do not alter its source files, database or pending changes.
- Keep GPT image requests in groups of at most five; complete the entire group before dispatching the next group.
- Preserve historical artifacts and their acceptance labels; generated exports and unit tests do not certify model appearance.
- Run the relevant Python regression tests and frontend build before publishing changes.
