# Web 3D LEGO Generation: System Design & API Specification

## Architecture Overview

### System Components

```
┌──────────────────────────────────────────────────────────────────┐
│                          LLM Layer                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Vision Analysis: Extract vehicle structure from images     │  │
│  │ Instruction Generation: Decompose into LEGO steps         │  │
│  │ Optimization: Suggest alternative assembly sequences       │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
              ↓
┌──────────────────────────────────────────────────────────────────┐
│                    Backend Service (FastAPI)                     │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ LEGO Assembly Generator                                 │   │
│  │ - Part placement algorithm                             │   │
│  │ - Constraint satisfaction solver                        │   │
│  │ - Instruction validation                                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Geometry Engine                                         │   │
│  │ - Brick definition management                          │   │
│  │ - Collision detection                                   │   │
│  │ - Connection validation                                 │   │
│  │ - Spatial indexing (BVH trees)                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Export Pipeline                                         │   │
│  │ - LDraw format generation                              │   │
│  │ - glTF 2.0 serialization                               │   │
│  │ - Studio .io format (reverse-engineered)               │   │
│  │ - BOM (Bill of Materials) compilation                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Asset Management                                        │   │
│  │ - LEGO part library (LDraw DAT files)                   │   │
│  │ - Color palette definitions                            │   │
│  │ - Connection point database                            │   │
│  │ - Performance cache (geometry, meshes)                  │   │
│  └─────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
              ↓
┌──────────────────────────────────────────────────────────────────┐
│                      Browser Frontend                             │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Three.js Rendering Engine                              │   │
│  │ - Real-time 3D scene rendering                         │   │
│  │ - Interactive camera controls                          │   │
│  │ - Exploded view assembly mode                          │   │
│  │ - Constraint visualization overlay                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ UI Components                                           │   │
│  │ - Part library browser                                 │   │
│  │ - Instruction viewer with step navigation             │   │
│  │ - Assembly timeline scrubber                           │   │
│  │ - Conflict detection alerts                            │   │
│  │ - Statistics & analysis panel                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Local Validation (WASM)                                │   │
│  │ - Quick collision checks                               │   │
│  │ - Connection type validation                           │   │
│  │ - Lightweight geometry operations                      │   │
│  └─────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Core Data Structures

### 1. LEGO Brick Definition

```python
@dataclass
class BrickConnector:
    type: Literal["stud", "tube", "axle", "clip", "pin"]  # Connection type
    position: Vector3  # Local coordinate (relative to brick)
    normal: Vector3    # Direction the connector faces
    gender: Literal["male", "female", "both"]
    strength: float    # Connection strength (0.0-1.0)

@dataclass
class LEGOBrick:
    lego_id: str                        # LEGO official part ID (e.g., "3003")
    name: str                           # Part name (e.g., "Brick 1x2")
    category: str                       # "brick", "plate", "wheel", etc.
    dimensions: Vector3                 # Size in stud units [width, height, depth]
    mass_grams: float                   # Physical weight estimate
    connectors: List[BrickConnector]   # Available connection points
    official_colors: List[str]          # Available LEGO colors for this part
    geometry_url: str                   # Path to LDraw DAT file or glTF
    
    def get_bounding_box(self) -> BoundingBox:
        """Return AABB for collision detection"""
        
    def get_connection_at(self, position: Vector3) -> Optional[BrickConnector]:
        """Find connector near position"""
```

### 2. Assembly Instruction

```python
@dataclass
class AssemblyStep:
    step_number: int
    description: str
    parts_to_add: List[int]          # Indices into assembly.parts
    reference_image: Optional[str]    # Optional visual reference
    time_estimate_seconds: int        # Estimated build time
    difficulty_level: Literal["easy", "medium", "hard"]
    
@dataclass
class AssemblyInstructions:
    vehicle_name: str
    vehicle_specs: dict                # Original specs from LLM
    total_parts: int
    bom: Dict[str, int]                # {"3003": 5, "3002": 3, ...}
    estimated_time_minutes: int
    steps: List[AssemblyStep]
    confidence_score: float            # 0.0-1.0 (LLM confidence)
    
    def to_json(self) -> str:
        """Serialize to JSON format"""
        
    @staticmethod
    def from_json(data: str) -> 'AssemblyInstructions':
        """Deserialize from JSON format"""
```

### 3. Placed Part Instance

```python
@dataclass
class PlacedPart:
    instance_id: int                    # Unique within assembly
    brick_definition: LEGOBrick
    position: Vector3                   # World coordinates (studs)
    rotation: Quaternion                # Orientation
    color: str                          # LEGO color name
    connected_to: List[int]             # Instance IDs of connected neighbors
    
    def get_matrix(self) -> Matrix4:
        """Return transformation matrix for 3D rendering"""
        
    def get_aabb(self) -> BoundingBox:
        """Get axis-aligned bounding box in world space"""
        
    def check_collision(self, other: 'PlacedPart', tolerance: float = 0.1) -> bool:
        """Check if overlapping with another part"""
```

### 4. Assembly (Complete Model)

```python
@dataclass
class Assembly:
    name: str
    parts: List[PlacedPart]
    instructions: AssemblyInstructions
    metadata: dict                      # Custom properties, origin, timestamp
    
    def validate(self) -> ValidationReport:
        """Check all constraints"""
        
    def to_ldraw(self) -> str:
        """Export to LDraw format"""
        
    def to_gltf(self) -> bytes:
        """Export to glTF binary format"""
        
    def get_statistics(self) -> AssemblyStatistics:
        """Return part counts, colors, connectivity info"""
```

---

## Backend API Specification

### 1. Generate Instructions

```
POST /api/generate-instructions
Content-Type: application/json

Request:
{
  "images": ["base64_image_1", "base64_image_2"],
  "vehicle_specs": {
    "brand": "Lamborghini",
    "model": "Countach",
    "year": 1974,
    "estimated_length_cm": 450
  },
  "lego_scale": 1/12,
  "style": "modular" | "realistic" | "compact",
  "llm_model": "gpt-4o" | "claude-opus"
}

Response:
{
  "instructions": {
    "vehicle_name": "Lamborghini Countach 1974",
    "total_parts": 1245,
    "bom": {"3001": 45, "3002": 120, ...},
    "steps": [
      {
        "step": 1,
        "description": "Attach first chassis brick",
        "parts_to_add": [{"brick_id": "3003", "color": "Bright Red", ...}]
      }
    ]
  },
  "confidence_score": 0.87,
  "generation_time_seconds": 45.2,
  "warnings": ["High parts count may require optimization"]
}
```

### 2. Render Instructions to Scene

```
POST /api/render-instructions
Content-Type: application/json

Request:
{
  "instructions": {...},  # Full AssemblyInstructions object
  "output_format": "gltf" | "ldraw" | "json",
  "include_step_by_step": true
}

Response:
{
  "assembly": {
    "name": "Lamborghini Countach",
    "parts": [
      {
        "id": 0,
        "brick_id": "3003",
        "color": "Bright Red",
        "position": [0, 0, 0],
        "rotation": [0, 0, 0],
        "connected_to": [1, 2]
      }
    ],
    "bounding_box": {
      "min": [-10, 0, -15],
      "max": [10, 8, 15]
    }
  },
  "validation": {
    "is_valid": true,
    "conflicts": [],
    "structural_integrity": 0.92
  }
}
```

### 3. Validate Assembly

```
POST /api/validate-assembly
Content-Type: application/json

Request:
{
  "assembly": {...},  # Full Assembly object
  "check_level": "basic" | "detailed" | "physics"
}

Response:
{
  "valid": true,
  "issues": [
    {
      "type": "unsupported_connection",
      "severity": "warning",
      "parts": [42, 43],
      "message": "Clip connection may fail on transparent brick"
    }
  ],
  "statistics": {
    "total_connections": 487,
    "valid_connections": 485,
    "total_parts": 1245,
    "colors_used": 12,
    "structural_score": 0.94
  }
}
```

### 4. Export Assembly

```
POST /api/export
Content-Type: application/json

Request:
{
  "assembly": {...},
  "format": "ldraw" | "gltf" | "studio_io",
  "optimized": true,
  "include_instructions": true
}

Response:
{
  "data": "base64_encoded_file",
  "format": "ldraw",
  "filename": "lamborghini_countach.ldr",
  "size_bytes": 45823
}
```

### 5. Browser-side WebSocket: Real-time Preview

```
WebSocket: ws://localhost:8777/api/stream

// Client → Server (part placement update)
{
  "type": "place_part",
  "part_id": 42,
  "position": [5, 2, 3],
  "rotation": [0, 0, 45]
}

// Server → Client (validation feedback)
{
  "type": "validation_result",
  "valid": false,
  "conflicts": [
    {
      "with_part": 41,
      "distance": 0.5,
      "required_distance": 0.8,
      "suggestion": "Move up by 0.3 studs"
    }
  ]
}
```

---

## LEGO Constraint System

### Connection Types

```python
ConnectionType = Literal[
    "stud",       # Standard male stud (Ø4.8mm)
    "tube",       # Female tube (accepts stud)
    "axle",       # Axle hole (for wheels/gears)
    "pin",        # Thin pin connector
    "clip",       # Clip/slope connector
    "magnet",     # Magnetic connection
    "rail",       # Pin rail system
]

class ConnectionValidator:
    """Validates LEGO part connections"""
    
    COMPATIBLE_PAIRS = {
        ("stud", "tube"): 1.0,        # Perfect fit
        ("stud", "tube-clip"): 0.8,   # Works but loose
        ("axle", "axle_hole"): 1.0,
        ("clip", "slope"): 1.0,
        ("stud", "clip"): 0.0,        # Invalid
    }
    
    def validate_connection(
        self, 
        part1: PlacedPart, 
        connector1: BrickConnector,
        part2: PlacedPart,
        connector2: BrickConnector
    ) -> ConnectionValidation:
        """Check if two connectors can join"""
        
    def auto_align(
        self,
        part1: PlacedPart,
        connector1: BrickConnector,
        part2: PlacedPart,
        connector2: BrickConnector
    ) -> Tuple[Vector3, Quaternion]:
        """Calculate precise alignment for two connectors"""
```

### Structural Constraints

```python
class StructuralValidator:
    """Validates structural integrity"""
    
    MAX_CANTILEVER_LENGTH = 8  # studs
    MIN_CONNECTION_STRENGTH = 0.5  # normalized
    MAX_BRICK_STRESSES = {
        "compression": 1000,  # N
        "tension": 500,
        "shear": 300,
    }
    
    def check_stability(self, assembly: Assembly) -> StabilityReport:
        """Analyze if structure can self-support"""
        # 1. Build connection graph
        # 2. Calculate load distribution
        # 3. Check stress limits
        # 4. Identify weak points
        
    def find_unstable_parts(self, assembly: Assembly) -> List[int]:
        """Return indices of parts at risk of falling"""
```

---

## Performance Optimization Strategy

### 1. Geometry Caching

```python
class GeometryCache:
    """LRU cache for brick geometries"""
    
    _cache: Dict[str, Tuple[Mesh, Texture]] = {}
    _max_size: int = 200  # Keep 200 unique brick types cached
    
    @staticmethod
    def get_geometry(brick_id: str, color: str) -> Mesh:
        key = f"{brick_id}_{color}"
        if key not in GeometryCache._cache:
            GeometryCache._load_and_cache(key)
        return GeometryCache._cache[key]
```

### 2. Spatial Indexing

```python
class SpatialIndex:
    """BVH tree for fast collision queries"""
    
    def __init__(self, assembly: Assembly):
        self.bvh = build_bvh_tree([p.get_aabb() for p in assembly.parts])
        
    def find_nearby_parts(self, position: Vector3, radius: float) -> List[int]:
        """O(log n) query for nearby parts"""
```

### 3. Level of Detail (LOD)

```python
class LODSystem:
    """Reduce geometry detail for distant parts"""
    
    LOD_LEVELS = {
        0: "full_detail",
        1: "simplified",
        2: "bounding_box",
    }
    
    @staticmethod
    def get_lod(distance_to_camera: float) -> int:
        if distance_to_camera < 20:
            return 0  # Full detail
        elif distance_to_camera < 50:
            return 1  # Simplified
        else:
            return 2  # Bounding box
```

### 4. WebAssembly (WASM) for Heavy Ops

```rust
// collision_detection.wasm
#[wasm_bindgen]
pub fn check_collision_batch(
    parts: &[SerializedPart],
    max_distance: f32
) -> Vec<CollisionResult> {
    // Heavy geometry operations in Rust
    // Called from JavaScript for performance
}
```

---

## Integration with Existing Pipeline

### Current Car2LGEO_3D_LLM Flow → Web 3D

```
BEFORE (Blender):
  LLM generates Blender script
    → Blender MCP executes
    → Native process execution
    → LDraw export
    
AFTER (Web 3D):
  LLM generates structured instructions (JSON)
    → Web service validates & renders
    → Browser previews in real-time
    → Multiple export formats
```

### Backward Compatibility

- Keep existing LDraw parser (same input format)
- Maintain BrickLink Studio `.io` export
- Support existing LEGO part library
- Compatible with current spec/image generation steps

---

## Testing Strategy

### Unit Tests

```python
# test_geometry.py
def test_brick_collision():
    """Verify collision detection accuracy"""
    brick1 = PlacedPart(...)
    brick2 = PlacedPart(...)
    assert collision_detector.check_collision(brick1, brick2) == True

# test_connections.py
def test_stud_tube_connection():
    """Verify valid connections are accepted"""
    stud_connector = BrickConnector(type="stud", ...)
    tube_connector = BrickConnector(type="tube", ...)
    assert ConnectionValidator.is_compatible(stud_connector, tube_connector)
```

### Integration Tests

```python
# test_pipeline.py
def test_full_generation_pipeline():
    """End-to-end test: image → instructions → assembly → export"""
    image_path = "test_car.jpg"
    specs = load_test_specs()
    
    # Generate instructions
    instructions = llm_generator.generate(image_path, specs)
    
    # Render to assembly
    assembly = assembly_renderer.render(instructions)
    
    # Validate
    report = assembly.validate()
    assert report.valid == True
    
    # Export
    ldraw = assembly.to_ldraw()
    assert len(ldraw) > 0
```

### Browser Tests (Playwright)

```typescript
// e2e/scene-rendering.spec.ts
test("Scene renders 1000 parts without frame drops", async ({ page }) => {
  await page.goto("http://localhost:8778");
  
  const fps = await page.evaluate(() => {
    return window.performanceMonitor.averageFPS;
  });
  
  expect(fps).toBeGreaterThan(30);
});
```

---

## Security Considerations

1. **Input Validation**: All LLM-generated instructions must pass schema validation
2. **Sandbox**: Browser WASM runs in isolated context
3. **File Uploads**: Scan for malicious content; limit file sizes
4. **API Rate Limiting**: Prevent DoS; limit generation requests per user
5. **CORS**: Restrict cross-origin requests appropriately

