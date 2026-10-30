"""Build the standalone LEGO_LLM repository from this project.

The generation code currently lives inside ``car2lego3d`` and imports a few of
its siblings. LEGO_LLM needs to stand alone, so this script copies the required
modules into ``lego_llm`` and rewrites their intra-package imports.

Usage:
    python scripts/build_lego_llm.py --output ../LEGO_LLM
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "car2lego3d"

# Modules the bundle needs, and what each one imports from outside itself.
MODULES = [
    "web3d_prototype.py",
    "ldraw_catalogue.py",
    "part_library.py",
    "ldraw_document.py",
    "instruction_expander.py",
    "llm_instruction_generator.py",
    "custom_model_system.py",
    "web3d_pipeline.py",
    "web3d_acceptance.py",
]

# Sibling imports that exist here but not in the bundle.
IMPORT_REWRITES = {
    "from .common import digest": "from .common import digest",
    "from .dialogue import settings": "from .config import settings",
}

CONFIG_SHIM = '''"""Model and API configuration, read from the environment."""
import os

from dotenv import dotenv_values


def settings(model_key="LEGO_LLM_MODEL", default_model="gpt-6-astra"):
    """Resolve API settings from the environment, then a local .env."""
    from pathlib import Path

    local = dotenv_values(Path(__file__).resolve().parents[2] / ".env")

    def value(key, default=""):
        return os.environ.get(key) or local.get(key) or default

    base = value("OPENAI_BASE_URL", "https://api.lundong.ai/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return dict(
        api_key=value("OPENAI_API_KEY"),
        base_url=base,
        model=value(model_key, value("OPENAI_MODEL", default_model)),
    )
'''

COMMON_SHIM = '''"""Small filesystem helpers shared by the exporter and Acceper."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
'''

PYPROJECT = '''[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "lego-llm"
version = "1.0.0"
description = "Generate buildable LEGO models and instructions from natural language"
readme = "README.md"
requires-python = ">=3.11,<3.12"
dependencies = [
  "numpy>=1.26.4",
  "jsonschema>=4.26.0",
  "openai>=2.20,<3",
  "python-dotenv>=1.2.4",
]

[project.optional-dependencies]
web = ["fastapi>=0.110", "uvicorn>=0.29"]
test = ["pytest>=8.3.3"]

[project.scripts]
lego-llm = "lego_llm.web3d_pipeline:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
'''

ENV_EXAMPLE = '''# API configuration
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.lundong.ai/v1

# Model used for instruction generation
LEGO_LLM_MODEL=gpt-6-astra

# LDraw part library roots, separated by os.pathsep (; on Windows, : elsewhere).
# Only parts found here can be used in a generated model.
CAR2LEGO_LDRAW_ROOTS=D:/lego/Studio 2.0/ldraw
'''


def rewrite(text: str) -> str:
    for old, new in IMPORT_REWRITES.items():
        text = text.replace(old, new)
    # Module-level docstrings still name the source project; keep them.
    return text


def build(output: Path, force: bool = False) -> None:
    package = output / "src" / "lego_llm"
    if package.exists() and any(package.iterdir()):
        if not force:
            raise SystemExit(f"{package} already has content; pass --force to overwrite")
        shutil.rmtree(package)
    package.mkdir(parents=True, exist_ok=True)

    for name in MODULES:
        source = SRC / name
        if not source.is_file():
            raise SystemExit(f"Missing source module: {source}")
        (package / name).write_text(rewrite(source.read_text(encoding="utf-8")),
                                    encoding="utf-8")

    # Support modules the copies import but that carry unrelated project code.
    (package / "config.py").write_text(CONFIG_SHIM, encoding="utf-8")
    (package / "common.py").write_text(COMMON_SHIM, encoding="utf-8")
    (package / "__init__.py").write_text(
        '"""LEGO model generation from natural language."""\n'
        '__version__ = "1.0.0"\n', encoding="utf-8")

    tests = output / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    for name, target in (("test_instruction_expander.py", "test_instruction_expander.py"),
                         ("test_web3d_integration.py", "test_web3d_integration.py")):
        source = ROOT / "tests" / name
        text = source.read_text(encoding="utf-8").replace("car2lego3d", "lego_llm")
        (tests / target).write_text(text, encoding="utf-8")
    (tests / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n",
        encoding="utf-8")

    docs = output / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("WEB3D_IMPLEMENTATION.md", "WEB3D_README.md", "DEPLOYMENT.md"):
        source = ROOT / "docs" / name
        if source.is_file():
            (docs / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    # Measured real-part footprints and the script that regenerates them. Without
    # this data the expander falls back to a dozen hand-written shapes.
    data_dir = output / "data" / "parts"
    data_dir.mkdir(parents=True, exist_ok=True)
    measured = ROOT / "data" / "parts" / "measured.json"
    if measured.is_file():
        shutil.copy2(measured, data_dir / "measured.json")
    scripts = output / "scripts"
    scripts.mkdir(exist_ok=True)
    measurer = ROOT / "scripts" / "measure_parts.py"
    if measurer.is_file():
        text = measurer.read_text(encoding="utf-8").replace("car2lego3d", "lego_llm")
        (scripts / "measure_parts.py").write_text(text, encoding="utf-8")

    (output / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (output / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    readme = output / "README.md"
    if not readme.exists():
        readme.write_text(
            "# LEGO_LLM\n\n"
            "Generate buildable LEGO models and step-by-step instructions from natural\n"
            "language, using gpt-6-astra and a real LDraw part library.\n\n"
            "See `docs/WEB3D_IMPLEMENTATION.md` for the architecture and\n"
            "`docs/DEPLOYMENT.md` for setup.\n", encoding="utf-8")

    print(f"Built {output}")
    for path in sorted(package.iterdir()):
        print(f"  src/lego_llm/{path.name}  ({path.stat().st_size} bytes)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Target checkout of LEGO_LLM")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    build(Path(args.output).resolve(), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
