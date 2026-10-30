"""
LLM-based LEGO Instruction Generator

Uses gpt-6-astra to generate LEGO assembly instructions from natural language
descriptions. Supports custom model parameters (size, style, colors).
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Literal
from enum import Enum
import json
from openai import OpenAI
from pathlib import Path

from .dialogue import settings
from .web3d_prototype import (
    Assembly, PlacedPart, AssemblyInstructions, AssemblyStep,
    LEGOBrick, Vector3, BrickLibrary
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
    max_parts: int = 1000
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

    def __init__(self, model: str = "gpt-6-astra"):
        """Initialize with specified model"""
        self.model = model
        config = settings(model_key="CAR2LEGO_BLENDER_MODEL", default_model=model)
        self.client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])

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
Generate a JSON object with:
{
  "reasoning": "Brief explanation of design approach",
  "parts_list": [
    {"brick_id": "3001", "quantity": 10, "color": "Bright Red", "usage": "main body"},
    ...
  ],
  "assembly_steps": [
    {
      "step_number": 1,
      "description": "Build the base foundation",
      "parts": [
        {"brick_id": "3001", "position": [0, 0, 0], "rotation": [0, 0, 0], "color": "Black"},
        ...
      ]
    },
    ...
  ],
  "dimensions": {"width": 16, "height": 8, "depth": 32},
  "total_parts": 250
}

All positions in stud units (1 stud = 8mm). Rotations in degrees [x, y, z]."""

    def _build_user_prompt(self, params: ModelParameters) -> str:
        """Build user prompt with model parameters"""
        prompt = f"""Generate LEGO assembly instructions for:

**Category**: {params.category.value}
**Description**: {params.description}
**Style**: {params.style.value}
**Scale**: {params.target_scale}
**Max Parts**: {params.max_parts}
**Color Scheme**: {', '.join(params.color_scheme)}
**Priority**: {params.structural_priority}

Requirements:
1. Use realistic LEGO brick types and connections
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

        Args:
            data: Parsed JSON from LLM
            params: Original model parameters

        Returns:
            (Assembly, AssemblyInstructions) tuple
        """
        # Extract parts from LLM output
        placed_parts = []
        for step_data in data.get("assembly_steps", []):
            for part_data in step_data.get("parts", []):
                brick_id = part_data["brick_id"]

                # Get brick definition from library
                brick = BrickLibrary.get_brick(brick_id)
                if brick is None:
                    # Fallback to standard 2x2 brick if unknown
                    brick = BrickLibrary.get_brick("3003")

                # Create placed part
                part = PlacedPart(
                    instance_id=len(placed_parts),
                    brick=brick,
                    position=Vector3(*part_data["position"]),
                    rotation=Vector3(*part_data["rotation"]),
                    color=part_data["color"]
                )
                placed_parts.append(part)

        # Create assembly
        assembly = Assembly(
            name=f"{params.category.value}: {params.description[:50]}",
            parts=placed_parts,
            instructions=None,  # Will be set below
            metadata={
                "category": params.category.value,
                "style": params.style.value,
                "scale": params.target_scale,
                "llm_model": self.model,
                "reasoning": data.get("reasoning", "")
            }
        )

        # Create instruction steps
        steps = []
        part_index = 0
        for step_data in data.get("assembly_steps", []):
            parts_in_step = []
            for _ in step_data.get("parts", []):
                if part_index < len(placed_parts):
                    parts_in_step.append(placed_parts[part_index])
                    part_index += 1

            step = AssemblyStep(
                step_number=step_data["step_number"],
                description=step_data["description"],
                parts_to_add=parts_in_step,
                camera_angle=Vector3(45, 0, 45),  # Default viewing angle
                highlight_connections=True
            )
            steps.append(step)

        # Create instructions
        instructions = AssemblyInstructions(
            model_name=assembly.name,
            steps=steps,
            difficulty=self._estimate_difficulty(len(placed_parts), params),
            estimated_build_time_minutes=len(placed_parts) // 4  # ~4 parts/minute
        )

        assembly.instructions = instructions

        return assembly, instructions

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
