/**
 * Web 3D LEGO Scene Builder using Three.js
 *
 * Renders LEGO assemblies in real-time, with interactive
 * manipulation, constraint visualization, and export capabilities.
 */

import * as THREE from 'three';

// ============================================================================
// Data Types (matching Python backend)
// ============================================================================

export interface Vector3Data {
  x: number;
  y: number;
  z: number;
}

export interface BrickData {
  lego_id: string;
  name: string;
  category: string;
  dimensions: [number, number, number];
  mass_grams: number;
  connectors: any[];
  official_colors: string[];
}

export interface PlacedPartData {
  instance_id: number;
  brick_id: string;
  position: [number, number, number];
  rotation: [number, number, number];
  color: string;
  connected_to: number[];
}

export interface AssemblyData {
  name: string;
  parts: PlacedPartData[];
  instructions: any;
  created_at: string;
  metadata: Record<string, any>;
}

// ============================================================================
// LEGO Color Palette
// ============================================================================

const LEGO_COLOR_MAP: Record<string, number> = {
  'Bright Red': 0xfc3c10,
  'Bright Blue': 0x0032c8,
  'Bright Yellow': 0xf6e600,
  'Black': 0x1a1a1a,
  'White': 0xfafafa,
  'Dark Gray': 0x423f3b,
  'Medium Stone Gray': 0xa0a5a9,
  'Bright Green': 0x02a84f,
  'Dark Tan': 0x5f7a5c,
  'Brown': 0x5c3d1f,
  'Dark Orange': 0xe87722,
  'Tan': 0xdab8a2,
};

// ============================================================================
// LEGO Brick Geometry Generator
// ============================================================================

class BrickGeometryGenerator {
  /**
   * Generate Three.js geometry for a LEGO brick
   * Studs are 8mm apart, one stud unit = 0.8
   */
  static generateBrickGeometry(
    width: number,
    height: number,
    depth: number
  ): THREE.BoxGeometry {
    // Convert stud units to 3D space (1 stud = 0.8 units for visual spacing)
    const boxWidth = width * 0.8;
    const boxHeight = height * 0.8;
    const boxDepth = depth * 0.8;

    return new THREE.BoxGeometry(boxWidth, boxHeight, boxDepth);
  }

  /**
   * Create a stud on top of a brick
   */
  static createStudGeometry(): THREE.CylinderGeometry {
    const radius = 0.2; // 4.8mm diameter stud
    const height = 0.15;
    const segments = 16;
    return new THREE.CylinderGeometry(radius, radius, height, segments);
  }

  /**
   * Create complete brick with studs
   */
  static createCompleteBrickMesh(
    width: number,
    height: number,
    depth: number,
    color: number
  ): THREE.Mesh {
    const geometry = this.generateBrickGeometry(width, height, depth);
    const material = new THREE.MeshStandardMaterial({
      color,
      roughness: 0.6,
      metalness: 0.0,
      flatShading: false,
    });

    const brick = new THREE.Mesh(geometry, material);

    // Add studs on top
    if (width === 1 && depth === 1) {
      // Single stud in center
      const stud = new THREE.Mesh(
        this.createStudGeometry(),
        new THREE.MeshStandardMaterial({ color })
      );
      stud.position.y = height * 0.4;
      brick.add(stud);
    } else if (width > 1 && depth > 1) {
      // Multiple studs in grid
      for (let x = 0; x < width; x++) {
        for (let z = 0; z < depth; z++) {
          const stud = new THREE.Mesh(
            this.createStudGeometry(),
            new THREE.MeshStandardMaterial({ color })
          );
          stud.position.x = (x - (width - 1) / 2) * 0.8;
          stud.position.y = height * 0.4;
          stud.position.z = (z - (depth - 1) / 2) * 0.8;
          brick.add(stud);
        }
      }
    }

    return brick;
  }
}

// ============================================================================
// LEGO Scene Builder
// ============================================================================

export class LEGOSceneBuilder {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  container: HTMLElement;

  // Scene objects
  partMeshes: Map<number, THREE.Mesh> = new Map();
  lights: THREE.Light[] = [];
  selectedPart: number | null = null;

  // Constraints visualization
  constraintVisualization: THREE.LineSegments | null = null;

  // Animation
  animationId: number | null = null;

  constructor(containerElement: HTMLElement) {
    this.container = containerElement;

    // Scene setup
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf0f0f0);

    // Camera
    this.camera = new THREE.PerspectiveCamera(
      75,
      containerElement.clientWidth / containerElement.clientHeight,
      0.1,
      1000
    );
    this.camera.position.set(10, 8, 10);
    this.camera.lookAt(0, 0, 0);

    // Renderer
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(
      containerElement.clientWidth,
      containerElement.clientHeight
    );
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowShadowMap;
    containerElement.appendChild(this.renderer.domElement);

    this.setupLighting();
    this.setupCameraControls();
    this.startAnimation();
  }

  /**
   * Setup scene lighting
   */
  private setupLighting(): void {
    // Ambient light
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    this.scene.add(ambientLight);
    this.lights.push(ambientLight);

    // Directional light (sun)
    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
    directionalLight.position.set(5, 10, 5);
    directionalLight.castShadow = true;
    directionalLight.shadow.mapSize.width = 2048;
    directionalLight.shadow.mapSize.height = 2048;
    this.scene.add(directionalLight);
    this.lights.push(directionalLight);

    // Ground plane
    const groundGeometry = new THREE.PlaneGeometry(50, 50);
    const groundMaterial = new THREE.ShadowMaterial({ opacity: 0.3 });
    const ground = new THREE.Mesh(groundGeometry, groundMaterial);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.5;
    ground.receiveShadow = true;
    this.scene.add(ground);
  }

  /**
   * Setup simple camera controls (orbit-like behavior)
   */
  private setupCameraControls(): void {
    let isDragging = false;
    let previousMousePosition = { x: 0, y: 0 };

    this.renderer.domElement.addEventListener('mousedown', (e) => {
      isDragging = true;
      previousMousePosition = { x: e.clientX, y: e.clientY };
    });

    this.renderer.domElement.addEventListener('mousemove', (e) => {
      if (!isDragging) return;

      const deltaX = e.clientX - previousMousePosition.x;
      const deltaY = e.clientY - previousMousePosition.y;

      const distance = this.camera.position.length();
      const angles = {
        theta: Math.atan2(this.camera.position.z, this.camera.position.x),
        phi: Math.acos(this.camera.position.y / distance),
      };

      angles.theta += deltaX * 0.01;
      angles.phi += deltaY * 0.01;
      angles.phi = Math.max(0.1, Math.min(Math.PI - 0.1, angles.phi));

      this.camera.position.x = distance * Math.sin(angles.phi) * Math.cos(angles.theta);
      this.camera.position.y = distance * Math.cos(angles.phi);
      this.camera.position.z = distance * Math.sin(angles.phi) * Math.sin(angles.theta);
      this.camera.lookAt(0, 0, 0);

      previousMousePosition = { x: e.clientX, y: e.clientY };
    });

    this.renderer.domElement.addEventListener('mouseup', () => {
      isDragging = false;
    });

    // Mouse wheel zoom
    this.renderer.domElement.addEventListener('wheel', (e) => {
      e.preventDefault();
      const distance = this.camera.position.length();
      const newDistance = Math.max(5, Math.min(100, distance + e.deltaY * 0.1));
      const direction = this.camera.position.normalize();
      this.camera.position.copy(direction.multiplyScalar(newDistance));
    });
  }

  /**
   * Render an assembly from data
   */
  renderAssembly(assemblyData: AssemblyData): void {
    // Clear existing parts
    this.clearScene();

    // Add grid helper
    const gridHelper = new THREE.GridHelper(20, 20, 0xcccccc, 0xeeeeee);
    this.scene.add(gridHelper);

    // Render each part
    for (const partData of assemblyData.parts) {
      this.addPart(partData);
    }

    // Auto-fit camera to view all parts
    this.fitCameraToObjects();
  }

  /**
   * Add a single part to the scene
   */
  addPart(partData: PlacedPartData): void {
    // For now, use simple dimensions (in real implementation, load from brick library)
    const defaultBricks: Record<string, [number, number, number]> = {
      '3003': [1, 1, 2],
      '3001': [2, 1, 2],
      '3002': [2, 1, 4],
      '2456': [1, 1, 1], // Simplified wheel
    };

    const [width, height, depth] = defaultBricks[partData.brick_id] || [1, 1, 1];
    const color = LEGO_COLOR_MAP[partData.color] || 0x888888;

    // Create brick mesh
    const mesh = BrickGeometryGenerator.createCompleteBrickMesh(
      width,
      height,
      depth,
      color
    );

    // Position and rotation
    mesh.position.set(partData.position[0], partData.position[1], partData.position[2]);
    mesh.rotation.order = 'XYZ';
    mesh.rotation.x = (partData.rotation[0] * Math.PI) / 180;
    mesh.rotation.y = (partData.rotation[1] * Math.PI) / 180;
    mesh.rotation.z = (partData.rotation[2] * Math.PI) / 180;

    // Add to scene
    this.scene.add(mesh);
    this.partMeshes.set(partData.instance_id, mesh);

    // Make interactive
    (mesh.userData as any).partId = partData.instance_id;
  }

  /**
   * Highlight constraint violations
   */
  visualizeConstraints(conflicts: any[]): void {
    // Create line segments for conflict visualization
    if (this.constraintVisualization) {
      this.scene.remove(this.constraintVisualization);
    }

    const geometry = new THREE.BufferGeometry();
    const positions: number[] = [];

    for (const conflict of conflicts) {
      if (this.partMeshes.has(conflict.part1) && this.partMeshes.has(conflict.part2)) {
        const mesh1 = this.partMeshes.get(conflict.part1)!;
        const mesh2 = this.partMeshes.get(conflict.part2)!;

        positions.push(
          mesh1.position.x, mesh1.position.y, mesh1.position.z,
          mesh2.position.x, mesh2.position.y, mesh2.position.z
        );
      }
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));
    const material = new THREE.LineBasicMaterial({ color: 0xff0000, linewidth: 2 });
    this.constraintVisualization = new THREE.LineSegments(geometry, material);
    this.scene.add(this.constraintVisualization);
  }

  /**
   * Auto-fit camera to show all parts
   */
  private fitCameraToObjects(): void {
    const box = new THREE.Box3();
    this.scene.children.forEach((child) => {
      if (child instanceof THREE.Mesh) {
        box.expandByObject(child);
      }
    });

    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    const fov = this.camera.fov * (Math.PI / 180);
    let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));

    cameraZ *= 1.5; // Add padding
    this.camera.position.z = cameraZ;

    const center = box.getCenter(new THREE.Vector3());
    this.camera.lookAt(center);
  }

  /**
   * Export current scene as glTF
   */
  exportToGLTF(): ArrayBuffer {
    // Simplified export - in production, use THREE.GLTFExporter
    const geometry = new THREE.BufferGeometry();
    // Merge all geometries...
    return new ArrayBuffer(0);
  }

  /**
   * Animation loop
   */
  private startAnimation(): void {
    const animate = () => {
      this.animationId = requestAnimationFrame(animate);

      // Update renderer
      this.renderer.render(this.scene, this.camera);
    };
    animate();
  }

  /**
   * Clean up resources
   */
  private clearScene(): void {
    // Remove all mesh objects (but keep lights, grid, etc.)
    const toRemove: THREE.Object3D[] = [];
    this.scene.children.forEach((child) => {
      if (child instanceof THREE.Mesh && !(child.material instanceof THREE.ShadowMaterial)) {
        toRemove.push(child);
      }
    });
    toRemove.forEach((child) => this.scene.remove(child));

    this.partMeshes.clear();
  }

  /**
   * Dispose resources
   */
  dispose(): void {
    if (this.animationId !== null) {
      cancelAnimationFrame(this.animationId);
    }
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  /**
   * Get scene statistics
   */
  getStatistics() {
    return {
      total_meshes: this.partMeshes.size,
      total_vertices: Array.from(this.partMeshes.values()).reduce(
        (sum, mesh) => sum + (mesh.geometry as any).attributes.position.count,
        0
      ),
    };
  }
}

// ============================================================================
// React Component Hook
// ============================================================================

export function useLEGOScene(containerRef: React.RefObject<HTMLDivElement>) {
  const sceneRef = React.useRef<LEGOSceneBuilder | null>(null);

  React.useEffect(() => {
    if (!containerRef.current) return;

    sceneRef.current = new LEGOSceneBuilder(containerRef.current);

    return () => {
      if (sceneRef.current) {
        sceneRef.current.dispose();
      }
    };
  }, [containerRef]);

  return {
    scene: sceneRef.current,
    renderAssembly: (data: AssemblyData) => {
      if (sceneRef.current) {
        sceneRef.current.renderAssembly(data);
      }
    },
    visualizeConstraints: (conflicts: any[]) => {
      if (sceneRef.current) {
        sceneRef.current.visualizeConstraints(conflicts);
      }
    },
  };
}
