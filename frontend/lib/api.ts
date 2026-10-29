/** API client for the Car2LEGO backend. */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

export function apiFileUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return path.startsWith("/api/v1/")
    ? `${API_URL.replace(/\/api\/v1\/?$/, "")}${path}`
    : `${API_URL.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
}

export interface CarResult {
  id: string;
  make: string;
  model: string;
  year: number | null;
  trim?: string;
  body_style?: string;
  image_url?: string;
  created_at: string;
}

export interface MatchInfo {
  level: number;
  label: string;
  confidence: number;
  set_num?: string;
  set_name?: string;
  moc_name?: string;
  moc_author?: string;
}

export interface DesignPart {
  part_num: string;
  ldraw_color_id: number;
  color_name?: string;
  quantity: number;
  bricklink_id?: string;
  name?: string;
  image_url?: string;
}

export interface Design {
  id: string;
  car_id?: string;
  match?: MatchInfo;
  status: string;
  parts_count?: number;
  difficulty?: string;
  created_at: string;
  completed_at?: string;
  error_message?: string;
  library_match?: LibraryMatch | null;
}

export interface LibraryMatch { total: number; url: string; model_ids: string[] }
export type CreateDesignResult = DesignDetail | { status: "matched"; library_match: LibraryMatch };

export interface PhysicalSpecifications {
  version: string;
  vehicle: { make: string; model: string; year: number | null };
  scale: string | null;
  scale_mode?: "auto" | "appearance" | "exact";
  applied_to_design?: boolean;
  overall_grounded: boolean;
  generation_status?: "ambiguous" | "source_scoped" | "requested_year" | "unknown";
  source_year_ranges?: { start: number; end: number }[];
  measurements: Record<string, { value: number; unit: string; method: string; grounded: boolean; conflicted: boolean;
    source_url?: string; source_kind: string; source_status: string; vehicle_match: string; confidence: number;
    target_ldu?: number; target_model_mm?: number; evidence: string; definition: string; derivation?: string }>;
  missing: string[];
  required_measurements?: string[];
  recommended_measurements?: string[];
  missing_required?: string[];
  missing_recommended?: string[];
  conflicts: { name: string }[];
  sources: { url: string; status: string; title?: string; error?: string; note?: string; measurement_count?: number }[];
  acquisition?: string;
  research?: { skill: string; version: string; status: string; scale: string; documents_read?: number;
    mode?: string; model?: string; turns?: number;
    started_at?: string; finished_at?: string; missing_fields?: string[];
    queries: { query: string; purpose: string; status: string; engine?: string; results: { title: string; url: string }[] }[] };
}

export interface DesignDetail extends Design {
  reference_gate?: { status: string; allowed: boolean; image_correct: boolean; blocked_views: string[] };
  repair_tasks?: { id: string; view: string; kind: string; modules: string[]; expected: string; verification: string; status: string }[];
  module_progress?: { status: string; completion_ready: boolean; complete: number; required: number | null; stale?: boolean;
    modules: { id: string; status: string; parts: string[]; failures: string[]; unverified: string[] }[] };
  module_tasks?: { planner: string; active: string | null; complete: number; required: number;
    tasks: { id: string; dependencies: string[]; status: string; attempts: number; reason?: string;
      evidence_current: boolean; provisional: true; candidates?: { passing: number; required: number } | null }[] } | null;
  acceptance?: { status: string; accepted: boolean; gates: Record<string, string>; appearance_review_ready?: boolean;
    review_inputs?: Record<string, string> | null };
  inspection_images?: { view: string; url: string; category: string; synthetic: false }[];
  assembly_planner?: string;
  assembly_agent?: { planner: string; status: string; steps: number; stop_reason?: string; digital_status?: string } | null;
  workflow_version?: string;
  requested_scale?: string | null;
  scale_mode?: "auto" | "appearance" | "exact";
  scale_requirements?: ScaleRequirements | null;
  scene_url?: string;
  renders?: { view: string; url: string }[];
  external_renders?: { view: string; label: string; url: string; category: string; module?: string; synthetic: boolean }[];
  external_checks?: Record<string, { status: string; tool?: string; version?: string; bom?: { status: string; expected_total?: number; actual_total?: number; missing_parts?: string[]; unknown_colors?: number[] }; analyzer_status?: string; artifact_count?: number; error?: string }>;
  actual_dimensions_mm?: Record<string, number>;
  physical_specifications?: PhysicalSpecifications;
  scale_deviation_percent?: Record<string, number>;
  mechanisms?: { id: string; capability: string; status: string; limits: number[]; reason?: string }[];
  quality_report?: { digital_status: string; precision_verified: boolean; gates: Record<string, string>; studio: { status: string }; physical: { status: string }; issues: { kind: string; parts: string[]; modules: string[] }[] };
  job?: { state: string; stage: string; progress: number; calls: Record<string, number>; limits: Record<string, number | null>; module_repairs: Record<string, number>;
    resume_stage?: string; failure?: { stage: string; call_kind?: string; exception_type: string; attempts: number; retryable: boolean } };
  assembly_preview_url?: string;
  assembly_preview_rear_url?: string;
  quality_level?: string;
  reference_images?: { view: string; url: string; model: string; synthetic: boolean; category?: string; purpose?: string;
    shot_request?: {focus: string; angles: string[]; reason: string; modules: string[]} }[];
  reference_dispatch?: { status: string; batch_index: number; batch_size: number; views: string[];
    completed_views: string[]; failed_views: string[]; expected_views: number } | null;
  official_build_references?: {set_num: string; name: string; instructions_url: string; product_url: string; local_pages?: number[]; local_pdf?: boolean}[];
  car?: CarResult;
  parts: DesignPart[];
  file_urls: Record<string, string>;
  parent_design_id?: string;
  customization_request?: string;
}

export interface CustomizationResponse {
  id: string;
  status: string;
  message: string;
  parent_design_id: string;
}

export interface DesignStatus {
  id: string;
  status: string;
  progress: number;
  stage?: string;
  error_message?: string;
}

export interface LegoSetInfo {
  set_num: string;
  name: string;
  year?: number;
  brick_count?: number;
  car_make?: string;
  car_model?: string;
  image_url?: string;
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }
  return res.json();
}

/** POST /designs — submit a car and get a LEGO design */
export async function createDesign(
  make: string,
  model: string,
  year?: number | null,
  image_url?: string,
  assembly_planner?: string,
  reuse_library?: boolean,
  reconstruction_backend?: string
): Promise<CreateDesignResult> {
  return fetchApi<CreateDesignResult>("/designs", {
    method: "POST",
    body: JSON.stringify({ make, model, year, image_url, assembly_planner, reuse_library, reconstruction_backend }),
  });
}

export async function createDesignFromImage(
  image: File, overrides: { make?: string; model?: string; year?: number; assembly_planner?: string; reuse_library?: boolean; reconstruction_backend?: string } = {}
): Promise<CreateDesignResult> {
  const form = new FormData();
  form.append("image", image);
  if (overrides.make) form.append("make", overrides.make);
  if (overrides.model) form.append("model", overrides.model);
  if (overrides.year) form.append("year", String(overrides.year));
  if (overrides.assembly_planner) form.append("assembly_planner", overrides.assembly_planner);
  if (overrides.reconstruction_backend) form.append("reconstruction_backend", overrides.reconstruction_backend);
  if (overrides.reuse_library === false) form.append("reuse_library", "false");
  const response = await fetch(`${API_URL}/designs/from-image`, { method: "POST", body: form });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

/** GET /designs/:id */
export async function getDesign(id: string): Promise<DesignDetail> {
  return fetchApi<DesignDetail>(`/designs/${id}`);
}

export interface AssemblyPlanners {
  default: string;
  alternatives: { id: string; configured: boolean }[];
  comparison_status: string;
  winner?: string | null;
}
export function getAssemblyPlanners(): Promise<AssemblyPlanners> {
  return fetchApi("/designs/assembly-planners");
}

export async function collectVehicleSpecifications(make: string, model: string, year: number | null): Promise<PhysicalSpecifications> {
  return fetchApi("/research/physical-specifications", {
    method: "POST", body: JSON.stringify({ make, model, year }),
  });
}

/** GET /designs/:id/status */
export async function getDesignStatus(id: string): Promise<DesignStatus> {
  return fetchApi<DesignStatus>(`/designs/${id}/status`);
}

/** GET /designs/:id/parts */
export async function getDesignParts(id: string): Promise<DesignPart[]> {
  return fetchApi<DesignPart[]>(`/designs/${id}/parts`);
}

/** GET /sets/known-cars */
export async function getKnownCars(): Promise<LegoSetInfo[]> {
  return fetchApi<LegoSetInfo[]>("/sets/known-cars");
}

/** GET /sets */
export async function searchSets(
  make?: string,
  model?: string
): Promise<LegoSetInfo[]> {
  const params = new URLSearchParams();
  if (make) params.set("make", make);
  if (model) params.set("model", model);
  return fetchApi<LegoSetInfo[]>(`/sets?${params.toString()}`);
}

/** GET /cars/lookup */
export async function lookupCar(make: string, model: string, year?: number | null) {
  return fetchApi<CarResult>("/cars/lookup", {
    method: "POST",
    body: JSON.stringify({ make, model, year }),
  });
}

/** POST /designs/:id/customize — customize an existing design */
export async function customizeDesign(
  designId: string,
  customizationText: string
): Promise<CustomizationResponse> {
  return fetchApi<CustomizationResponse>(`/designs/${designId}/customize`, {
    method: "POST",
    body: JSON.stringify({ customization_text: customizationText }),
  });
}

/** GET /designs/:id/ldr — get raw LDraw content for 3D viewer */
export async function getDesignLdr(id: string): Promise<string> {
  const url = `${API_URL}/designs/${id}/ldr`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load LDraw: ${res.status}`);
  return res.text();
}

/** POST /designs/:id/studio-open — open design in BrickLink Studio */
export async function openInStudio(designId: string): Promise<{
  success: boolean;
  io_path: string;
  studio_opened: boolean;
  file_opened: boolean;
}> {
  return fetchApi(`/designs/${designId}/studio-open`, { method: "POST" });
}

export function reviewAppearance(id: string, status: "pass" | "fail", inputs: Record<string, string>, notes: string) {
  return fetchApi(`/designs/${id}/appearance-review`, {
    method: "POST", body: JSON.stringify({ status, inputs, notes }),
  });
}

/** GET /mods — get all modification parts catalog */
export interface ModPartInfo {
  id: string;
  name: string;
  category: string;
  real_world_ref: string;
  description: string;
  difficulty: string;
  estimated_parts: number;
  visual_change: string;
  lego_parts_count: number;
}

export async function getModsCatalog(): Promise<Record<string, ModPartInfo[]>> {
  return fetchApi<Record<string, ModPartInfo[]>>("/mods");
}

export interface ScaleRequirements {
  scale_mode?: "appearance" | "exact";
  size_margin_percent?: number | null;
  version: string; scale: string; label: string; description: string; applied_to_design?: boolean;
  visual_requirements: { id: string; label: string; detail: string }[];
  mechanisms: { id: string; label: string; minimum_instances: number }[];
  reference_views: string[]; required_measurements: string[]; recommended_measurements: string[];
  dimension_tolerances_percent: Record<string, number>;
  component_tolerances_percent: { tyre_diameter: number; tyre_width: number };
  physical_checks: string[]; motion_sample_count: number; max_step_parts: number;
}
export interface ScaleCapability { ratio: string; mechanisms: string[]; status: string; recipe_available: boolean; precision_supported: boolean; requirements?: ScaleRequirements }
export interface CatalogVehicle {
  id: string; make: string; model: string; year: number; body_style: string; powertrain: string;
  visual_targets: string[]; set_num?: string; image_url?: string; image_kind: string;
  specifications?: { length: number; width: number; height: number; wheelbase: number; source: string };
  official_build_references?: {set_num: string; name: string; instructions_url: string; product_url: string}[];
  scale_capabilities: ScaleCapability[]; recipe_available: boolean; precision_verified: boolean;
}
export async function getVehicleCatalog(): Promise<{ vehicles: CatalogVehicle[] }> { return fetchApi("/sets/vehicles"); }

export interface LibraryBrand { id: string; en: string; zh: string; logo?: string | null }
export interface OfficialModel {
  id: string; name_zh: string; original_name: string; model: string; brands: LibraryBrand[];
  vehicle_types: string[]; vehicle_type_labels: string[]; identity: string;
  vehicle_year: number | null; vehicle_year_evidence: string; release_year: number | null;
  set_num: string; variant: string; variant_zh: string; parts_count: number; steps_count: number;
  filename: string; sha256: string; bytes: number; source_url: string; author: string; provider: string;
  description_zh: string; unresolved_parts: string[]; missing_parts: string; missing_patterns: string; missing_stickers: string;
  download_url: string; thumbnail_url?: string | null; image_kind: string;
}
export interface OfficialModelPage {
  models: OfficialModel[]; total: number; collection_total: number; page: number; pages: number; per_page: number;
  facets: { brands: LibraryBrand[]; types: {id: string; label: string}[]; vehicle_years: number[]; release_years: number[] };
}
export interface ModelPreviewStatus { status: "processing" | "ready" | "failed"; model_url?: string; error?: string; missing_geometry?: string[] }
export function getOfficialModels(params: string): Promise<OfficialModelPage> { return fetchApi(`/sets/library?${params}`); }
export function getOfficialModel(id: string): Promise<OfficialModel> { return fetchApi(`/sets/library/${id}`); }
export async function getScales(): Promise<{ scales: ScaleCapability[]; default: string }> { return fetchApi("/designs/scales"); }
export async function controlDesign(id: string, action: string, module?: string) {
  return fetchApi<DesignStatus>(`/designs/${id}/control/${action}${module ? `?module=${encodeURIComponent(module)}` : ""}`, { method: "POST" });
}
export interface ScenePart { id: string; part_num: string; color_id: number; module: string; feature: string; transform: { position: number[]; rotation: number[] } }
export interface AssemblyScene { version: string; colors?: Record<number, number[]>; parts: ScenePart[]; modules: { id: string; parent?: string | null; depends_on: string[] }[]; steps: { index: number; module: string; parts: string[] }[]; joints: { id: string; child: string; parent: string; axis: number[]; pivot: number[]; limits: number[]; kind: string; capability: string; implemented: boolean }[] }
export function getAssemblyScene(id: string): Promise<AssemblyScene> { return fetchApi(`/designs/${id}/scene`); }
export function getAssemblyGeometry(id: string): Promise<Record<string, { color_id: number; positions: number[] }[]>> { return fetchApi(`/designs/${id}/geometry`); }
