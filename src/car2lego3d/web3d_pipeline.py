"""
End-to-End Integration Pipeline

Connects LLM instruction generation → Web 3D rendering → Validation → Export.
Provides complete workflow for LEGO model generation from natural language.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from pathlib import Path
import json
import time

from .llm_instruction_generator import (
    LLMInstructionGenerator,
    ModelParameters,
    GenerationResult,
    ModelCategory,
    ModelStyle
)
from .custom_model_system import (
    ModelDefinitionManager,
    ModelDefinitionValidator,
    ValidationResult as ModelValidationResult
)
from .web3d_prototype import (
    Assembly,
    AssemblyInstructions,
    LDrawExporter,
    ValidationReport
)


@dataclass
class PipelineResult:
    """Complete pipeline execution result"""
    success: bool
    model_name: str
    assembly: Optional[Assembly]
    instructions: Optional[AssemblyInstructions]
    validation_report: Optional[ValidationReport]
    exports: Dict[str, Path]  # format -> file path
    generation_time_seconds: float
    llm_tokens_used: int
    metadata: Dict[str, Any]
    error: Optional[str] = None


class Web3DLegoPipeline:
    """Complete end-to-end pipeline for LEGO model generation"""

    def __init__(
        self,
        llm_model: str = "gpt-6-astra",
        output_dir: Path = None
    ):
        """
        Initialize pipeline.

        Args:
            llm_model: LLM model to use for generation
            output_dir: Directory for output files (default: ./output)
        """
        self.llm_generator = LLMInstructionGenerator(model=llm_model)
        self.model_manager = ModelDefinitionManager()
        self.output_dir = output_dir or Path("./output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_from_description(
        self,
        description: str,
        category: str = "vehicle",
        style: str = "realistic",
        max_parts: int = 500,
        color_scheme: Optional[List[str]] = None,
        export_formats: Optional[List[str]] = None
    ) -> PipelineResult:
        """
        Generate complete LEGO model from natural language description.

        Args:
            description: Natural language model description
            category: Model category (vehicle, building, character, etc.)
            style: Model style (realistic, stylized, minimalist, technical)
            max_parts: Maximum part count
            color_scheme: List of LEGO colors to use
            export_formats: Formats to export (ldraw, json, etc.)

        Returns:
            PipelineResult with generated model and exports
        """
        start_time = time.time()
        exports = {}

        try:
            # Phase 1: LLM Instruction Generation
            print(f"[1/5] Generating instructions with {self.llm_generator.model}...")
            params = ModelParameters(
                category=ModelCategory(category),
                description=description,
                style=ModelStyle(style),
                max_parts=max_parts,
                color_scheme=color_scheme
            )

            generation_result = self.llm_generator.generate_instructions(params)

            if not generation_result.success:
                return PipelineResult(
                    success=False,
                    model_name=description[:50],
                    assembly=None,
                    instructions=None,
                    validation_report=None,
                    exports={},
                    generation_time_seconds=time.time() - start_time,
                    llm_tokens_used=0,
                    metadata={},
                    error=generation_result.error
                )

            assembly = generation_result.assembly
            instructions = generation_result.instructions

            print(f"✓ Generated {len(assembly.parts)} parts in {len(instructions.steps)} steps")

            # Phase 2: Structural Validation
            print("[2/5] Validating assembly constraints...")
            validation_report = assembly.validate()
            print(f"✓ Validation: {validation_report.is_valid}, Score: {validation_report.structural_score:.2f}")

            if not validation_report.is_valid:
                print(f"  Warnings: {len(validation_report.issues)} issues found")
                for issue in validation_report.issues[:3]:
                    print(f"    - {issue.severity}: {issue.description}")

            # Phase 3: Save Assembly JSON
            print("[3/5] Saving assembly data...")
            model_name = assembly.name.replace(" ", "_").lower()
            assembly_path = self.output_dir / f"{model_name}_assembly.json"
            assembly.save_to_file(assembly_path)
            exports["json"] = assembly_path
            print(f"✓ Saved assembly to {assembly_path}")

            # Phase 4: Export to requested formats
            print("[4/5] Exporting to formats...")
            export_formats = export_formats or ["ldraw"]

            if "ldraw" in export_formats:
                ldraw_content = LDrawExporter.export(assembly)
                ldraw_path = self.output_dir / f"{model_name}.ldr"
                ldraw_path.write_text(ldraw_content, encoding="utf-8")
                exports["ldraw"] = ldraw_path
                print(f"✓ Exported LDraw to {ldraw_path}")

            # Phase 5: Generate metadata summary
            print("[5/5] Generating metadata...")
            metadata = {
                "model_name": assembly.name,
                "description": description,
                "category": category,
                "style": style,
                "total_parts": len(assembly.parts),
                "instruction_steps": len(instructions.steps),
                "estimated_build_time_minutes": instructions.estimated_time_minutes,
                "bom_unique_parts": len(instructions.bom),
                "confidence_score": instructions.confidence_score,
                "validation_score": validation_report.structural_score,
                "validation_issues": len(validation_report.issues),
                "llm_model": self.llm_generator.model,
                "llm_reasoning": generation_result.reasoning[:200],
                "color_scheme": params.color_scheme,
                "generation_timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            metadata_path = self.output_dir / f"{model_name}_metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            exports["metadata"] = metadata_path
            print(f"✓ Saved metadata to {metadata_path}")

            # Success!
            generation_time = time.time() - start_time
            print(f"\n✓ Pipeline complete in {generation_time:.1f}s")

            return PipelineResult(
                success=True,
                model_name=assembly.name,
                assembly=assembly,
                instructions=instructions,
                validation_report=validation_report,
                exports=exports,
                generation_time_seconds=generation_time,
                llm_tokens_used=generation_result.tokens_used,
                metadata=metadata
            )

        except Exception as e:
            return PipelineResult(
                success=False,
                model_name=description[:50],
                assembly=None,
                instructions=None,
                validation_report=None,
                exports=exports,
                generation_time_seconds=time.time() - start_time,
                llm_tokens_used=0,
                metadata={},
                error=str(e)
            )

    def generate_from_template(
        self,
        template_name: str,
        custom_name: Optional[str] = None,
        export_formats: Optional[List[str]] = None
    ) -> PipelineResult:
        """
        Generate model from a predefined template.

        Args:
            template_name: Template name (vehicle_sports_car, building_house, etc.)
            custom_name: Optional custom name for the model
            export_formats: Formats to export

        Returns:
            PipelineResult
        """
        # Load template
        model_def = self.model_manager.create_from_template(template_name, custom_name)

        # Extract parameters
        description = model_def.get("description", "")
        category = model_def.get("category", "vehicle")
        specs = model_def.get("specifications", {})
        style = specs.get("style", "realistic")
        max_parts = specs.get("part_budget", {}).get("max_total_parts", 500)
        color_scheme = specs.get("color_palette")

        # Run pipeline
        return self.generate_from_description(
            description=description,
            category=category,
            style=style,
            max_parts=max_parts,
            color_scheme=color_scheme,
            export_formats=export_formats
        )

    def batch_generate(
        self,
        descriptions: List[Dict[str, Any]],
        export_formats: Optional[List[str]] = None
    ) -> List[PipelineResult]:
        """
        Generate multiple models in batch.

        Args:
            descriptions: List of model specifications
            export_formats: Export formats for all models

        Returns:
            List of PipelineResults
        """
        results = []
        total = len(descriptions)

        for i, spec in enumerate(descriptions, 1):
            print(f"\n{'='*60}")
            print(f"Batch Generation: {i}/{total}")
            print(f"{'='*60}")

            result = self.generate_from_description(
                description=spec.get("description", ""),
                category=spec.get("category", "vehicle"),
                style=spec.get("style", "realistic"),
                max_parts=spec.get("max_parts", 500),
                color_scheme=spec.get("color_scheme"),
                export_formats=export_formats
            )

            results.append(result)

            if result.success:
                print(f"✓ Model {i}/{total} completed successfully")
            else:
                print(f"✗ Model {i}/{total} failed: {result.error}")

        return results


# ============================================================================
# FastAPI Web Service (Optional)
# ============================================================================

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel

    app = FastAPI(title="Web 3D LEGO Generator API", version="1.0.0")
    pipeline = Web3DLegoPipeline()

    class GenerationRequest(BaseModel):
        description: str
        category: str = "vehicle"
        style: str = "realistic"
        max_parts: int = 500
        color_scheme: Optional[List[str]] = None
        export_formats: Optional[List[str]] = ["ldraw", "json"]

    @app.post("/api/generate")
    async def generate_model(request: GenerationRequest):
        """Generate LEGO model from description"""
        result = pipeline.generate_from_description(
            description=request.description,
            category=request.category,
            style=request.style,
            max_parts=request.max_parts,
            color_scheme=request.color_scheme,
            export_formats=request.export_formats
        )

        if not result.success:
            raise HTTPException(status_code=500, detail=result.error)

        return {
            "success": True,
            "model_name": result.model_name,
            "total_parts": len(result.assembly.parts),
            "instruction_steps": len(result.instructions.steps),
            "exports": {k: str(v) for k, v in result.exports.items()},
            "metadata": result.metadata
        }

    @app.get("/api/download/{format}/{filename}")
    async def download_file(format: str, filename: str):
        """Download generated file"""
        filepath = pipeline.output_dir / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(filepath)

    @app.get("/api/templates")
    async def list_templates():
        """List available templates"""
        from .custom_model_system import ModelTemplate
        templates = ModelTemplate.get_all_templates()
        return {
            "templates": [
                {
                    "name": name,
                    "category": data.get("category"),
                    "description": data.get("description")
                }
                for name, data in templates.items()
            ]
        }

except ImportError:
    # FastAPI not installed, skip web service
    pass


# ============================================================================
# CLI Interface
# ============================================================================

def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Web 3D LEGO Generator")
    parser.add_argument("description", help="Model description")
    parser.add_argument("--category", default="vehicle", help="Model category")
    parser.add_argument("--style", default="realistic", help="Model style")
    parser.add_argument("--max-parts", type=int, default=500, help="Maximum parts")
    parser.add_argument("--colors", nargs="+", help="Color scheme")
    parser.add_argument("--output", default="./output", help="Output directory")
    parser.add_argument("--formats", nargs="+", default=["ldraw", "json"], help="Export formats")

    args = parser.parse_args()

    print("Web 3D LEGO Generator")
    print("=" * 60)
    print(f"Description: {args.description}")
    print(f"Category: {args.category}")
    print(f"Style: {args.style}")
    print(f"Max Parts: {args.max_parts}")
    print(f"Output: {args.output}")
    print("=" * 60)

    pipeline = Web3DLegoPipeline(output_dir=Path(args.output))

    result = pipeline.generate_from_description(
        description=args.description,
        category=args.category,
        style=args.style,
        max_parts=args.max_parts,
        color_scheme=args.colors,
        export_formats=args.formats
    )

    if result.success:
        print(f"\n✓ SUCCESS!")
        print(f"  Model: {result.model_name}")
        print(f"  Parts: {len(result.assembly.parts)}")
        print(f"  Steps: {len(result.instructions.steps)}")
        print(f"  Exports:")
        for fmt, path in result.exports.items():
            print(f"    - {fmt}: {path}")
    else:
        print(f"\n✗ FAILED: {result.error}")
        return 1

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
