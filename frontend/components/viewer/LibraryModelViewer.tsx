"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Eye, LoaderCircle, Maximize, RotateCcw } from "lucide-react";
import { apiFileUrl, ModelPreviewStatus } from "@/lib/api";

export default function LibraryModelViewer({id}: {id: string}) {
  const host = useRef<HTMLDivElement>(null), shell = useRef<HTMLDivElement>(null);
  const reset = useRef<() => void>(), root = useRef<THREE.Group>();
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [retry, setRetry] = useState(0), [wire, setWire] = useState(false), [missing, setMissing] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false, frame = 0, timer: ReturnType<typeof setTimeout> | undefined;
    let renderer: THREE.WebGLRenderer | undefined, controls: OrbitControls | undefined, observer: ResizeObserver | undefined;
    const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>(), textures = new Set<THREE.Texture>();
    setLoading(true); setError(""); setMissing([]); setWire(false);
    const load = async () => {
      const response = await fetch(apiFileUrl(`/api/v1/sets/library/${id}/preview${retry ? "?retry=1" : ""}`), {signal: controller.signal});
      if (!response.ok) throw new Error("预览服务暂时不可用。");
      const state: ModelPreviewStatus = await response.json();
      if (cancelled) return;
      if (state.status === "processing") { timer = setTimeout(() => { void load().catch(fail); }, 1500); return; }
      if (state.status !== "ready" || !state.model_url) throw new Error(state.error || "模型预览生成失败。");
      const modelResponse = await fetch(apiFileUrl(state.model_url), {signal: controller.signal});
      if (!modelResponse.ok) throw new Error("模型预览文件无法加载。");
      const gltf = await new GLTFLoader().parseAsync(await modelResponse.arrayBuffer(), "");
      const releaseScene = () => gltf.scene.traverse(object => { if (object instanceof THREE.Mesh) { object.geometry.dispose(); (Array.isArray(object.material) ? object.material : [object.material]).forEach((material: THREE.Material) => material.dispose()); } });
      if (cancelled || !host.current) { releaseScene(); return; }
      root.current = gltf.scene;
      gltf.scene.traverse(object => {
        if (object instanceof THREE.Mesh) {
          geometries.add(object.geometry);
          (Array.isArray(object.material) ? object.material : [object.material]).forEach((material: THREE.Material) => {
            materials.add(material);
            Object.values(material).forEach(value => { if (value instanceof THREE.Texture) textures.add(value); });
          });
        }
      });
      const scene = new THREE.Scene(); scene.background = new THREE.Color("#f5f5f7"); scene.add(gltf.scene);
      renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1;
      host.current.appendChild(renderer.domElement);
      const camera = new THREE.PerspectiveCamera(35, 1, .1, 1000000);
      controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
      scene.add(new THREE.HemisphereLight(0xffffff, 0x91939a, 1.8));
      for (const [x, y, z, intensity] of [[-1, 2, 3, 2.4], [2, 1, -1, 1]]) {
        const light = new THREE.DirectionalLight(0xffffff, intensity); light.position.set(x, y, z); scene.add(light);
      }
      const bounds = new THREE.Box3().setFromObject(gltf.scene), center = bounds.getCenter(new THREE.Vector3()), size = bounds.getSize(new THREE.Vector3());
      if (bounds.isEmpty() || !Number.isFinite(size.length())) throw new Error("模型没有可显示的几何。");
      reset.current = () => {
        const vertical = THREE.MathUtils.degToRad(camera.fov), horizontal = 2 * Math.atan(Math.tan(vertical/2) * camera.aspect);
        const distance = size.length()/2/Math.sin(Math.min(vertical, horizontal)/2)*1.05;
        camera.position.copy(center).add(new THREE.Vector3(.9, .65, 1.2).normalize().multiplyScalar(distance));
        camera.near = Math.max(.01, distance/10000); camera.far = distance*30; camera.updateProjectionMatrix();
        controls!.target.copy(center); controls!.minDistance = Math.max(size.length()*.08, 1); controls!.maxDistance = distance*5; controls!.update();
      };
      const resize = () => {
        if (!host.current || !renderer) return;
        const width = host.current.clientWidth, height = host.current.clientHeight;
        if (!width || !height) return;
        renderer.setSize(width, height); camera.aspect = width/height; reset.current?.();
      };
      observer = new ResizeObserver(resize); observer.observe(host.current); resize();
      const animate = () => { if (cancelled) return; controls!.update(); renderer!.render(scene, camera); frame = requestAnimationFrame(animate); };
      animate(); setMissing(state.missing_geometry || []); setLoading(false);
    };
    const fail = (reason: unknown) => { if (!cancelled) { setError(reason instanceof Error ? reason.message : "模型加载失败。"); setLoading(false); } };
    void load().catch(fail);
    return () => {
      cancelled = true; controller.abort(); clearTimeout(timer); cancelAnimationFrame(frame); observer?.disconnect(); controls?.dispose();
      geometries.forEach(item => item.dispose()); materials.forEach(item => item.dispose()); textures.forEach(item => item.dispose());
      renderer?.dispose(); renderer?.domElement.remove(); root.current = undefined;
    };
  }, [id, retry]);
  useEffect(() => { root.current?.traverse(object => { if (object instanceof THREE.Mesh) (Array.isArray(object.material) ? object.material : [object.material]).forEach((material: THREE.MeshStandardMaterial) => { material.wireframe = wire; }); }); }, [wire]);
  return <div className="library-viewer" ref={shell}>
    <div ref={host} className="library-canvas" aria-label="车辆三维预览"/>
    {loading && <div className="viewer-overlay" role="status"><LoaderCircle className="spin"/>正在准备模型预览</div>}
    {error && <div className="viewer-overlay" role="alert"><p>{error}</p><button className="text-link" onClick={() => setRetry(v => v+1)}>重新生成预览<RotateCcw size={15}/></button></div>}
    {!loading && !error && <div className="viewer-tools"><button className="icon-button" title="恢复视角" aria-label="恢复视角" onClick={() => reset.current?.()}><RotateCcw size={18}/></button><button className="icon-button" title="线框" aria-label="线框" aria-pressed={wire} onClick={() => setWire(v => !v)}><Eye size={18}/></button><button className="icon-button" title="全屏" aria-label="全屏" onClick={() => { void shell.current?.requestFullscreen(); }}><Maximize size={18}/></button></div>}
    {missing.length > 0 && <div className="library-preview-warning" role="status">预览缺少 {missing.length} 项零件几何：{missing.slice(0, 5).join("、")}{missing.length > 5 ? "…" : ""}</div>}
  </div>;
}
