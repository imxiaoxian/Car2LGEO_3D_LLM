"""
Web 3D LEGO Model Generation - Python Backend

Core geometry and validation engine for pure software LEGO generation.
Handles brick definitions, assembly instructions, and constraint validation.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Literal
from enum import Enum
import json
from abc import ABC, abstractmethod
import numpy as np
from datetime import datetime


# ============================================================================
# Data Structures
# ============================================================================

class ConnectionType(str, Enum):
    """LEGO brick connection types"""
    STUD = "stud"           # Standard male stud (4.8mm diameter)
    TUBE = "tube"           # Female tube (accepts stud)
    AXLE = "axle"           # Axle hole (for wheels/gears)
    AXLE_HOLE = "axle_hole"
    PIN = "pin"             # Thin pin connector
    CLIP = "clip"           # Clip connector
    SLOPE = "slope"         # Slope/tile with connections
    MAGNET = "magnet"       # Magnetic connection
    RAIL = "rail"           # Pin rail system


class BrickCategory(str, Enum):
    """Categories of LEGO bricks"""
    BRICK = "brick"
    PLATE = "plate"
    SLOPE = "slope"
    WHEEL = "wheel"
    DOOR = "door"
    WINDOW = "window"
    MINIFIG_PART = "minifig_part"
    TECHNIC = "technic"
    CONNECTOR = "connector"
    OTHER = "other"


@dataclass
class Vector3:
    """3D vector for positions and dimensions"""
    x: float
    y: float
    z: float

    def __add__(self, other: 'Vector3') -> 'Vector3':
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: 'Vector3') -> 'Vector3':
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> 'Vector3':
        return Vector3(self.x * scalar, self.y * scalar, self.z * scalar)

    def distance_to(self, other: 'Vector3') -> float:
        """Euclidean distance to another vector"""
        return np.sqrt((self.x - other.x)**2 + (self.y - other.y)**2 + (self.z - other.z)**2)

    def to_list(self) -> List[float]:
        return [self.x, self.y, self.z]

    @staticmethod
    def from_list(lst: List[float]) -> 'Vector3':
        return Vector3(lst[0], lst[1], lst[2])


@dataclass
class BoundingBox:
    """Axis-aligned bounding box"""
    min: Vector3
    max: Vector3

    def intersects(self, other: 'BoundingBox', tolerance: float = 0.01) -> bool:
        """Check whether two boxes genuinely overlap.

        Two bricks that merely touch (one ends exactly where the next begins)
        are correctly joined, not colliding, so the overlap must exceed
        ``tolerance`` on all three axes before this reports True.
        """
        return (
            self.min.x + tolerance < other.max.x and other.min.x + tolerance < self.max.x and
            self.min.y + tolerance < other.max.y and other.min.y + tolerance < self.max.y and
            self.min.z + tolerance < other.max.z and other.min.z + tolerance < self.max.z
        )

    def contains_point(self, point: Vector3, tolerance: float = 0.01) -> bool:
        """Check if point is inside AABB"""
        return (
            self.min.x - tolerance <= point.x <= self.max.x + tolerance and
            self.min.y - tolerance <= point.y <= self.max.y + tolerance and
            self.min.z - tolerance <= point.z <= self.max.z + tolerance
        )

    def to_dict(self) -> dict:
        return {
            "min": self.min.to_list(),
            "max": self.max.to_list()
        }


@dataclass
class BrickConnector:
    """Connection point on a LEGO brick"""
    type: ConnectionType
    position: Vector3              # Local coordinate (relative to brick origin)
    normal: Vector3                # Direction connector faces (unit vector)
    gender: Literal["male", "female", "both"]
    strength: float = 1.0         # Connection strength (0.0-1.0)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "position": self.position.to_list(),
            "normal": self.normal.to_list(),
            "gender": self.gender,
            "strength": self.strength
        }

    @staticmethod
    def from_dict(d: dict) -> 'BrickConnector':
        return BrickConnector(
            type=ConnectionType(d["type"]),
            position=Vector3.from_list(d["position"]),
            normal=Vector3.from_list(d["normal"]),
            gender=d["gender"],
            strength=d.get("strength", 1.0)
        )


@dataclass
class LEGOBrick:
    """Definition of a LEGO brick type"""
    lego_id: str                   # Official LEGO part ID (e.g., "3003")
    name: str                      # Part name (e.g., "Brick 1x2")
    category: BrickCategory
    dimensions: Vector3            # Size in stud units [width, height, depth]
    mass_grams: float              # Estimated physical weight
    connectors: List[BrickConnector] = field(default_factory=list)
    official_colors: List[str] = field(default_factory=lambda: ["Bright Red", "Bright Blue", "Bright Yellow"])
    geometry_ldraw_url: str = ""   # Path to LDraw DAT file

    def get_bounding_box(self) -> BoundingBox:
        """Get axis-aligned bounding box (in stud units)"""
        # Studs are 8mm apart, brick is centered at origin
        half_width = self.dimensions.x / 2
        half_depth = self.dimensions.z / 2
        return BoundingBox(
            min=Vector3(-half_width, 0, -half_depth),
            max=Vector3(half_width, self.dimensions.y, half_depth)
        )

    def get_connectors_of_type(self, conn_type: ConnectionType) -> List[BrickConnector]:
        """Get all connectors of a specific type"""
        return [c for c in self.connectors if c.type == conn_type]

    def to_dict(self) -> dict:
        return {
            "lego_id": self.lego_id,
            "name": self.name,
            "category": self.category.value,
            "dimensions": self.dimensions.to_list(),
            "mass_grams": self.mass_grams,
            "connectors": [c.to_dict() for c in self.connectors],
            "official_colors": self.official_colors,
            "geometry_ldraw_url": self.geometry_ldraw_url
        }

    @staticmethod
    def from_dict(d: dict) -> 'LEGOBrick':
        return LEGOBrick(
            lego_id=d["lego_id"],
            name=d["name"],
            category=BrickCategory(d["category"]),
            dimensions=Vector3.from_list(d["dimensions"]),
            mass_grams=d["mass_grams"],
            connectors=[BrickConnector.from_dict(c) for c in d.get("connectors", [])],
            official_colors=d.get("official_colors", []),
            geometry_ldraw_url=d.get("geometry_ldraw_url", "")
        )


# ============================================================================
# Assembly Definition
# ============================================================================

@dataclass
class PlacedPart:
    """Instance of a LEGO brick in an assembly"""
    instance_id: int
    brick_definition: LEGOBrick
    position: Vector3              # World coordinates (in studs)
    rotation: Vector3              # Euler angles [x, y, z] in degrees
    color: str                     # LEGO color name
    connected_to: List[int] = field(default_factory=list)  # Instance IDs

    def get_bounding_box(self) -> BoundingBox:
        """Get AABB in world space (simplified - no rotation)"""
        local_aabb = self.brick_definition.get_bounding_box()
        return BoundingBox(
            min=self.position + local_aabb.min,
            max=self.position + local_aabb.max
        )

    def check_collision(self, other: 'PlacedPart', tolerance: float = 0.1) -> bool:
        """Check if overlapping with another part (simple AABB test)"""
        return self.get_bounding_box().intersects(other.get_bounding_box(), tolerance)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "brick_id": self.brick_definition.lego_id,
            "position": self.position.to_list(),
            "rotation": self.rotation.to_list(),
            "color": self.color,
            "connected_to": self.connected_to
        }


@dataclass
class AssemblyStep:
    """Single step in assembly instructions"""
    step_number: int
    description: str
    parts_to_add: List[int]        # Instance IDs
    reference_image_url: Optional[str] = None
    time_estimate_seconds: int = 30
    difficulty_level: Literal["easy", "medium", "hard"] = "medium"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AssemblyInstructions:
    """Complete assembly instructions"""
    vehicle_name: str
    vehicle_specs: Dict               # Original vehicle specs from LLM
    total_parts: int
    bom: Dict[str, int]              # Part ID → quantity
    estimated_time_minutes: int
    steps: List[AssemblyStep] = field(default_factory=list)
    confidence_score: float = 0.0    # LLM confidence (0.0-1.0)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "vehicle_name": self.vehicle_name,
            "vehicle_specs": self.vehicle_specs,
            "total_parts": self.total_parts,
            "bom": self.bom,
            "estimated_time_minutes": self.estimated_time_minutes,
            "steps": [s.to_dict() for s in self.steps],
            "confidence_score": self.confidence_score,
            "warnings": self.warnings
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @staticmethod
    def from_dict(d: dict) -> 'AssemblyInstructions':
        return AssemblyInstructions(
            vehicle_name=d["vehicle_name"],
            vehicle_specs=d["vehicle_specs"],
            total_parts=d["total_parts"],
            bom=d["bom"],
            estimated_time_minutes=d["estimated_time_minutes"],
            steps=[AssemblyStep(**s) for s in d.get("steps", [])],
            confidence_score=d.get("confidence_score", 0.0),
            warnings=d.get("warnings", [])
        )

    @staticmethod
    def from_json(data: str) -> 'AssemblyInstructions':
        return AssemblyInstructions.from_dict(json.loads(data))


@dataclass
class Assembly:
    """Complete LEGO assembly"""
    name: str
    parts: List[PlacedPart]
    instructions: AssemblyInstructions
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict = field(default_factory=dict)

    def validate(self) -> 'ValidationReport':
        """Validate assembly constraints"""
        return ValidationReport.validate(self)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "parts": [p.to_dict() for p in self.parts],
            "instructions": self.instructions.to_dict() if self.instructions else None,
            "created_at": self.created_at,
            "metadata": self.metadata
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def save_to_file(self, path) -> None:
        """Write the assembly to disk as JSON."""
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @staticmethod
    def load_from_file(path) -> 'Assembly':
        """Read an assembly written by save_to_file."""
        from pathlib import Path
        return Assembly.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @staticmethod
    def from_dict(d: dict) -> 'Assembly':
        parts = []
        for part_data in d.get("parts", []):
            brick = BrickLibrary.get_brick(part_data["brick_id"])
            if brick is None:
                brick = BrickLibrary.get_brick("3001")
            parts.append(PlacedPart(
                instance_id=part_data["instance_id"],
                brick_definition=brick,
                position=Vector3.from_list(part_data["position"]),
                rotation=Vector3.from_list(part_data["rotation"]),
                color=part_data["color"],
                connected_to=part_data.get("connected_to", []),
            ))
        return Assembly(
            name=d["name"],
            parts=parts,
            instructions=(AssemblyInstructions.from_dict(d["instructions"])
                          if d.get("instructions") else None),
            created_at=d.get("created_at", datetime.now().isoformat()),
            metadata=d.get("metadata", {}),
        )

    def get_statistics(self) -> 'AssemblyStatistics':
        """Calculate assembly statistics"""
        colors = {}
        for part in self.parts:
            colors[part.color] = colors.get(part.color, 0) + 1

        connections = sum(len(p.connected_to) for p in self.parts) // 2

        return AssemblyStatistics(
            total_parts=len(self.parts),
            unique_brick_types=len(set(p.brick_definition.lego_id for p in self.parts)),
            colors_used=list(colors.keys()),
            color_distribution=colors,
            total_connections=connections,
            estimated_weight_grams=sum(p.brick_definition.mass_grams for p in self.parts)
        )


# ============================================================================
# Validation and Constraints
# ============================================================================

@dataclass
class ValidationIssue:
    """Single validation issue found"""
    issue_type: str              # "overlap", "unsupported_connection", etc.
    severity: Literal["error", "warning", "info"]
    part_ids: List[int]
    message: str
    suggestion: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    """Result of assembly validation"""
    is_valid: bool
    issues: List[ValidationIssue]
    total_connections: int = 0
    valid_connections: int = 0
    structural_score: float = 1.0  # 0.0-1.0

    @staticmethod
    def validate(assembly: Assembly) -> 'ValidationReport':
        """Perform complete validation"""
        issues = []

        # 1. Check for overlapping parts
        for i, part1 in enumerate(assembly.parts):
            for part2 in assembly.parts[i+1:]:
                if part1.check_collision(part2):
                    issues.append(ValidationIssue(
                        issue_type="overlap",
                        severity="error",
                        part_ids=[part1.instance_id, part2.instance_id],
                        message=f"Parts {part1.instance_id} and {part2.instance_id} overlap",
                        suggestion="Move one of the parts to avoid collision"
                    ))

        # 2. Check bounding box (assembly dimensions reasonable)
        all_bboxes = [p.get_bounding_box() for p in assembly.parts]
        if all_bboxes:
            min_x = min(b.min.x for b in all_bboxes)
            max_x = max(b.max.x for b in all_bboxes)
            min_z = min(b.min.z for b in all_bboxes)
            max_z = max(b.max.z for b in all_bboxes)

            # Check if assembly fits in reasonable bounds (not too large)
            if (max_x - min_x) > 100 or (max_z - min_z) > 100:
                issues.append(ValidationIssue(
                    issue_type="size_warning",
                    severity="warning",
                    part_ids=[],
                    message="Assembly is very large",
                    suggestion="Consider if scale is appropriate"
                ))

        # 3. Check color palette (max 12 colors for visual clarity)
        colors = set(p.color for p in assembly.parts)
        if len(colors) > 12:
            issues.append(ValidationIssue(
                issue_type="color_palette",
                severity="warning",
                part_ids=[],
                message=f"Assembly uses {len(colors)} colors (typically max 12 recommended)",
                suggestion="Consider simplifying color scheme"
            ))

        # Calculate structural score (simplified)
        structural_score = 1.0
        if issues:
            error_count = sum(1 for i in issues if i.severity == "error")
            structural_score = max(0.0, 1.0 - (error_count * 0.1))

        return ValidationReport(
            is_valid=not any(i.severity == "error" for i in issues),
            issues=issues,
            structural_score=structural_score
        )

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "issues": [i.to_dict() for i in self.issues],
            "total_connections": self.total_connections,
            "valid_connections": self.valid_connections,
            "structural_score": self.structural_score
        }


@dataclass
class AssemblyStatistics:
    """Statistics about an assembly"""
    total_parts: int
    unique_brick_types: int
    colors_used: List[str]
    color_distribution: Dict[str, int]
    total_connections: int
    estimated_weight_grams: float

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# Connection Validation
# ============================================================================

class ConnectionValidator:
    """Validates LEGO part connections"""

    # Compatibility matrix for connection types
    COMPATIBLE_CONNECTIONS = {
        (ConnectionType.STUD, ConnectionType.TUBE): 1.0,
        (ConnectionType.AXLE, ConnectionType.AXLE_HOLE): 1.0,
        (ConnectionType.PIN, ConnectionType.PIN): 1.0,
        (ConnectionType.CLIP, ConnectionType.SLOPE): 1.0,
    }

    @staticmethod
    def is_compatible(conn_type1: ConnectionType, conn_type2: ConnectionType) -> bool:
        """Check if two connector types can join"""
        key = (conn_type1, conn_type2)
        reverse_key = (conn_type2, conn_type1)
        return key in ConnectionValidator.COMPATIBLE_CONNECTIONS or reverse_key in ConnectionValidator.COMPATIBLE_CONNECTIONS

    @staticmethod
    def get_compatibility_strength(conn_type1: ConnectionType, conn_type2: ConnectionType) -> float:
        """Get connection strength (0.0-1.0) for two connector types"""
        key = (conn_type1, conn_type2)
        if key in ConnectionValidator.COMPATIBLE_CONNECTIONS:
            return ConnectionValidator.COMPATIBLE_CONNECTIONS[key]
        reverse_key = (conn_type2, conn_type1)
        if reverse_key in ConnectionValidator.COMPATIBLE_CONNECTIONS:
            return ConnectionValidator.COMPATIBLE_CONNECTIONS[reverse_key]
        return 0.0


# ============================================================================
# Predefined Common LEGO Bricks (Brick Library)
# ============================================================================

class BrickLibrary:
    """Standard LEGO brick definitions"""

    # Common brick types (simplified definitions)
    STANDARD_BRICKS = {
        "3003": LEGOBrick(
            lego_id="3003",
            name="Brick 2x2",
            category=BrickCategory.BRICK,
            dimensions=Vector3(2, 1.2, 2),
            mass_grams=2.4,
            connectors=[
                BrickConnector(ConnectionType.STUD, Vector3(0, 0.5, 0), Vector3(0, 1, 0), "male", 1.0),
                BrickConnector(ConnectionType.STUD, Vector3(0, 0.5, 1), Vector3(0, 1, 0), "male", 1.0),
                BrickConnector(ConnectionType.TUBE, Vector3(0, 0, 0), Vector3(0, -1, 0), "female", 1.0),
                BrickConnector(ConnectionType.TUBE, Vector3(0, 0, 1), Vector3(0, -1, 0), "female", 1.0),
            ],
            official_colors=["Bright Red", "Bright Blue", "Bright Yellow", "Black", "White"]
        ),
        "3001": LEGOBrick(
            lego_id="3001",
            name="Brick 2x4",
            category=BrickCategory.BRICK,
            dimensions=Vector3(4, 1.2, 2),
            mass_grams=3.2,
            connectors=[
                BrickConnector(ConnectionType.STUD, Vector3(-0.5, 0.5, -0.5), Vector3(0, 1, 0), "male"),
                BrickConnector(ConnectionType.STUD, Vector3(0.5, 0.5, -0.5), Vector3(0, 1, 0), "male"),
                BrickConnector(ConnectionType.STUD, Vector3(-0.5, 0.5, 0.5), Vector3(0, 1, 0), "male"),
                BrickConnector(ConnectionType.STUD, Vector3(0.5, 0.5, 0.5), Vector3(0, 1, 0), "male"),
            ],
            official_colors=["Bright Red", "Bright Blue"]
        ),
        "3002": LEGOBrick(
            lego_id="3002",
            name="Brick 2x3",
            category=BrickCategory.BRICK,
            dimensions=Vector3(3, 1.2, 2),
            mass_grams=4.6,
            official_colors=["Bright Red", "Bright Blue", "Bright Yellow", "Black", "White", "Dark Gray"]
        ),
        "2456": LEGOBrick(
            lego_id="2456",
            name="Wheel 43.2 x 26",
            category=BrickCategory.WHEEL,
            dimensions=Vector3(4.32, 2.6, 4.32),
            mass_grams=5.1,
            connectors=[
                BrickConnector(ConnectionType.AXLE, Vector3(0, 1.3, 0), Vector3(1, 0, 0), "female"),
            ],
            official_colors=["Black"]
        ),
    }

    @classmethod
    def get_brick(cls, lego_id: str) -> Optional[LEGOBrick]:
        """Get brick definition by ID"""
        part = str(lego_id).strip().lower()
        if part in cls.STANDARD_BRICKS:
            return cls.STANDARD_BRICKS[part]
        return None

    @classmethod
    def resolve_brick(cls, lego_id: str, catalogue=None) -> Optional[LEGOBrick]:
        """Resolve a brick, deriving a definition for real LDraw parts.

        The hand-written table only covers a handful of shapes, but reference
        models use hundreds of distinct parts. When a part exists in the LDraw
        library we synthesise a definition from its measured geometry so the
        rest of the pipeline (stud-based collision, export) keeps working.
        """
        part = str(lego_id).strip().lower()
        known = cls.STANDARD_BRICKS.get(part)
        if known is not None:
            return known
        if catalogue is not None and not catalogue.has(part):
            return None
        derived = cls._derived.get(part)
        if derived is not None:
            return derived
        derived = cls._derive_from_ldraw(part)
        if derived is not None:
            cls._derived[part] = derived
        return derived

    # Dimensions are cached per part ID; deriving one walks the LDraw file.
    _derived: Dict[str, LEGOBrick] = {}

    @classmethod
    def _derive_from_ldraw(cls, part: str) -> Optional[LEGOBrick]:
        """Measure an LDraw part and express it in stud units."""
        try:
            from .ldraw_catalogue import load_catalogue
            from .part_library import PartLibrary
        except Exception:
            return None
        catalogue = load_catalogue()
        root = next((r for r in catalogue.roots if (r / "parts").is_dir()), None)
        if root is None:
            return None
        try:
            library = PartLibrary(root)
            triangles, _ = library.triangles(part + ".dat")
        except Exception:
            return None
        if triangles is None or len(triangles) == 0:
            return None
        points = triangles.reshape(-1, 3)
        lo = points.min(axis=0)
        hi = points.max(axis=0)
        # LDraw LDU: 20 LDU per stud, 24 LDU per brick height.
        studs = (hi - lo) / 20.0
        width = max(studs[0], 0.1)
        depth = max(studs[2], 0.1)
        height = max(studs[1], 0.1) * 20.0 / 24.0
        category = BrickCategory.OTHER
        # Measured LDraw bounding boxes: a plate sits near 0.4-0.6 studs tall,
        # a standard brick near 1.2-1.4. The gap leaves room for lips and trim.
        if height <= 0.6:
            category = BrickCategory.PLATE
            pitch = 0.4
        elif height >= 0.85:
            category = BrickCategory.BRICK
            pitch = 1.2
        else:
            pitch = height
        return LEGOBrick(
            lego_id=part,
            name=f"LDraw part {part.upper()}",
            category=category,
            dimensions=Vector3(width, pitch, depth),
            mass_grams=round(max(0.4, width * depth * height * 0.9), 2),
            official_colors=["Bright Red", "Black", "White", "Dark Gray"],
        )

    @classmethod
    def list_bricks(cls) -> List[str]:
        """List all available brick IDs"""
        return list(cls.STANDARD_BRICKS.keys())

    @classmethod
    def get_bricks_by_category(cls, category: BrickCategory) -> List[LEGOBrick]:
        """Get all bricks in a category"""
        return [b for b in cls.STANDARD_BRICKS.values() if b.category == category]


# ============================================================================
# LDraw Export (Standard LEGO Format)
# ============================================================================

class LDrawExporter:
    """Export Assembly to LDraw format"""

    # LDraw standard colour codes
    COLOR_CODES = {
        "Bright Red": 4,
        "Bright Blue": 1,
        "Bright Yellow": 14,
        "Black": 0,
        "White": 15,
        "Dark Gray": 8,
        "Dark Bluish Gray": 72,
        "Light Bluish Gray": 71,
        "Bright Green": 2,
        "Dark Green": 288,
        "Tan": 19,
        "Dark Tan": 28,
        "Brown": 6,
        "Reddish Brown": 70,
        "Dark Orange": 484,
        "Sand Green": 378,
        "Trans-Clear": 47,
        "Trans-Black": 40,
        "Trans-Light Blue": 43,
        "Trans-Red": 41,
        "Trans-Yellow": 46,
        "Flat Silver": 179,
        "Pearl Gold": 297,
        "Light Gray": 7,
    }

    @staticmethod
    def export(assembly: Assembly) -> str:
        """Generate LDraw (.ldr) format string"""
        lines = []
        lines.append("0 Name: " + assembly.name)
        lines.append("0 Author: Web3D LEGO Generator")
        lines.append("0 Description: Auto-generated LEGO model")
        lines.append("")

        # Part references
        for part in assembly.parts:
            color_code = LDrawExporter.COLOR_CODES.get(part.color, 16)

            # LDraw command: 1 <color> <x> <y> <z> <a> <b> <c> <d> <e> <f> <g> <h> <i> <part>
            # (simplified 45-degree rotation matrix)
            x, y, z = part.position.x, part.position.y, part.position.z
            ldraw_line = f"1 {color_code} {x:.2f} {y:.2f} {z:.2f} 1 0 0 0 1 0 0 0 1 {part.brick_definition.lego_id}.dat"
            lines.append(ldraw_line)

        lines.append("0 STEP")
        return "\n".join(lines)


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    print("Web 3D LEGO Generation - Prototype Module")
    print("=" * 50)

    # Create sample parts
    brick_2x2 = BrickLibrary.get_brick("3001")
    brick_2x4 = BrickLibrary.get_brick("3002")

    # Create assembly
    parts = [
        PlacedPart(0, brick_2x2, Vector3(0, 0, 0), Vector3(0, 0, 0), "Bright Red"),
        PlacedPart(1, brick_2x4, Vector3(0, 1, 0), Vector3(0, 0, 0), "Bright Blue"),
    ]

    instructions = AssemblyInstructions(
        vehicle_name="Test Model",
        vehicle_specs={"scale": "1:12"},
        total_parts=2,
        bom={"3001": 1, "3002": 1},
        estimated_time_minutes=5
    )

    assembly = Assembly(
        name="Test Assembly",
        parts=parts,
        instructions=instructions
    )

    # Validate
    report = assembly.validate()
    print(f"Assembly valid: {report.is_valid}")
    print(f"Structural score: {report.structural_score}")
    print(f"Issues: {len(report.issues)}")

    # Export
    ldraw = LDrawExporter.export(assembly)
    print("\nLDraw Export Preview:")
    print(ldraw[:200] + "...")

    # Statistics
    stats = assembly.get_statistics()
    print(f"\nAssembly Statistics:")
    print(f"  Total parts: {stats.total_parts}")
    print(f"  Unique brick types: {stats.unique_brick_types}")
    print(f"  Colors used: {len(stats.colors_used)}")
    print(f"  Estimated weight: {stats.estimated_weight_grams:.1f}g")
