"""
Custom LEGO Model Definition System

Provides JSON schema, validation, and template management for custom LEGO models.
Supports predefined templates for vehicles, buildings, characters, and more.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Literal
from enum import Enum
import json
from pathlib import Path
import jsonschema


# ============================================================================
# Model Definition Schema
# ============================================================================

MODEL_DEFINITION_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "LEGO Model Definition",
    "description": "Complete specification for a custom LEGO model",
    "type": "object",
    "required": ["name", "category", "specifications", "constraints"],
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "description": "Model name"
        },
        "category": {
            "type": "string",
            "enum": ["vehicle", "building", "character", "furniture", "landscape", "abstract"],
            "description": "Model category"
        },
        "description": {
            "type": "string",
            "maxLength": 500,
            "description": "Detailed model description"
        },
        "specifications": {
            "type": "object",
            "required": ["scale", "target_dimensions"],
            "properties": {
                "scale": {
                    "type": "string",
                    "pattern": "^(1:[0-9]+|minifig|micro|midi)$",
                    "description": "Scale ratio (e.g., '1:24', 'minifig')"
                },
                "target_dimensions": {
                    "type": "object",
                    "required": ["width_studs", "height_studs", "depth_studs"],
                    "properties": {
                        "width_studs": {"type": "integer", "minimum": 1, "maximum": 200},
                        "height_studs": {"type": "integer", "minimum": 1, "maximum": 200},
                        "depth_studs": {"type": "integer", "minimum": 1, "maximum": 200}
                    }
                },
                "style": {
                    "type": "string",
                    "enum": ["realistic", "stylized", "minimalist", "technical"],
                    "default": "realistic"
                },
                "color_palette": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20,
                    "description": "Allowed LEGO colors"
                },
                "part_budget": {
                    "type": "object",
                    "properties": {
                        "max_total_parts": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "max_unique_parts": {"type": "integer", "minimum": 1, "maximum": 500},
                        "prefer_common_parts": {"type": "boolean", "default": True}
                    }
                }
            }
        },
        "constraints": {
            "type": "object",
            "properties": {
                "structural": {
                    "type": "object",
                    "properties": {
                        "min_connection_overlap": {"type": "integer", "minimum": 1, "default": 2},
                        "require_stability": {"type": "boolean", "default": True},
                        "allow_floating_parts": {"type": "boolean", "default": False}
                    }
                },
                "functional": {
                    "type": "object",
                    "properties": {
                        "moving_parts": {"type": "boolean", "default": False},
                        "openable_features": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["doors", "hood", "trunk", "roof", "windows"]}
                        },
                        "minifig_accessible": {"type": "boolean", "default": False}
                    }
                },
                "aesthetic": {
                    "type": "object",
                    "properties": {
                        "smooth_surfaces": {"type": "boolean", "default": True},
                        "hidden_studs": {"type": "boolean", "default": False},
                        "symmetry": {
                            "type": "string",
                            "enum": ["none", "bilateral", "radial", "rotational"]
                        }
                    }
                }
            }
        },
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "type"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "priority": {"type": "string", "enum": ["required", "preferred", "optional"]},
                    "parameters": {"type": "object"}
                }
            }
        },
        "reference_images": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "view"],
                "properties": {
                    "path": {"type": "string"},
                    "view": {"type": "string", "enum": ["front", "side", "top", "perspective", "detail"]},
                    "weight": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0}
                }
            }
        },
        "metadata": {
            "type": "object",
            "properties": {
                "author": {"type": "string"},
                "version": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "difficulty": {"type": "string", "enum": ["beginner", "intermediate", "advanced", "expert"]},
                "estimated_build_time_minutes": {"type": "integer", "minimum": 5}
            }
        }
    }
}


# ============================================================================
# Model Templates
# ============================================================================

class ModelTemplate:
    """Predefined template for common model types"""

    @staticmethod
    def vehicle_sports_car(name: str = "Sports Car") -> dict:
        """Template for a sports car model"""
        return {
            "name": name,
            "category": "vehicle",
            "description": "High-performance sports car with aerodynamic design",
            "specifications": {
                "scale": "1:24",
                "target_dimensions": {
                    "width_studs": 8,
                    "height_studs": 6,
                    "depth_studs": 20
                },
                "style": "realistic",
                "color_palette": ["Bright Red", "Black", "White", "Dark Gray", "Trans-Clear"],
                "part_budget": {
                    "max_total_parts": 400,
                    "max_unique_parts": 80,
                    "prefer_common_parts": True
                }
            },
            "constraints": {
                "structural": {
                    "min_connection_overlap": 2,
                    "require_stability": True,
                    "allow_floating_parts": False
                },
                "functional": {
                    "moving_parts": True,
                    "openable_features": ["doors", "hood"],
                    "minifig_accessible": False
                },
                "aesthetic": {
                    "smooth_surfaces": True,
                    "hidden_studs": True,
                    "symmetry": "bilateral"
                }
            },
            "features": [
                {"name": "wheels", "type": "wheel_assembly", "priority": "required", "parameters": {"count": 4, "size": "medium"}},
                {"name": "spoiler", "type": "aerodynamic", "priority": "preferred", "parameters": {"position": "rear"}},
                {"name": "headlights", "type": "lighting", "priority": "required", "parameters": {"transparent": True}},
                {"name": "windows", "type": "glazing", "priority": "preferred", "parameters": {"tinted": True}}
            ],
            "metadata": {
                "difficulty": "intermediate",
                "estimated_build_time_minutes": 90,
                "tags": ["vehicle", "sports", "car", "racing"]
            }
        }

    @staticmethod
    def building_house(name: str = "Family House") -> dict:
        """Template for a residential building"""
        return {
            "name": name,
            "category": "building",
            "description": "Two-story family house with garden",
            "specifications": {
                "scale": "minifig",
                "target_dimensions": {
                    "width_studs": 32,
                    "height_studs": 24,
                    "depth_studs": 32
                },
                "style": "stylized",
                "color_palette": ["White", "Bright Red", "Brown", "Tan", "Bright Green", "Trans-Clear"],
                "part_budget": {
                    "max_total_parts": 800,
                    "max_unique_parts": 120,
                    "prefer_common_parts": True
                }
            },
            "constraints": {
                "structural": {
                    "min_connection_overlap": 2,
                    "require_stability": True,
                    "allow_floating_parts": False
                },
                "functional": {
                    "moving_parts": True,
                    "openable_features": ["doors", "windows", "roof"],
                    "minifig_accessible": True
                },
                "aesthetic": {
                    "smooth_surfaces": True,
                    "hidden_studs": False,
                    "symmetry": "bilateral"
                }
            },
            "features": [
                {"name": "walls", "type": "structure", "priority": "required", "parameters": {"floors": 2}},
                {"name": "roof", "type": "covering", "priority": "required", "parameters": {"style": "gabled"}},
                {"name": "windows", "type": "opening", "priority": "required", "parameters": {"count": 8}},
                {"name": "door", "type": "entrance", "priority": "required", "parameters": {"count": 1}},
                {"name": "chimney", "type": "detail", "priority": "optional", "parameters": {}}
            ],
            "metadata": {
                "difficulty": "advanced",
                "estimated_build_time_minutes": 180,
                "tags": ["building", "house", "residential", "architecture"]
            }
        }

    @staticmethod
    def character_minifig(name: str = "Character") -> dict:
        """Template for a character/minifigure build"""
        return {
            "name": name,
            "category": "character",
            "description": "Character model compatible with minifigure scale",
            "specifications": {
                "scale": "minifig",
                "target_dimensions": {
                    "width_studs": 4,
                    "height_studs": 10,
                    "depth_studs": 2
                },
                "style": "stylized",
                "color_palette": ["Bright Yellow", "Bright Red", "Bright Blue", "Black", "White"],
                "part_budget": {
                    "max_total_parts": 150,
                    "max_unique_parts": 40,
                    "prefer_common_parts": True
                }
            },
            "constraints": {
                "structural": {
                    "min_connection_overlap": 2,
                    "require_stability": True,
                    "allow_floating_parts": False
                },
                "functional": {
                    "moving_parts": True,
                    "openable_features": [],
                    "minifig_accessible": False
                },
                "aesthetic": {
                    "smooth_surfaces": True,
                    "hidden_studs": False,
                    "symmetry": "bilateral"
                }
            },
            "features": [
                {"name": "head", "type": "body_part", "priority": "required", "parameters": {}},
                {"name": "torso", "type": "body_part", "priority": "required", "parameters": {}},
                {"name": "arms", "type": "body_part", "priority": "required", "parameters": {"articulated": True}},
                {"name": "legs", "type": "body_part", "priority": "required", "parameters": {"articulated": True}}
            ],
            "metadata": {
                "difficulty": "beginner",
                "estimated_build_time_minutes": 30,
                "tags": ["character", "minifig", "figure"]
            }
        }

    @staticmethod
    def get_all_templates() -> Dict[str, dict]:
        """Get all available templates"""
        return {
            "vehicle_sports_car": ModelTemplate.vehicle_sports_car(),
            "building_house": ModelTemplate.building_house(),
            "character_minifig": ModelTemplate.character_minifig()
        }


# ============================================================================
# Validation System
# ============================================================================

@dataclass
class ValidationError:
    """Validation error details"""
    field: str
    message: str
    severity: Literal["error", "warning"]


@dataclass
class ValidationResult:
    """Result of model definition validation"""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)

    def add_error(self, field: str, message: str):
        """Add a validation error"""
        self.errors.append(ValidationError(field, message, "error"))
        self.is_valid = False

    def add_warning(self, field: str, message: str):
        """Add a validation warning"""
        self.warnings.append(ValidationError(field, message, "warning"))


class ModelDefinitionValidator:
    """Validates custom model definitions"""

    def __init__(self, schema: dict = MODEL_DEFINITION_SCHEMA):
        """Initialize with JSON schema"""
        self.schema = schema

    def validate(self, definition: dict) -> ValidationResult:
        """
        Validate a model definition.

        Args:
            definition: Model definition dictionary

        Returns:
            ValidationResult with errors and warnings
        """
        result = ValidationResult(is_valid=True)

        # Schema validation
        try:
            jsonschema.validate(instance=definition, schema=self.schema)
        except jsonschema.ValidationError as e:
            result.add_error(str(e.path), e.message)
            return result

        # Additional custom validation
        self._validate_dimensions(definition, result)
        self._validate_part_budget(definition, result)
        self._validate_color_palette(definition, result)
        self._validate_features(definition, result)

        return result

    def _validate_dimensions(self, definition: dict, result: ValidationResult):
        """Validate dimensional constraints"""
        specs = definition.get("specifications", {})
        dims = specs.get("target_dimensions", {})

        width = dims.get("width_studs", 0)
        height = dims.get("height_studs", 0)
        depth = dims.get("depth_studs", 0)

        total_volume = width * height * depth

        # Check reasonable bounds. A standard modular building footprint is
        # 32 x 32 studs, and figures reach ~30 studs tall, so the ceiling has to
        # sit well above a single-storey house rather than at furniture scale.
        if total_volume < 8:
            result.add_warning("specifications.target_dimensions", "Model is very small (< 8 stud³)")
        elif total_volume > 150000:
            result.add_error("specifications.target_dimensions", "Model is too large (> 150000 stud³)")
        elif total_volume > 50000:
            result.add_warning("specifications.target_dimensions", "Model is very large (> 50000 stud³); build time and part count grow quickly")

        # Check aspect ratios
        if max(width, depth) / min(width, depth) > 10:
            result.add_warning("specifications.target_dimensions", "Extreme aspect ratio may be unstable")

    def _validate_part_budget(self, definition: dict, result: ValidationResult):
        """Validate part count constraints"""
        specs = definition.get("specifications", {})
        budget = specs.get("part_budget", {})

        max_total = budget.get("max_total_parts", 0)
        max_unique = budget.get("max_unique_parts", 0)

        if max_unique > max_total:
            result.add_error("specifications.part_budget", "max_unique_parts cannot exceed max_total_parts")

        # Warn about extreme budgets
        if max_total < 50:
            result.add_warning("specifications.part_budget", "Very low part count may limit detail")
        elif max_total > 5000:
            result.add_warning("specifications.part_budget", "Very high part count may be difficult to build")

    def _validate_color_palette(self, definition: dict, result: ValidationResult):
        """Validate color constraints"""
        specs = definition.get("specifications", {})
        palette = specs.get("color_palette", [])

        # Known LEGO colors (subset)
        known_colors = {
            "Bright Red", "Bright Blue", "Bright Yellow", "Black", "White",
            "Dark Gray", "Medium Stone Gray", "Bright Green", "Dark Orange",
            "Tan", "Dark Tan", "Brown", "Trans-Clear", "Trans-Red", "Trans-Blue"
        }

        for color in palette:
            if color not in known_colors:
                result.add_warning("specifications.color_palette", f"Unknown color '{color}' - may not render correctly")

        if len(palette) > 10:
            result.add_warning("specifications.color_palette", "Large color palette may look busy")

    def _validate_features(self, definition: dict, result: ValidationResult):
        """Validate feature definitions"""
        features = definition.get("features", [])

        # Check for required features by category
        category = definition.get("category")
        if category == "vehicle":
            has_wheels = any(f.get("type") == "wheel_assembly" for f in features)
            if not has_wheels:
                result.add_error("features", "Vehicle models must include wheels")

        # Check feature consistency
        functional = definition.get("constraints", {}).get("functional", {})
        openable = functional.get("openable_features", [])

        for feature_name in openable:
            has_feature = any(feature_name in f.get("name", "").lower() for f in features)
            if not has_feature:
                result.add_warning("features", f"Openable feature '{feature_name}' not defined in features list")


# ============================================================================
# Model Definition Manager
# ============================================================================

class ModelDefinitionManager:
    """Manages custom model definitions"""

    def __init__(self, storage_dir: Path = None):
        """
        Initialize manager with storage directory.

        Args:
            storage_dir: Directory to store model definitions (default: ./models)
        """
        self.storage_dir = storage_dir or Path("./models")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.validator = ModelDefinitionValidator()

    def save_definition(self, definition: dict, filename: str = None) -> Path:
        """
        Save a model definition to disk.

        Args:
            definition: Model definition dictionary
            filename: Optional filename (default: based on model name)

        Returns:
            Path to saved file
        """
        # Validate before saving
        validation = self.validator.validate(definition)
        if not validation.is_valid:
            raise ValueError(f"Invalid model definition: {validation.errors[0].message}")

        # Generate filename from model name if not provided
        if filename is None:
            name = definition.get("name", "unnamed")
            filename = name.lower().replace(" ", "_") + ".json"

        filepath = self.storage_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(definition, f, indent=2, ensure_ascii=False)

        return filepath

    def load_definition(self, filename: str) -> dict:
        """Load a model definition from disk"""
        filepath = self.storage_dir / filename

        if not filepath.exists():
            raise FileNotFoundError(f"Model definition not found: {filepath}")

        with open(filepath, "r", encoding="utf-8") as f:
            definition = json.load(f)

        return definition

    def list_definitions(self) -> List[Dict[str, Any]]:
        """List all stored model definitions"""
        definitions = []

        for filepath in self.storage_dir.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    definition = json.load(f)

                definitions.append({
                    "filename": filepath.name,
                    "name": definition.get("name", "Unknown"),
                    "category": definition.get("category", "unknown"),
                    "description": definition.get("description", "")[:100]
                })
            except Exception:
                continue

        return definitions

    def create_from_template(self, template_name: str, custom_name: str = None) -> dict:
        """
        Create a model definition from a template.

        Args:
            template_name: Name of the template (e.g., "vehicle_sports_car")
            custom_name: Optional custom name for the model

        Returns:
            Model definition dictionary
        """
        templates = ModelTemplate.get_all_templates()

        if template_name not in templates:
            raise ValueError(f"Unknown template: {template_name}. Available: {list(templates.keys())}")

        definition = templates[template_name].copy()

        if custom_name:
            definition["name"] = custom_name

        return definition


# ============================================================================
# Convenience Functions
# ============================================================================

def validate_model_definition(definition: dict) -> ValidationResult:
    """Quick validation function"""
    validator = ModelDefinitionValidator()
    return validator.validate(definition)


def create_model_from_template(template_name: str, name: str = None) -> dict:
    """Quick template creation function"""
    manager = ModelDefinitionManager()
    return manager.create_from_template(template_name, name)


if __name__ == "__main__":
    print("Custom Model Definition System - Test")
    print("=" * 60)

    # Test 1: Create from template
    print("\n1. Creating sports car from template...")
    sports_car = create_model_from_template("vehicle_sports_car", "My Custom Sports Car")
    print(f"✓ Created: {sports_car['name']}")
    print(f"  Category: {sports_car['category']}")
    print(f"  Dimensions: {sports_car['specifications']['target_dimensions']}")

    # Test 2: Validate definition
    print("\n2. Validating definition...")
    result = validate_model_definition(sports_car)
    print(f"✓ Valid: {result.is_valid}")
    print(f"  Errors: {len(result.errors)}")
    print(f"  Warnings: {len(result.warnings)}")

    # Test 3: Save and load
    print("\n3. Saving definition...")
    manager = ModelDefinitionManager(Path("./test_models"))
    filepath = manager.save_definition(sports_car)
    print(f"✓ Saved to: {filepath}")

    loaded = manager.load_definition(filepath.name)
    print(f"✓ Loaded: {loaded['name']}")

    # Test 4: List available templates
    print("\n4. Available templates:")
    templates = ModelTemplate.get_all_templates()
    for name in templates.keys():
        print(f"  - {name}")

    print("\n✓ All tests passed!")
