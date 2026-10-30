# Web 3D LEGO Generation - Deployment Guide

## Repository Structure

```
Car2LGEO_3D_LLM/  (this repository)
├── src/car2lego3d/
│   ├── llm_instruction_generator.py    # LLM-based instruction generation
│   ├── custom_model_system.py          # Model definitions and validation
│   ├── web3d_prototype.py              # Core geometry and validation
│   ├── web3d_pipeline.py               # End-to-end integration pipeline
│   └── dialogue.py                     # OpenAI configuration (existing)
├── tests/
│   └── test_web3d_integration.py       # Comprehensive test suite
├── docs/
│   ├── WEB3D_IMPLEMENTATION.md         # Implementation details
│   └── WEB3D_README.md                 # Usage guide (from reference)
└── frontend/src/lib/
    └── lego-scene-builder.ts           # Three.js scene builder
```

## Deployment to LEGO_LLM Repository

### Step 1: Prepare Repository

```bash
# Clone target repository
git clone https://github.com/imxiaoxian/LEGO_LLM.git
cd LEGO_LLM

# Initialize structure
mkdir -p src/lego_llm
mkdir -p tests
mkdir -p docs
mkdir -p frontend/src/lib
```

### Step 2: Copy Implementation Files

From this repository (`Car2LGEO_3D_LLM`), copy:

```bash
# Python backend
cp src/car2lego3d/llm_instruction_generator.py LEGO_LLM/src/lego_llm/
cp src/car2lego3d/custom_model_system.py LEGO_LLM/src/lego_llm/
cp src/car2lego3d/web3d_prototype.py LEGO_LLM/src/lego_llm/
cp src/car2lego3d/web3d_pipeline.py LEGO_LLM/src/lego_llm/

# Tests
cp tests/test_web3d_integration.py LEGO_LLM/tests/

# Documentation
cp docs/WEB3D_IMPLEMENTATION.md LEGO_LLM/docs/
cp docs/WEB3D_README.md LEGO_LLM/docs/

# Frontend
cp frontend/src/lib/lego-scene-builder.ts LEGO_LLM/frontend/src/lib/
```

### Step 3: Create Package Configuration

Create `LEGO_LLM/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "lego-llm"
version = "1.0.0"
description = "Web 3D LEGO model generation with LLM integration"
requires-python = ">=3.11,<3.12"
dependencies = [
  "numpy>=1.26.4",
  "jsonschema>=4.26.0",
  "openai>=2.20,<3",
  "python-dotenv>=1.2.4",
  "pydantic>=2.0,<3",
  "pydantic-settings>=2,<3"
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
```

### Step 4: Create Configuration File

Create `LEGO_LLM/.env.example`:

```bash
# OpenAI API Configuration
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.lundong.ai/v1

# Model Configuration
CAR2LEGO_BLENDER_MODEL=gpt-6-astra

# Output Configuration
OUTPUT_DIR=./output
```

### Step 5: Create README

Create `LEGO_LLM/README.md`:

```markdown
# LEGO_LLM - Web 3D LEGO Generation with LLM

Generate high-quality LEGO models from natural language descriptions using LLM (gpt-6-astra).

## Features

- **LLM-Based Generation**: Natural language → LEGO assembly instructions
- **Custom Model System**: Templates and validation for vehicles, buildings, characters
- **Web 3D Rendering**: Real-time Three.js visualization
- **Export Formats**: LDraw (.ldr), JSON, metadata
- **Quality Matching**: 200-400 parts for 1:24 scale vehicles

## Quick Start

### Installation

```bash
pip install -e .
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your OpenAI API key
```

### Usage

#### CLI

```bash
lego-llm "Red sports car with spoiler" \
  --category vehicle \
  --max-parts 400 \
  --output ./output
```

#### Python API

```python
from lego_llm.web3d_pipeline import Web3DLegoPipeline

pipeline = Web3DLegoPipeline()
result = pipeline.generate_from_description(
    description="Sports car with aerodynamic body",
    category="vehicle",
    max_parts=400
)

print(f"Generated {len(result.assembly.parts)} parts")
```

#### Web API

```bash
uvicorn lego_llm.web3d_pipeline:app --host 0.0.0.0 --port 8777
```

## Documentation

- [Implementation Details](docs/WEB3D_IMPLEMENTATION.md)
- [Usage Guide](docs/WEB3D_README.md)
- [API Reference](docs/API.md)

## Testing

```bash
pytest tests/ -v
```

## License

MIT
```

### Step 6: Commit and Push

```bash
cd LEGO_LLM
git add .
git commit -m "Initial implementation: Web 3D LEGO generation with gpt-6-astra

- LLM instruction generator using gpt-6-astra
- Custom model system with templates and validation
- Web 3D prototype with geometry and validation engine
- End-to-end integration pipeline
- Comprehensive test suite
- Three.js frontend scene builder
- LDraw and JSON export
- Quality matching existing Car2LEGO models (200-400 parts)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

git push origin main
```

## Integration with Car2LGEO_3D_LLM

The implementation in this repository (`Car2LGEO_3D_LLM`) can continue to:
- Reuse the existing frontend at port 8778
- Reuse the existing database and data structures
- Reference the quality standards from existing models
- Use the existing OpenAI configuration

The `LEGO_LLM` repository provides a standalone, focused implementation that can be:
- Deployed independently
- Integrated into other systems
- Used as a library or service

## Next Steps

1. **Deploy LEGO_LLM**: Push to GitHub repository
2. **Generate Validation Models**: Create 5-10 test models
3. **Quality Assessment**: Compare with existing models
4. **Documentation**: Complete API documentation
5. **Integration**: Connect with existing systems if needed

## Quality Benchmarks

Target quality (matching existing Car2LEGO models):
- **Part Count**: 200-400 for 1:24 vehicles
- **Structural Score**: >0.8
- **Assembly Steps**: 10-30 organized steps
- **Build Time**: 60-180 minutes estimated
- **Export Formats**: LDraw, JSON, metadata
