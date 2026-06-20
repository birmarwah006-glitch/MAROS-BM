// oak.js — Prof Oak 3D loader + animations
import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.157.0/build/three.module.js';
import { GLTFLoader } from 'https://cdn.jsdelivr.net/npm/three@0.157.0/examples/jsm/loaders/GLTFLoader.js';

const OAK = {
  scene    : null,
  camera   : null,
  renderer : null,
  model    : null,
  mixer    : null,
  clock    : new THREE.Clock(),
  mode     : 'hero',
  talking  : false,
};

export function initOak(canvasId = 'oak-canvas') {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  OAK.scene = new THREE.Scene();

  OAK.camera = new THREE.PerspectiveCamera(
    45, window.innerWidth / window.innerHeight, 0.1, 100
  );
  OAK.camera.position.set(0, 1.2, 4);

  OAK.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  OAK.renderer.setSize(window.innerWidth, window.innerHeight);
  OAK.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  OAK.renderer.shadowMap.enabled = true;

  const ambient = new THREE.AmbientLight(0xffffff, 0.6);
  OAK.scene.add(ambient);

  const key = new THREE.DirectionalLight(0xc8f060, 1.2);
  key.position.set(2, 4, 3);
  OAK.scene.add(key);

  const fill = new THREE.DirectionalLight(0x60c8f0, 0.4);
  fill.position.set(-3, 2, -2);
  OAK.scene.add(fill);

  loadOak();
  window.addEventListener('resize', onResize);
  animate();
}

function loadOak() {
  const loader = new GLTFLoader();
  loader.load(
    './static/oak.glb',
    (gltf) => {
      OAK.model = gltf.scene;

      const box    = new THREE.Box3().setFromObject(OAK.model);
      const center = box.getCenter(new THREE.Vector3());
      const size   = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z);
      const scale  = 2.5 / maxDim;

      OAK.model.scale.setScalar(scale);
      OAK.model.position.sub(center.multiplyScalar(scale));
      OAK.model.position.y -= 0.5;

      OAK.model.traverse(child => {
        if (child.isMesh) {
          child.castShadow    = true;
          child.receiveShadow = true;
        }
      });

      OAK.scene.add(OAK.model);

      if (gltf.animations && gltf.animations.length > 0) {
        OAK.mixer = new THREE.AnimationMixer(OAK.model);
        OAK.mixer.clipAction(gltf.animations[0]).play();
      }

      console.log('[MAROS] Prof Oak loaded ✓');
    },
    null,
    (error) => console.warn('[MAROS] Oak load failed:', error)
  );
}

export function setOakMode(mode) {
  OAK.mode = mode;
  if (!OAK.camera) return;
  OAK.camera.position.set(0, 1.2, mode === 'hero' ? 4 : 6);
}

export function setOakTalking(talking) {
  OAK.talking = talking;
}

function animate() {
  requestAnimationFrame(animate);

  const delta   = OAK.clock.getDelta();
  const elapsed = OAK.clock.getElapsedTime();

  if (OAK.mixer) OAK.mixer.update(delta);

  if (OAK.model) {
    OAK.model.rotation.y = Math.sin(elapsed * 0.4) * 0.15;
    OAK.model.position.y = Math.sin(elapsed * 0.8) * 0.08 - 0.5;
    if (OAK.talking) {
      OAK.model.position.y += Math.sin(elapsed * 8) * 0.02;
    }
  }

  if (OAK.renderer) OAK.renderer.render(OAK.scene, OAK.camera);
}

function onResize() {
  OAK.camera.aspect = window.innerWidth / window.innerHeight;
  OAK.camera.updateProjectionMatrix();
  OAK.renderer.setSize(window.innerWidth, window.innerHeight);
}