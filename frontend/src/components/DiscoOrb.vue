<script setup>
import { onMounted, ref, onBeforeUnmount } from 'vue';
import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/examples/jsm/postprocessing/ShaderPass.js';

const props = defineProps({
  active: { type: Boolean, default: false },
});

// 容器引用
const canvasContainer = ref(null);

// 变量定义以便销毁时清理
let scene, camera, renderer, composer;
let sphereMesh, sphereGroup, ringSystem, tetherLine;
let bloomPass;
let animationId;
let lastT = 0;
let ringScale = 1;
let bloomStrength = 0.3;
let ringOpacity = 0.5;

// --- 配置参数 ---
const CONFIG = {
  ringParticleCount: 20000,
  coreSize: 22,
  ringInner: 28,
  ringOuter: 50,
  // 颜色配置
  colorLight: 0xcccccc,     // 亮格颜色 (灰白)
  colorDark: 0x050505,      // 暗格颜色 (深黑)
  colorRim: 0x888888,       // 边缘光
  colorRing: 0xcfa863,      // 环颜色 (暗金)
};

onMounted(() => {
  initThree();
  animate();
});

onBeforeUnmount(() => {
  cancelAnimationFrame(animationId);
  window.removeEventListener('resize', onWindowResize);
  renderer.dispose();
});

const initThree = () => {
  const container = canvasContainer.value;
  const width = container.clientWidth;
  const height = container.clientHeight;

  // 1. 场景与相机
  scene = new THREE.Scene();
  scene.background = null; // 让背景透明，而非全黑
  scene.fog = new THREE.FogExp2(0x000000, 0.002);

  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
  // 压低 Y 轴，让灯球往下沉，且拉远 Z 轴确保灯球和星环能在较矮的容器里完整显示
  camera.position.set(0, 0, 140);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.appendChild(renderer.domElement);

  // --- 2. 核心球体 (Solid Disco Sphere) ---
  const geometry = new THREE.IcosahedronGeometry(CONFIG.coreSize, 5);

  const sphereMaterial = new THREE.ShaderMaterial({
    uniforms: {
      time: { value: 0 },
      colorLight: { value: new THREE.Color(CONFIG.colorLight) },
      colorDark: { value: new THREE.Color(CONFIG.colorDark) },
      colorRim: { value: new THREE.Color(CONFIG.colorRim) }
    },
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vPos;
      varying vec3 vNormal;
      varying vec3 vViewPosition;

      void main() {
        vUv = uv;
        vPos = position;

        vNormal = normalize(normalMatrix * normal);
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        vViewPosition = -mvPosition.xyz;

        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      uniform vec3 colorLight;
      uniform vec3 colorDark;
      uniform vec3 colorRim;
      uniform float time;

      varying vec3 vPos;
      varying vec3 vNormal;
      varying vec3 vViewPosition;

      // 伪随机
      float random(vec2 st) {
          return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123);
      }

      void main() {
        vec3 norm = normalize(vPos);

        // [修改] 移除了之前的 noise distortion 计算，让经纬线保持平直
        float theta = atan(norm.z, norm.x);
        float phi = acos(norm.y);

        float tilesH = 25.0;
        float tilesV = 16.0;

        float globalH = (theta + 3.14159) / 6.28318 * tilesH * 2.0;
        float globalV = phi / 3.14159 * tilesV * 2.0;

        float checkH = floor(globalH);
        float checkV = floor(globalV);
        vec2 tileUV = vec2(fract(globalH), fract(globalV));

        // --- 1. 白线 (Gap) ---
        float gapSize = 0.06;
        float lineMask = smoothstep(0.0, gapSize, tileUV.x) * smoothstep(1.0, 1.0 - gapSize, tileUV.x) *
                         smoothstep(0.0, gapSize, tileUV.y) * smoothstep(1.0, 1.0 - gapSize, tileUV.y);

        // --- 2. 倒角/景深 (Bevel) ---
        float bevel = pow(tileUV.x * (1.0 - tileUV.x) * tileUV.y * (1.0 - tileUV.y), 0.25);
        bevel *= 1.8;

        // --- 3. 黑白逻辑 & 极地杂点 ---
        vec2 tileID = vec2(checkH, checkV);
        float tileRnd = random(tileID);

        // 基础概率：20% 白格
        float isLight = step(0.8, tileRnd);

        // 极地杂色逻辑
        float distFromEquator = abs(phi - 1.57079) / 1.57079;
        float noiseProb = smoothstep(0.5, 1.0, distFromEquator) * 0.4;
        float dirtRnd = random(tileID + vec2(10.0));

        if (dirtRnd < noiseProb) {
            isLight = 1.0 - isLight;
        }

        // --- 4. 颜色合成 ---
        vec3 tileColor = mix(colorDark, colorLight, isLight);

        // 叠加倒角
        tileColor *= bevel;

        // 增强噪点 (Heavy Grain) - 保持之前的强度
        float grain = random(vPos.xy * 300.0 + time * 0.1);
        tileColor *= (0.6 + 0.4 * grain);

        // --- 5. 绘制白线 ---
        vec3 lineColor = vec3(0.9);
        vec3 finalBase = mix(lineColor, tileColor, lineMask);

        // --- 6. 边缘光 ---
        vec3 viewDir = normalize(vViewPosition);
        vec3 normal = normalize(vNormal);
        float fresnel = 1.0 - dot(viewDir, normal);
        fresnel = pow(clamp(fresnel, 0.0, 1.0), 3.0);

        vec3 finalColor = finalBase + colorRim * fresnel * 0.5;

        gl_FragColor = vec4(finalColor, 1.0);
      }
    `,
    transparent: false,
  });

  sphereMesh = new THREE.Mesh(geometry, sphereMaterial);

  sphereGroup = new THREE.Group();
  sphereGroup.add(sphereMesh);

  // 同步倾斜角度
  sphereGroup.rotation.z = Math.PI / 20;
  sphereGroup.rotation.x = Math.PI / 12;

  scene.add(sphereGroup);

  // --- 3. 开普勒吸积盘 ---
  const ringGeo = new THREE.BufferGeometry();
  const ringPos = [];
  const ringSizes = [];
  const ringOrbitInfo = [];

  for (let i = 0; i < CONFIG.ringParticleCount; i++) {
    const r = THREE.MathUtils.randFloat(CONFIG.ringInner, CONFIG.ringOuter);
    const theta = THREE.MathUtils.randFloat(0, Math.PI * 2);
    const speed = 50.0 / Math.pow(r, 1.5);
    const y = (Math.random() - 0.5) * 1.0;

    const x = r * Math.cos(theta);
    const z = r * Math.sin(theta);

    ringPos.push(x, y, z);
    ringSizes.push(Math.random());
    ringOrbitInfo.push({ r, theta, speed, y });
  }

  ringGeo.setAttribute('position', new THREE.Float32BufferAttribute(ringPos, 3));
  ringGeo.setAttribute('size', new THREE.Float32BufferAttribute(ringSizes, 1));

  const ringMaterial = new THREE.PointsMaterial({
    color: CONFIG.colorRing,
    size: 0.2,
    transparent: true,
    opacity: 0.5,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true
  });

  ringSystem = new THREE.Points(ringGeo, ringMaterial);
  ringSystem.userData.orbits = ringOrbitInfo;

  ringSystem.rotation.z = Math.PI / 20;
  ringSystem.rotation.x = Math.PI / 12;

  scene.add(ringSystem);

  // --- 4. 悬挂线 ---
  const lineGeo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, CONFIG.coreSize, 0),
    new THREE.Vector3(0, 100, 0)
  ]);
  const lineMat = new THREE.LineBasicMaterial({
    color: 0x444444,
    transparent: true,
    opacity: 0.3
  });
  tetherLine = new THREE.Line(lineGeo, lineMat);
  scene.add(tetherLine);


  // --- 5. 后处理 ---
  composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(scene, camera);
  composer.addPass(renderPass);

  // Bloom
  bloomPass = new UnrealBloomPass(
      new THREE.Vector2(width, height),
      0.3,
      0.9,
      0.35
  );
  composer.addPass(bloomPass);

  // Film Grain
  const FilmGrainShader = {
    uniforms: {
      "tDiffuse": { value: null },
      "time": { value: 0.0 },
      "nIntensity": { value: 0.25 }, // 保持增强的噪点强度
      "sIntensity": { value: 0.05 },
      "grayscale": { value: 0 }
    },
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
      }
    `,
    fragmentShader: `
      uniform float time;
      uniform float nIntensity;
      uniform float sIntensity;
      uniform float grayscale;
      uniform sampler2D tDiffuse;
      varying vec2 vUv;

      void main() {
        vec4 cTextureScreen = texture2D( tDiffuse, vUv );
        float x = vUv.x * vUv.y * time * 1000.0;
        x = mod( x, 13.0 ) * mod( x, 123.0 );
        float dx = mod( x, 0.01 );
        vec3 cResult = cTextureScreen.rgb + cTextureScreen.rgb * clamp( 0.1 + dx * 100.0, 0.0, 1.0 );
        vec2 sc = vec2( sin( vUv.y * 4096.0 ), cos( vUv.y * 4096.0 ) );
        cResult += cTextureScreen.rgb * vec3( sc.x, sc.y, sc.x ) * sIntensity;
        cResult = cTextureScreen.rgb + clamp(nIntensity, 0.0,1.0) * (cResult - cTextureScreen.rgb);
        vec3 gray = vec3(dot(cResult, vec3(0.299, 0.587, 0.114)));
        cResult = mix(cResult, gray, 0.5);
        gl_FragColor =  vec4( cResult, cTextureScreen.a );
      }
    `
  };

  const filmPass = new ShaderPass(FilmGrainShader);
  filmPass.renderToScreen = true;
  composer.addPass(filmPass);

  window.addEventListener('resize', onWindowResize);
};

const onWindowResize = () => {
  if (!canvasContainer.value) return;
  const width = canvasContainer.value.clientWidth;
  const height = canvasContainer.value.clientHeight;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
  composer.setSize(width, height);
};

const animate = () => {
  animationId = requestAnimationFrame(animate);
  const time = performance.now() * 0.001;
  const dt = lastT ? Math.min(0.05, Math.max(0.001, time - lastT)) : 0.016;
  lastT = time;
  const isActive = !!props.active;
  const targetRingScale = isActive ? 1.35 : 1.0;
  const targetBloom = isActive ? 0.95 : 0.3;
  const targetOpacity = isActive ? 0.85 : 0.5;

  ringScale += (targetRingScale - ringScale) * 0.08;
  bloomStrength += (targetBloom - bloomStrength) * 0.08;
  ringOpacity += (targetOpacity - ringOpacity) * 0.1;

  if (sphereMesh) {
    sphereMesh.rotation.y = time * 0.1;
    sphereMesh.material.uniforms.time.value = time;
  }

  if (ringSystem) {
    const positions = ringSystem.geometry.attributes.position.array;
    const orbits = ringSystem.userData.orbits;
    const speedFactor = isActive ? 2.4 : 1.0;

    for (let i = 0; i < orbits.length; i++) {
      const o = orbits[i];
      o.theta += o.speed * 0.005 * speedFactor;
      const x = o.r * Math.cos(o.theta);
      const z = o.r * Math.sin(o.theta);
      positions[i * 3] = x;
      positions[i * 3 + 1] = o.y;
      positions[i * 3 + 2] = z;
    }
    ringSystem.geometry.attributes.position.needsUpdate = true;

    ringSystem.rotation.y += dt * (isActive ? 1.35 : 0.35);
    ringSystem.scale.setScalar(ringScale);
    if (ringSystem.material) {
      ringSystem.material.opacity = ringOpacity;
      ringSystem.material.needsUpdate = false;
    }
  }

  if (bloomPass) {
    bloomPass.strength = bloomStrength;
  }

  const filmPass = composer.passes[2];
  if (filmPass) {
    filmPass.uniforms.time.value = time;
  }

  composer.render();
};
</script>

<template>
  <div class="disco-scene" ref="canvasContainer"></div>
</template>

<style scoped>
.disco-scene {
  width: 100%;
  height: 100%;
  min-height: 150px;
  background: transparent;
  overflow: hidden;
  position: relative;
}

.disco-scene :deep(canvas) {
  position: absolute;
  inset: 0;
  width: 100% !important;
  height: 100% !important;
}
</style>
