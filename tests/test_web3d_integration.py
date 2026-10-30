"""
Test Suite for Web 3D LEGO Generation System

Comprehensive tests for LLM generation, validation, and pipeline integration.
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile

from car2lego3d.llm_instruction_generator import (
    LLMInstructionGenerator,
    ModelParameters,
    ModelCategory,
    ModelStyle,
    generate_lego_model
)
from car2lego3d.custom_model_system import (
    ModelDefinitionValidator,
    ModelDefinitionManager,
    ModelTemplate,
    validate_model_definition,
    create_model_from_template
)
from car2lego3d.web3d_prototype import (
    Assembly,
    PlacedPart,
    LEGOBrick,
    Vector3,
    BrickLibrary,
    BrickCategory,
    LDrawExporter
)
from car2lego3d.web3d_pipeline import (
    Web3DLegoPipeline,
    PipelineResult
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def sample_brick():
    """Create a sample LEGO brick for testing"""
    return BrickLibrary.get_brick("3001")  # 2x4 brick


@pytest.fixture
def sample_assembly(sample_brick):
    """Create a sample assembly for testing"""
    parts = [
        PlacedPart(0, sample_brick, Vector3(0, 0, 0), Vector3(0, 0, 0), "Bright Red"),
        PlacedPart(1, sample_brick, Vector3(0, 1, 0), Vector3(0, 0, 0), "Bright Blue"),
        PlacedPart(2, sample_brick, Vector3(4, 0, 0), Vector3(0, 0, 0), "Black")
    ]
    return Assembly("Test Model", parts, None)


@pytest.fixture
def temp_output_dir():
    """Create temporary output directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ============================================================================
# Test Custom Model System
# ============================================================================

class TestModelTemplates:
    """Test predefined model templates"""

    def test_vehicle_template(self):
        """Test vehicle sports car template"""
        template = ModelTemplate.vehicle_sports_car("Test Car")

        assert template["name"] == "Test Car"
        assert template["category"] == "vehicle"
        assert "specifications" in template
        assert "constraints" in template
        assert "features" in template

    def test_building_template(self):
        """Test building house template"""
        template = ModelTemplate.building_house("Test House")

        assert template["name"] == "Test House"
        assert template["category"] == "building"
        assert template["specifications"]["scale"] == "minifig"

    def test_character_template(self):
        """Test character template"""
        template = ModelTemplate.character_minifig("Test Character")

        assert template["name"] == "Test Character"
        assert template["category"] == "character"


class TestModelValidation:
    """Test model definition validation"""

    def test_valid_definition(self):
        """Test validation of a valid model definition"""
        definition = ModelTemplate.vehicle_sports_car()
        result = validate_model_definition(definition)

        assert result.is_valid
        assert len(result.errors) == 0

    def test_invalid_dimensions(self):
        """Test validation with invalid dimensions"""
        definition = ModelTemplate.vehicle_sports_car()
        definition["specifications"]["target_dimensions"]["width_studs"] = 10000

        result = validate_model_definition(definition)
        assert not result.is_valid

    def test_part_budget_validation(self):
        """Test part budget validation"""
        definition = ModelTemplate.vehicle_sports_car()
        definition["specifications"]["part_budget"]["max_unique_parts"] = 1000
        definition["specifications"]["part_budget"]["max_total_parts"] = 100

        result = validate_model_definition(definition)
        assert not result.is_valid
        assert any("max_unique_parts" in e.field for e in result.errors)


class TestModelManager:
    """Test model definition management"""

    def test_create_from_template(self, temp_output_dir):
        """Test creating model from template"""
        manager = ModelDefinitionManager(temp_output_dir)
        definition = manager.create_from_template("vehicle_sports_car", "My Car")

        assert definition["name"] == "My Car"
        assert definition["category"] == "vehicle"

    def test_save_and_load(self, temp_output_dir):
        """Test saving and loading definitions"""
        manager = ModelDefinitionManager(temp_output_dir)
        definition = ModelTemplate.vehicle_sports_car("Test Model")

        # Save
        filepath = manager.save_definition(definition)
        assert filepath.exists()

        # Load
        loaded = manager.load_definition(filepath.name)
        assert loaded["name"] == "Test Model"

    def test_list_definitions(self, temp_output_dir):
        """Test listing stored definitions"""
        manager = ModelDefinitionManager(temp_output_dir)

        # Save multiple definitions
        manager.save_definition(ModelTemplate.vehicle_sports_car("Car 1"))
        manager.save_definition(ModelTemplate.building_house("House 1"))

        definitions = manager.list_definitions()
        assert len(definitions) == 2


# ============================================================================
# Test Web 3D Prototype
# ============================================================================

class TestLEGOBrick:
    """Test LEGO brick operations"""

    def test_brick_library(self):
        """Test brick library access"""
        brick = BrickLibrary.get_brick("3001")

        assert brick is not None
        assert brick.lego_id == "3001"
        assert brick.category == BrickCategory.BRICK

    def test_bounding_box(self, sample_brick):
        """Test brick bounding box calculation"""
        bbox = sample_brick.get_bounding_box()

        assert bbox.min.x < bbox.max.x
        assert bbox.min.y < bbox.max.y
        assert bbox.min.z < bbox.max.z


class TestAssembly:
    """Test assembly operations"""

    def test_assembly_creation(self, sample_assembly):
        """Test creating an assembly"""
        assert len(sample_assembly.parts) == 3
        assert sample_assembly.name == "Test Model"

    def test_assembly_validation(self, sample_assembly):
        """Test assembly structural validation"""
        report = sample_assembly.validate()

        assert report is not None
        assert hasattr(report, "is_valid")
        assert hasattr(report, "structural_score")

    def test_ldraw_export(self, sample_assembly):
        """Test LDraw export"""
        ldraw = LDrawExporter.export(sample_assembly)

        assert "0 Name:" in ldraw
        assert "1 4 " in ldraw  # Bright Red = LDraw colour 4
        assert "3001.dat" in ldraw

    def test_json_serialization(self, sample_assembly, temp_output_dir):
        """Test JSON save and load"""
        filepath = temp_output_dir / "test_assembly.json"
        sample_assembly.save_to_file(filepath)

        assert filepath.exists()

        loaded = Assembly.load_from_file(filepath)
        assert loaded.name == sample_assembly.name
        assert len(loaded.parts) == len(sample_assembly.parts)


# ============================================================================
# Test LLM Integration (with mocking)
# ============================================================================

class TestLLMGenerator:
    """Test LLM instruction generator"""

    @patch('car2lego3d.llm_instruction_generator.OpenAI')
    def test_generate_instructions(self, mock_openai):
        """Test LLM instruction generation (mocked)"""
        # Mock LLM response
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({
            "reasoning": "Test reasoning",
            "parts_list": [],
            "assembly_steps": [
                {
                    "step_number": 1,
                    "description": "Build base",
                    "parts": [
                        {"brick_id": "3001", "position": [0, 0, 0], "rotation": [0, 0, 0], "color": "Bright Red"}
                    ]
                }
            ],
            "dimensions": {"width": 8, "height": 4, "depth": 16},
            "total_parts": 1
        })
        mock_response.usage.total_tokens = 500

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        # Test generation
        generator = LLMInstructionGenerator()
        params = ModelParameters(
            category=ModelCategory.VEHICLE,
            description="Test sports car"
        )

        result = generator.generate_instructions(params)

        assert result.success
        assert result.assembly is not None
        assert len(result.assembly.parts) >= 1

    def test_model_parameters(self):
        """Test model parameter creation"""
        params = ModelParameters(
            category=ModelCategory.VEHICLE,
            description="Red sports car",
            style=ModelStyle.REALISTIC,
            max_parts=300
        )

        assert params.category == ModelCategory.VEHICLE
        assert params.description == "Red sports car"
        assert params.max_parts == 300


# ============================================================================
# Test Pipeline Integration
# ============================================================================

class TestPipeline:
    """Test end-to-end pipeline"""

    @patch('car2lego3d.llm_instruction_generator.OpenAI')
    def test_pipeline_generation(self, mock_openai, temp_output_dir):
        """Test complete pipeline execution (mocked LLM)"""
        # Mock LLM response
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({
            "reasoning": "Simple test model",
            "parts_list": [],
            "assembly_steps": [
                {
                    "step_number": 1,
                    "description": "Build foundation",
                    "parts": [
                        {"brick_id": "3001", "position": [0, 0, 0], "rotation": [0, 0, 0], "color": "Bright Red"},
                        {"brick_id": "3001", "position": [4, 0, 0], "rotation": [0, 0, 0], "color": "Black"}
                    ]
                }
            ],
            "dimensions": {"width": 8, "height": 4, "depth": 4},
            "total_parts": 2
        })
        mock_response.usage.total_tokens = 300

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        # Run pipeline
        pipeline = Web3DLegoPipeline(output_dir=temp_output_dir)
        result = pipeline.generate_from_description(
            description="Simple test model",
            category="vehicle",
            max_parts=100
        )

        assert result.success
        assert result.assembly is not None
        assert len(result.exports) > 0
        assert "json" in result.exports
        assert "ldraw" in result.exports

    def test_pipeline_output_structure(self, temp_output_dir):
        """Test pipeline output directory structure"""
        pipeline = Web3DLegoPipeline(output_dir=temp_output_dir)

        assert pipeline.output_dir.exists()
        assert pipeline.output_dir == temp_output_dir


# ============================================================================
# Performance Tests
# ============================================================================

class TestPerformance:
    """Test performance characteristics"""

    def test_assembly_size_limits(self):
        """Test handling of large assemblies"""
        brick = BrickLibrary.get_brick("3001")

        # Create assembly with many parts
        parts = [
            PlacedPart(i, brick, Vector3(i % 10, i // 10, 0), Vector3(0, 0, 0), "Bright Red")
            for i in range(100)
        ]

        assembly = Assembly("Large Model", parts, None)
        assert len(assembly.parts) == 100

        # Should still validate reasonably fast
        report = assembly.validate()
        assert report is not None

    def test_export_performance(self, sample_assembly):
        """Test export performance"""
        import time

        start = time.time()
        ldraw = LDrawExporter.export(sample_assembly)
        duration = time.time() - start

        # Should export in under 1 second for small models
        assert duration < 1.0
        assert len(ldraw) > 0


# ============================================================================
# Integration Test Suite
# ============================================================================

def run_acceptance_tests():
    """Run complete acceptance test suite"""
    print("Web 3D LEGO Generation - Acceptance Tests")
    print("=" * 60)

    results = {
        "passed": 0,
        "failed": 0,
        "errors": []
    }

    # Test 1: Template creation
    print("\n[1/6] Testing template system...")
    try:
        car = create_model_from_template("vehicle_sports_car", "Test Car")
        assert car["name"] == "Test Car"
        results["passed"] += 1
        print("✓ Template system working")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"Template test: {e}")
        print(f"✗ Template test failed: {e}")

    # Test 2: Validation
    print("\n[2/6] Testing validation system...")
    try:
        validation = validate_model_definition(car)
        assert validation.is_valid
        results["passed"] += 1
        print("✓ Validation system working")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"Validation test: {e}")
        print(f"✗ Validation test failed: {e}")

    # Test 3: Brick library
    print("\n[3/6] Testing brick library...")
    try:
        brick = BrickLibrary.get_brick("3001")
        assert brick is not None
        results["passed"] += 1
        print("✓ Brick library working")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"Brick library test: {e}")
        print(f"✗ Brick library test failed: {e}")

    # Test 4: Assembly creation
    print("\n[4/6] Testing assembly system...")
    try:
        brick = BrickLibrary.get_brick("3001")
        parts = [PlacedPart(0, brick, Vector3(0, 0, 0), Vector3(0, 0, 0), "Bright Red")]
        assembly = Assembly("Test", parts, None)
        assert len(assembly.parts) == 1
        results["passed"] += 1
        print("✓ Assembly system working")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"Assembly test: {e}")
        print(f"✗ Assembly test failed: {e}")

    # Test 5: LDraw export
    print("\n[5/6] Testing export system...")
    try:
        ldraw = LDrawExporter.export(assembly)
        assert "3001.dat" in ldraw
        results["passed"] += 1
        print("✓ Export system working")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"Export test: {e}")
        print(f"✗ Export test failed: {e}")

    # Test 6: Pipeline structure
    print("\n[6/6] Testing pipeline structure...")
    try:
        pipeline = Web3DLegoPipeline()
        assert pipeline.llm_generator is not None
        assert pipeline.model_manager is not None
        results["passed"] += 1
        print("✓ Pipeline structure working")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"Pipeline test: {e}")
        print(f"✗ Pipeline test failed: {e}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Acceptance Tests Complete")
    print(f"Passed: {results['passed']}/6")
    print(f"Failed: {results['failed']}/6")

    if results["errors"]:
        print("\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")

    return results["failed"] == 0


if __name__ == "__main__":
    # Run acceptance tests
    success = run_acceptance_tests()
    exit(0 if success else 1)
