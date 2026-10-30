# Third-Party Notices

The project glue, plate packing and exports use the MIT license in `LICENSE`.
Model licenses and downloaded dependencies remain separate. This repository does
not redistribute model weights or the Studio/LDraw library. Reused UI source
and catalog assets are identified below.

- **Hunyuan3D-2 / Hunyuan3D-2mv**:
  https://github.com/Tencent-Hunyuan/Hunyuan3D-2 and
  https://huggingface.co/tencent/Hunyuan3D-2mv . Code and model use the
  Tencent Hunyuan 3D 2.0 Community License, not this project's MIT license.
  The license contains territory and use restrictions. The pinned source's
  LICENSE and NOTICE are retained by the downloader; versions and checkpoint
  SHA256 are in `models.lock.json`. No weights or upstream source are bundled.
- **LEGO building instructions**: official set metadata and PDF source links
  are recorded in `data/recipes/official-sources.json`. PDFs remain local
  research inputs and are not redistributed. Module observations and adapted
  recipes are not official replicas or endorsed LEGO designs.
- **TripoSR**: https://github.com/VAST-AI-Research/TripoSR
  Copyright (c) 2024 Tripo AI & Stability AI; MIT code and model weights.
  Downloaded source retains its original LICENSE and per-file notices, including
  Apache-2.0 notices in transformer modules. Commit is fixed in `models.lock.json`.
- **SD-Turbo**: https://huggingface.co/stabilityai/sd-turbo
  The pinned model card describes research, non-commercial and commercial uses
  and directs commercial users to https://stability.ai/license and membership
  terms. Consult those terms before commercial deployment. MIT licensing of this
  repository does not grant rights to the SD-Turbo weights. The image stage is
  optional: use your own legally obtained image via `run --image`.
- **DINO config**: https://huggingface.co/facebook/dino-vitb16 (Apache-2.0).
  TripoSR initializes its image encoder with this configuration; its encoder
  weights come from the TripoSR checkpoint.
- **rembg / U2NetP**: https://github.com/danielgatis/rembg (MIT) and
  https://github.com/xuebinqin/U-2-Net (Apache-2.0). Background-removal weights are
  downloaded by rembg and are not included in this repository.
- **trimesh** (MIT), **scikit-image** (BSD), **PyTorch** (BSD-style), **diffusers**
  and **transformers** (Apache-2.0), and other Python dependencies retain their
  own licenses. See the installed distributions for full notices.
- **Car2LEGO**: https://github.com/imxiaoxian/Car2LEGO (MIT, copyright 2025
  imxiaoxian). Architecture reference: real part IDs, LDraw outputs, and separate
  digital versus physical validation. The Next.js desktop pages, styles, viewer,
  components, brand selector, search helper, catalog, translations, logo assets,
  showcase images, catalog documentation and sync script are reused. Source services
  for recognition, reference images, native geometry and step documents run in
  isolated subprocesses. Studio color identifiers are used as factual mappings.
  Original MIT copyright and license are retained in `frontend/LICENSE.Car2LEGO`.
  No private data or credentials are copied.
- **car-logos-dataset**: https://github.com/filippofilip95/car-logos-dataset,
  revision `bb2d661f28ce617dba8a51bdfc2069a3381a23b0`. Its README states MIT
  for the project but the pinned revision has no standalone LICENSE file.
  Vehicle logos belong to their respective owners; project MIT terms do not
  grant logo redistribution rights. Additional assets come from Carlogos.org,
  manufacturer websites and Seeklogo. Each asset's original source and SHA-256
  are stored in `frontend/lib/data/vehicle-brands.json`; see
  `docs/vehicle-brand-catalog.md` for scope and license limitations.
- **React**, **Next.js**, **Vite**, **TypeScript** (MIT), **lucide-react** (ISC): local page
  dependencies retain their respective licenses in installed packages.

LEGO and BrickLink are trademarks of their owners. This research project is not
affiliated with or endorsed by them.

- **BrickWright / BrickLayer**: https://github.com/AvaLovelace1/brickwright.
  Optional `bricklayer-sdk==0.1.0` provides independent digital collision, ground
  support and connector checks. SDK code is MIT; bundled LDraw geometry/data
  include CC BY 2.0 and CC BY 4.0 material. Retain the installed package's LICENSE
  and NOTICE when redistributing it. Its published benchmark is not validation
  of this project's vehicle outputs.
- **BrickAgent / BrickBench**: https://github.com/kulits/BrickAgent (MIT), and
  **LEGO Forge**: https://github.com/ericfu-tianchi/lego-forge (MIT code, LDraw
  geometry CC BY 2.0), are architecture references. Their code and part assets
  are not copied into this repository. See `docs/open-source-assembly-references.md`.
