"""
LLM-based LEGO Instruction Generator

Uses gpt-6-astra to generate LEGO assembly instructions from natural language
descriptions. Supports custom model parameters (size, style, colors).
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Literal
from enum import Enum
import json
import re
from openai import OpenAI
from pathlib import Path

from .dialogue import settings
from .ldraw_catalogue import PartCatalogue, load_catalogue
from .web3d_prototype import (
    Assembly, PlacedPart, AssemblyInstructions, AssemblyStep,
    LEGOBrick, Vector3, BrickLibrary
)


# Part families the generator is allowed to ask for, expressed as LDraw
# number prefixes. They cover the shapes the reference models actually use:
# bricks, plates, tiles, slopes, Technic, wheels and windscreens.
ALLOWED_PART_FAMILIES = (
    "3001", "3002", "3003", "3004", "3005", "3008", "3009", "3010",
    "3622", "3623", "6111", "6112",
    # plates
    "3020", "3021", "3022", "3023", "3024", "3031", "3032", "3034", "3035",
    "3036", "3710", "3666", "4477", "3460",
    # tiles
    "3069", "3070", "2431", "6636",
    # slopes and curved slopes
    "3037", "3038", "3039", "3040", "3041", "3044", "3298", "3665", "4286",
    "85984", "11477", "15068", "50950", "61678", "93606",
    # Technic
    "3700", "3701", "3702", "3703", "3894", "3895", "32000", "32017",
    "32140", "40490", "41239",
    # wheels, tyres, rims
    "4624", "6014", "6015", "56145", "44309", "41897", "30028", "6578",
    # glazing
    "3823", "3822", "3069b", "60603", "62360", "92583",
    # round and specialty
    "3062", "3941", "4032", "11213", "4073", "20482",
)


class ModelCategory(str, Enum):
    """Categories of LEGO models"""
    VEHICLE = "vehicle"
    BUILDING = "building"
    CHARACTER = "character"
    FURNITURE = "furniture"
    LANDSCAPE = "landscape"
    ABSTRACT = "abstract"


class ModelStyle(str, Enum):
    """Style of the LEGO model"""
    REALISTIC = "realistic"
    STYLIZED = "stylized"
    MINIMALIST = "minimalist"
    TECHNICAL = "technical"


@dataclass
class ModelParameters:
    """Parameters for custom LEGO model generation"""
    category: ModelCategory
    description: str
    style: ModelStyle = ModelStyle.REALISTIC
    target_scale: str = "1:24"  # Scale ratio (e.g., "1:24", "minifig")
    # Complex showcase cars in D:/lego/result run 1127-4122 parts, so the
    # default budget has to be able to reach that detail level.
    max_parts: int = 1500
    color_scheme: List[str] = None  # LEGO color names
    structural_priority: Literal["stability", "accuracy", "efficiency"] = "accuracy"

    def __post_init__(self):
        if self.color_scheme is None:
            self.color_scheme = ["Bright Red", "Black", "White", "Dark Gray"]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GenerationResult:
    """Result of LLM instruction generation"""
    success: bool
    assembly: Optional[Assembly]
    instructions: Optional[AssemblyInstructions]
    reasoning: str
    tokens_used: int
    error: Optional[str] = None


class LLMInstructionGenerator:
    """Generate LEGO assembly instructions using LLM"""

    def __init__(self, model: str = "gpt-6-astra", catalogue: PartCatalogue | None = None):
        """Initialize with specified model"""
        self.model = model
        self.catalogue = catalogue if catalogue is not None else load_catalogue()
        config = settings(model_key="CAR2LEGO_BLENDER_MODEL", default_model=model)
        self.api_key = config["api_key"]
        self.base_url = config["base_url"]
        # The client is built on first use. Building it here raised at import
        # time whenever no key was configured, which made the pipeline
        # constructible only on a machine with credentials.
        self._client = None

    @property
    def client(self):
        """OpenAI client, created on first request."""
        if self._client is None:
            # A module plan for a four-figure-part vehicle takes minutes; the
            # OpenAI default timeout surfaces as a bare "Connection error".
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=600.0,
                max_retries=2,
            )
        return self._client

    def available_parts(self, limit: int = 120) -> List[str]:
        """Real LDraw part IDs the model is allowed to place.

        Only parts present in the installed library are offered, so the LLM
        cannot invent a shape that would fail to render or build.
        """
        found: List[str] = []
        for prefix in ALLOWED_PART_FAMILIES:
            if self.catalogue.has(prefix):
                found.append(prefix)
                continue
            variants = self.catalogue.sample(re.sub(r"[a-z]+$", "", prefix) or prefix, limit=3)
            found.extend(variants)
        seen, ordered = set(), []
        for part in found:
            if part not in seen:
                seen.add(part)
                ordered.append(part)
        return ordered[:limit]

    def generate_instructions(
        self,
        params: ModelParameters,
        temperature: float = 0.7
    ) -> GenerationResult:
        """
        Generate LEGO assembly instructions from model parameters.

        Args:
            params: Model generation parameters
            temperature: LLM sampling temperature (0.0-1.0)

        Returns:
            GenerationResult with assembly and instructions
        """
        try:
            # Build system prompt with LEGO construction knowledge
            system_prompt = self._build_system_prompt()

            # Build user prompt with model parameters
            user_prompt = self._build_user_prompt(params)

            # Call LLM to generate instruction plan
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=32000,
                response_format={"type": "json_object"}
            )

            # Parse LLM response
            content = response.choices[0].message.content
            result_data = json.loads(content)

            # Convert LLM output to Assembly and Instructions
            assembly, instructions = self._parse_llm_output(result_data, params)

            return GenerationResult(
                success=True,
                assembly=assembly,
                instructions=instructions,
                reasoning=result_data.get("reasoning", ""),
                tokens_used=response.usage.total_tokens
            )

        except Exception as e:
            return GenerationResult(
                success=False,
                assembly=None,
                instructions=None,
                reasoning="",
                tokens_used=0,
                error=str(e)
            )

    def _build_system_prompt(self) -> str:
        """Build system prompt with LEGO construction expertise"""
        return """You are an expert LEGO designer and assembly instruction writer.

Your task is to generate detailed, buildable LEGO assembly instructions from natural language descriptions.

# LEGO Construction Knowledge

## Brick Types & Usage
- **Standard Bricks (3xxx)**: Primary structural elements, good for walls and bulk
- **Plates (3xxx)**: Thinner than bricks (1/3 height), used for layers and detail
- **Slopes (3xxx)**: Angled pieces for aerodynamic shapes and roofs
- **Tiles (3xxx)**: Flat pieces without studs, for smooth surfaces
- **Technic (4xxxx)**: Pins, axles, gears for mechanical connections
- **Wheels & Axles**: For vehicles, use wheel assemblies with proper clearance

## Structural Principles
1. **Stability**: Alternate brick joints (no vertical seams)
2. **Connection**: Minimum 2 studs overlap for strong connections
3. **Weight Distribution**: Heavy parts on bottom, light on top
4. **Symmetry**: Mirror left/right for vehicles and buildings
5. **Color Blocking**: Group colors logically (chassis, body, details)

## Assembly Order
1. Base/Foundation (wheels, floor, platform)
2. Core Structure (walls, frame, skeleton)
3. Major Sections (cabin, body panels, rooms)
4. Details (windows, doors, accessories)
5. Finishing (tiles, slopes, decorative elements)

## Output Format
Two modes are supported. Prefer **module mode** for anything larger than a
handful of bricks: describe the model as structural blocks and let the builder
tile them into real parts.

### Module mode (preferred)
{
  "reasoning": "Brief explanation of design approach",
  "name": "model name",
  "modules": [
    {"name": "chassis", "origin": [0, 0, 0], "size": [8, 2, 20],
     "color": "Black", "fill": "solid", "step": 1},
    {"name": "body", "origin": [0, 2.4, 0], "size": [8, 4, 16],
     "color": "Bright Red", "fill": "solid", "accent_color": "Black", "step": 2}
  ],
  "details": [
    {"part": "55982", "position": [0, 2, 4], "rotation": [0, 0, 0],
     "color": "Black", "purpose": "front left wheel"},
    {"part": "3069b", "position": [4, 5, 8], "rotation": [0, 0, 0],
     "color": "Trans-Clear", "purpose": "windscreen"}
  ],
  "notes": "assembly order and intent"
}

- `origin` is the module's minimum corner in studs [x, y, z]; `y` is vertical.
- `size` is [width, height, depth] in studs.
- `fill`: "solid" (bricks with plate courses), "shell" or "plate-only".
- Modules must not overlap; stack them by adding the lower module's height.
- `details` are individual parts the tiler cannot produce by filling a box:
  wheels, tyres, rims, windscreens and other glazing. Tiling only makes
  rectangular bodywork, so a vehicle without `details` has no wheels and a
  car without a windscreen reads as a brick. Place them at the position the
  feature belongs (wheel centres at the chassis corners, glass in the cabin
  openings).

**Vehicle wheels are required.** Every vehicle needs four wheel assemblies
from the allowed list — a tyre plus a rim, or a combined wheel part — placed at
the four corners of the chassis. Sites use part numbers such as 55982, 56145,
44309, 41897, 30028, 6578, 6014, 6015 and 4624.

### Part mode (only for very small models)
{
  "reasoning": "...",
  "assembly_steps": [
    {"step_number": 1, "description": "Build the base foundation",
     "parts": [{"brick_id": "3001", "position": [0, 0, 0],
                "rotation": [0, 0, 0], "color": "Black"}]}
  ],
  "dimensions": {"width": 16, "height": 8, "depth": 32},
  "total_parts": 250
}

Positions are part centres in stud units (1 stud = 8mm). Rotations in degrees."""

    def _volume_guidance(self, params: ModelParameters) -> str:
        """Tell the model how much volume its part budget actually fills.

        Modules are tiled at roughly one structural part per 10-14 stud² of a
        brick course, so asking for a 10x5x22 build yields a few hundred parts
        while the budget allows thousands. Stating the arithmetic up front is
        what stops the model returning a doll-sized object.
        """
        # ~11 stud² of one course per part, averaged over brick and plate rows.
        per_layer = 11.0
        # A layer is ~0.8 studs of height on average once plates are mixed in.
        layers_per_stud = 1.25
        volume = params.max_parts * per_layer / layers_per_stud
        # Assume a proportions-neutral box: width:height:depth = 2:1.2:4.
        ratio = (2.0, 1.2, 4.0)
        scale = (volume / (ratio[0] * ratio[1] * ratio[2])) ** (1 / 3)
        dims = tuple(round(r * scale) for r in ratio)
        return (
            f"**Target size**: to use about {params.max_parts} parts, the summed module "
            f"volume should be roughly {int(volume)} stud³ — for example a "
            f"{dims[0]} x {dims[1]} x {dims[2]} stud envelope split across modules. "
            f"Small envelopes (a 10 x 5 x 20 body) only fill a few hundred parts."
        )

    def _build_user_prompt(self, params: ModelParameters) -> str:
        """Build user prompt with model parameters"""
        allowed = self.available_parts()
        parts_block = (
            ", ".join(allowed)
            if allowed
            else "the standard LDraw catalogue (use conventional 3xxx brick and plate numbers)"
        )
        prompt = f"""Generate LEGO assembly instructions for:

**Category**: {params.category.value}
**Description**: {params.description}
**Style**: {params.style.value}
**Scale**: {params.target_scale}
**Max Parts**: {params.max_parts}
**Color Scheme**: {', '.join(params.color_scheme)}
**Priority**: {params.structural_priority}

**Allowed part IDs** (these exist in the installed LDraw library):
{parts_block}

{self._volume_guidance(params)}

Requirements:
1. Use ONLY part IDs from the allowed list above
2. Follow structural stability principles
3. Stay within the part count limit
4. Use the specified color scheme
5. Provide step-by-step assembly order
6. Include exact positions and rotations

Generate the complete assembly instructions in JSON format."""
        return prompt

    def _parse_llm_output(
        self,
        data: dict,
        params: ModelParameters
    ) -> tuple[Assembly, AssemblyInstructions]:
        """
        Parse LLM JSON output into Assembly and Instructions objects.

        Module output is expanded into concrete parts by the deterministic
        builder; part output is used directly for small models. Either way the
        budget and catalogue are enforced here rather than trusted.
        """
        placed_parts = []
        bom: dict[str, int] = {}
        rejected = 0
        unknown_parts: set[str] = set()

        if data.get("modules"):
            placed_parts = self._expand_modules(data, params)
            for part in placed_parts:
                bom[part.brick_definition.lego_id] = bom.get(part.brick_definition.lego_id, 0) + 1
            steps = self._steps_from_modules(data, placed_parts)
        else:
            placed_parts, rejected, unknown_parts = self._parts_from_steps(
                data, params, unknown_parts)
            for part in placed_parts:
                bom[part.brick_definition.lego_id] = bom.get(part.brick_definition.lego_id, 0) + 1
            steps = self._steps_from_parts(data, placed_parts)

        warnings = list(data.get("warnings", []))
        if rejected:
            warnings.append(f"Truncated {rejected} parts to respect max_parts={params.max_parts}")
        if unknown_parts:
            sample = ", ".join(sorted(unknown_parts)[:5])
            warnings.append(
                f"{len(unknown_parts)} part IDs were not in the LDraw library and were "
                f"substituted with Brick 2x2: {sample}"
            )

        instructions = AssemblyInstructions(
            vehicle_name=f"{params.category.value}: {params.description[:50]}",
            vehicle_specs={
                "category": params.category.value,
                "style": params.style.value,
                "scale": params.target_scale,
                "color_scheme": params.color_scheme,
                "max_parts": params.max_parts,
            },
            total_parts=len(placed_parts),
            bom=bom,
            estimated_time_minutes=max(5, len(placed_parts) // 4),
            steps=steps,
            confidence_score=float(data.get("confidence_score", 0.0) or 0.0),
            warnings=warnings,
        )

        assembly = Assembly(
            name=f"{params.category.value}: {params.description[:50]}",
            parts=placed_parts,
            instructions=instructions,
            metadata={
                "category": params.category.value,
                "style": params.style.value,
                "scale": params.target_scale,
                "llm_model": self.model,
                "reasoning": data.get("reasoning", ""),
            }
        )

        return assembly, instructions

    # ------------------------------------------------------------------
    # Plan-to-parts helpers
    # ------------------------------------------------------------------

    def _expand_modules(self, data: dict, params: ModelParameters) -> List[PlacedPart]:
        """Expand a module plan into positioned parts within the budget."""
        from .instruction_expander import BuildPlan, describe, expand_plan
        plan = BuildPlan.from_llm(data)
        parts, spans = expand_plan(plan, self.catalogue, max_parts=params.max_parts)
        self._last_spans = spans
        self._last_module_summary = describe(parts)
        return parts

    def _steps_from_modules(self, data: dict, parts: List[PlacedPart]) -> List[AssemblyStep]:
        """Group module-built parts into steps using the expander's real spans."""
        spans = getattr(self, "_last_spans", None) or [("Assemble the model", 0, len(parts))]
        steps = []
        for index, (name, start, end) in enumerate(spans, start=1):
            if end <= start:
                continue
            steps.append(AssemblyStep(
                step_number=index,
                description=f"Build the {name}",
                parts_to_add=list(range(start, end)),
            ))
        return steps

    def _parts_from_steps(self, data: dict, params: ModelParameters,
                          unknown_parts: set) -> tuple[List[PlacedPart], int, set]:
        """Read explicit part placements from LLM output."""
        placed: List[PlacedPart] = []
        rejected = 0
        for step_data in data.get("assembly_steps", []):
            for part_data in step_data.get("parts", []):
                if len(placed) >= params.max_parts:
                    rejected += 1
                    continue

                requested = str(part_data.get("brick_id", "")).strip().lower()
                # Only parts that exist in the installed LDraw library can be
                # rendered or built, so an invented number is corrected here.
                if requested and not self.catalogue.has(requested):
                    resolved = self.catalogue.resolve(requested)
                    if resolved:
                        requested = resolved
                    else:
                        unknown_parts.add(requested)
                        requested = ""
                brick = (BrickLibrary.resolve_brick(requested, self.catalogue)
                         if requested else None)
                if brick is None:
                    brick = BrickLibrary.get_brick("3003")

                color = part_data.get("color") or params.color_scheme[0]
                position = part_data.get("position") or [0, 0, 0]
                rotation = part_data.get("rotation") or [0, 0, 0]
                placed.append(PlacedPart(
                    instance_id=len(placed),
                    brick_definition=brick,
                    position=Vector3.from_list(list(position)[:3]),
                    rotation=Vector3.from_list(list(rotation)[:3]),
                    color=color,
                ))
        return placed, rejected, unknown_parts

    def _steps_from_parts(self, data: dict, parts: List[PlacedPart]) -> List[AssemblyStep]:
        """Map declared steps onto consecutive ranges of placed parts."""
        steps = []
        cursor = 0
        for index, step_data in enumerate(data.get("assembly_steps", []), start=1):
            declared = len(step_data.get("parts", []))
            ids = list(range(cursor, min(cursor + declared, len(parts))))
            cursor += declared
            if not ids:
                continue
            steps.append(AssemblyStep(
                step_number=index,
                description=step_data.get("description") or f"Step {index}",
                parts_to_add=ids,
            ))
        return steps

    def _estimate_difficulty(self, part_count: int, params: ModelParameters) -> Literal["beginner", "intermediate", "advanced", "expert"]:
        """Estimate build difficulty based on complexity"""
        if part_count < 100:
            return "beginner"
        elif part_count < 300:
            return "intermediate"
        elif part_count < 600:
            return "advanced"
        else:
            return "expert"

    def generate_from_description(
        self,
        description: str,
        category: ModelCategory = ModelCategory.VEHICLE,
        **kwargs
    ) -> GenerationResult:
        """
        Convenience method to generate from a simple description.

        Args:
            description: Natural language description of the model
            category: Model category
            **kwargs: Additional ModelParameters fields

        Returns:
            GenerationResult
        """
        params = ModelParameters(
            category=category,
            description=description,
            **kwargs
        )
        return self.generate_instructions(params)


# Convenience function for quick generation
def generate_lego_model(
    description: str,
    category: str = "vehicle",
    max_parts: int = 500,
    color_scheme: Optional[List[str]] = None
) -> GenerationResult:
    """
    Quick generation function for simple use cases.

    Example:
        result = generate_lego_model(
            "Sports car with spoiler",
            category="vehicle",
            max_parts=400,
            color_scheme=["Bright Red", "Black", "White"]
        )

        if result.success:
            print(f"Generated {len(result.assembly.parts)} parts")
            result.assembly.save_to_file("my_model.json")
    """
    generator = LLMInstructionGenerator()
    params = ModelParameters(
        category=ModelCategory(category),
        description=description,
        max_parts=max_parts,
        color_scheme=color_scheme
    )
    return generator.generate_instructions(params)


if __name__ == "__main__":
    # Test the generator
    print("LLM Instruction Generator - Test")
    print("=" * 50)

    # Example 1: Simple sports car
    print("\n1. Generating sports car...")
    result = generate_lego_model(
        "Red sports car with aerodynamic body and large rear spoiler",
        category="vehicle",
        max_parts=300,
        color_scheme=["Bright Red", "Black", "White", "Dark Gray"]
    )

    if result.success:
        print(f"✓ Success! Generated {len(result.assembly.parts)} parts")
        print(f"  Tokens used: {result.tokens_used}")
        print(f"  Reasoning: {result.reasoning[:100]}...")
    else:
        print(f"✗ Failed: {result.error}")

    # Example 2: Building
    print("\n2. Generating house...")
    generator = LLMInstructionGenerator()
    params = ModelParameters(
        category=ModelCategory.BUILDING,
        description="Two-story house with red roof and white walls",
        style=ModelStyle.STYLIZED,
        max_parts=400,
        color_scheme=["White", "Bright Red", "Brown", "Bright Green"]
    )
    result = generator.generate_instructions(params)

    if result.success:
        print(f"✓ Success! Generated {len(result.assembly.parts)} parts")
        print(f"  Steps: {len(result.instructions.steps)}")
        print(f"  Difficulty: {result.instructions.difficulty}")
    else:
        print(f"✗ Failed: {result.error}")
