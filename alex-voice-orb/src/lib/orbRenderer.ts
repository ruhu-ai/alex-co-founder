import { OrbMode, OrbParameters } from '../types/orb';

export const DEFAULT_ORB_PARAMS: OrbParameters = {
  cobaltWeight: 0.85,
  azureVibrancy: 0.92,
  cloudDensity: 0.88,
  cloudElevation: 0.42,
  iceBlueCore: 0.95,
  fluidity: 1.0,
  organicWarp: 0.25,
  innerRadiance: 0.85,
  outerHaloSoftness: 0.22,
  breathingSpeed: 1.0,
  voiceReactivity: 1.0,
  precessionRate: 0.8,
  overallScale: 1.0,
};

export interface RenderOrbOptions {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  time: number; // in seconds
  mode: OrbMode;
  audioLevel?: number; // 0 to 1
  audioFreqData?: Uint8Array | null;
  params?: OrbParameters;
  isStaticKeyframe?: boolean;
}

/**
 * Renders the authentic ChatGPT-style Voice Blue Cloud Orb.
 * Features a rich electric periwinkle/cornflower blue atmosphere,
 * layered billowing white cumulus cloud mist across the lower-mid zone,
 * organic fluid turbulence, voice audio reactivity, and clean alpha transparency.
 */
export function renderAlexOrb({
  ctx,
  width,
  height,
  time,
  mode,
  audioLevel = 0,
  audioFreqData = null,
  params = DEFAULT_ORB_PARAMS,
  isStaticKeyframe = false,
}: RenderOrbOptions) {
  // Clear canvas (preserving transparent alpha background)
  ctx.clearRect(0, 0, width, height);

  const cx = width / 2;
  const cy = height / 2;
  const minDim = Math.min(width, height);
  const baseRadius = (minDim * 0.38) * params.overallScale;

  if (baseRadius <= 2) return;

  // Process audio frequency energies
  let bassEnergy = audioLevel * 0.7;
  let midEnergy = audioLevel * 0.5;
  let highEnergy = audioLevel * 0.3;

  if (audioFreqData && audioFreqData.length > 16) {
    let bSum = 0;
    let mSum = 0;
    let hSum = 0;
    const len = audioFreqData.length;
    const bEnd = Math.floor(len * 0.15);
    const mEnd = Math.floor(len * 0.5);

    for (let i = 0; i < bEnd; i++) bSum += audioFreqData[i];
    for (let i = bEnd; i < mEnd; i++) mSum += audioFreqData[i];
    for (let i = mEnd; i < len; i++) hSum += audioFreqData[i];

    bassEnergy = Math.max(audioLevel, (bSum / (bEnd || 1)) / 255) * params.voiceReactivity;
    midEnergy = Math.max(audioLevel * 0.8, (mSum / ((mEnd - bEnd) || 1)) / 255) * params.voiceReactivity;
    highEnergy = (hSum / ((len - mEnd) || 1) / 255) * params.voiceReactivity;
  }

  // State-specific motion dynamics
  let speedMultiplier = params.fluidity;
  let breatheAmp = 0.045;
  let breatheSpeed = 1.3 * params.breathingSpeed;
  let voiceExpansion = 1.0 + (bassEnergy * 0.14 + midEnergy * 0.08);
  let cloudActivity = 1.0 + (midEnergy * 0.6 + bassEnergy * 0.4);
  let swirlSpeed = 0.2 * params.precessionRate;

  switch (mode) {
    case 'idle':
      speedMultiplier *= 0.75;
      breatheAmp = 0.04;
      breatheSpeed = 1.1 * params.breathingSpeed;
      break;
    case 'listening':
      speedMultiplier *= 1.15;
      breatheAmp = 0.07;
      breatheSpeed = 1.8 * params.breathingSpeed;
      voiceExpansion += 0.03;
      cloudActivity *= 1.25;
      break;
    case 'thinking':
      speedMultiplier *= 1.7;
      breatheAmp = 0.035;
      breatheSpeed = 2.4 * params.breathingSpeed;
      swirlSpeed = 0.9 * params.precessionRate;
      cloudActivity *= 1.4;
      break;
    case 'speaking':
      speedMultiplier *= 1.4;
      breatheAmp = 0.065;
      breatheSpeed = 2.0 * params.breathingSpeed;
      cloudActivity *= 1.6;
      break;
    case 'muted':
      speedMultiplier *= 0.25;
      breatheAmp = 0.015;
      breatheSpeed = 0.6 * params.breathingSpeed;
      voiceExpansion = 0.95;
      break;
  }

  // Rhythmic organic breathing oscillation
  const breath = Math.sin(time * breatheSpeed) * breatheAmp + 
                 Math.sin(time * breatheSpeed * 0.48 + 1.2) * (breatheAmp * 0.35);
  
  const currentRadius = baseRadius * (1 + breath) * voiceExpansion;
  const t = time * speedMultiplier;

  ctx.save();

  // -------------------------------------------------------------
  // LAYER 1: Subtle Ambient Outer Bloom / Halo
  // -------------------------------------------------------------
  if (params.outerHaloSoftness > 0.02) {
    const haloRadius = currentRadius * (1.3 + params.outerHaloSoftness * 0.35);
    const haloGrad = ctx.createRadialGradient(cx, cy, currentRadius * 0.6, cx, cy, haloRadius);
    
    const haloAlpha = Math.min(0.24, (0.08 + (bassEnergy * 0.07)) * params.outerHaloSoftness * params.azureVibrancy);
    haloGrad.addColorStop(0, `rgba(79, 117, 254, ${haloAlpha * 1.3})`);
    haloGrad.addColorStop(0.4, `rgba(59, 130, 246, ${haloAlpha * 0.8})`);
    haloGrad.addColorStop(0.75, `rgba(37, 99, 235, ${haloAlpha * 0.25})`);
    haloGrad.addColorStop(1, 'rgba(30, 58, 138, 0)');

    ctx.fillStyle = haloGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, haloRadius, 0, Math.PI * 2);
    ctx.fill();
  }

  // -------------------------------------------------------------
  // LAYER 2: Spherical Clip Mask with Soft Boundary
  // -------------------------------------------------------------
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, currentRadius, 0, Math.PI * 2);
  ctx.clip();

  // -------------------------------------------------------------
  // LAYER 3: Rich Blue Atmospheric Sky Base (ChatGPT Iconic Periwinkle)
  // Top: Electric sky blue, Center: Cornflower/Periwinkle, Bottom: Deep cobalt
  // -------------------------------------------------------------
  const baseGrad = ctx.createLinearGradient(
    cx - currentRadius * 0.3,
    cy - currentRadius,
    cx + currentRadius * 0.2,
    cy + currentRadius
  );

  // Vibrant periwinkle palette matching ChatGPT Voice Orb
  baseGrad.addColorStop(0, `rgba(99, 130, 255, ${0.98 * params.azureVibrancy})`); // Top-light vibrant sky
  baseGrad.addColorStop(0.32, `rgba(67, 97, 238, ${0.96 * params.azureVibrancy})`); // Rich cornflower periwinkle
  baseGrad.addColorStop(0.65, `rgba(58, 86, 246, ${0.94 * params.azureVibrancy})`); // Core royal blue
  baseGrad.addColorStop(0.9, `rgba(37, 62, 184, ${0.96 * params.cobaltWeight})`);  // Lower cobalt
  baseGrad.addColorStop(1, `rgba(24, 40, 135, ${0.98 * params.cobaltWeight})`);    // Deep bottom anchor

  ctx.fillStyle = baseGrad;
  ctx.fillRect(cx - currentRadius, cy - currentRadius, currentRadius * 2, currentRadius * 2);

  // Top-left ambient illumination highlight
  const topLightGrad = ctx.createRadialGradient(
    cx - currentRadius * 0.28,
    cy - currentRadius * 0.35,
    currentRadius * 0.05,
    cx,
    cy,
    currentRadius * 0.95
  );
  topLightGrad.addColorStop(0, `rgba(165, 195, 255, ${0.45 * params.azureVibrancy})`);
  topLightGrad.addColorStop(0.5, `rgba(99, 130, 255, ${0.2 * params.azureVibrancy})`);
  topLightGrad.addColorStop(1, 'rgba(67, 97, 238, 0)');

  ctx.fillStyle = topLightGrad;
  ctx.fillRect(cx - currentRadius, cy - currentRadius, currentRadius * 2, currentRadius * 2);

  // -------------------------------------------------------------
  // LAYER 4: Deep Ambient Mist Baseline (Soft background fog across mid-low)
  // -------------------------------------------------------------
  const mistBaseY = cy + currentRadius * (params.cloudElevation - 0.5) * 0.8;
  const mistGrad = ctx.createLinearGradient(
    cx,
    mistBaseY - currentRadius * 0.45,
    cx,
    mistBaseY + currentRadius * 0.7
  );

  const density = params.cloudDensity;
  mistGrad.addColorStop(0, 'rgba(255, 255, 255, 0)');
  mistGrad.addColorStop(0.28, `rgba(224, 242, 254, ${0.45 * density})`);
  mistGrad.addColorStop(0.6, `rgba(186, 230, 253, ${0.6 * density})`);
  mistGrad.addColorStop(0.85, `rgba(147, 197, 253, ${0.35 * density})`);
  mistGrad.addColorStop(1, 'rgba(59, 130, 246, 0)');

  ctx.fillStyle = mistGrad;
  ctx.fillRect(cx - currentRadius, cy - currentRadius, currentRadius * 2, currentRadius * 2);

  // -------------------------------------------------------------
  // LAYER 5: Billowing White Cumulus Cloud Formations (The Signature Cloud)
  // Multi-cluster procedural cloud puffs drifting and billowing horizontally
  // -------------------------------------------------------------
  const cloudPuffs = [
    // [relBaseX, relBaseY, relSize, speedRate, phase, opacity]
    [-0.55, 0.05, 0.42, 0.32, 0.0, 0.95],
    [-0.28, -0.04, 0.48, 0.38, 1.3, 1.0],
    [0.02, 0.02, 0.52, 0.34, 2.7, 1.0],
    [0.32, -0.06, 0.46, 0.36, 4.1, 0.96],
    [0.58, 0.08, 0.40, 0.30, 5.4, 0.92],
    // Secondary lower volume puffs
    [-0.42, 0.22, 0.38, 0.28, 0.8, 0.85],
    [-0.10, 0.18, 0.45, 0.32, 2.2, 0.92],
    [0.22, 0.20, 0.42, 0.30, 3.6, 0.88],
    [0.48, 0.24, 0.36, 0.26, 4.9, 0.82],
    // Top wispy cumulus crests
    [-0.20, -0.16, 0.32, 0.42, 1.9, 0.75],
    [0.12, -0.18, 0.35, 0.40, 3.3, 0.78],
    [0.38, -0.12, 0.28, 0.44, 4.7, 0.70],
  ];

  const cloudBaseY = cy + currentRadius * (params.cloudElevation - 0.45) * 0.75;
  const warp = params.organicWarp;

  for (let i = 0; i < cloudPuffs.length; i++) {
    const [relX, relY, relSize, pSpeed, phase, alpha] = cloudPuffs[i];
    
    // Continuous horizontal cloud drift with wraparound
    const driftSpan = currentRadius * 2.6;
    const rawX = relX * currentRadius * 1.8 + t * (pSpeed * 28 * cloudActivity);
    const wrapX = (((rawX % driftSpan) + driftSpan) % driftSpan) - driftSpan / 2;

    // Harmonic undulating vertical billow
    const waveY = Math.sin(t * 1.3 * pSpeed + phase + wrapX * 0.02) * (currentRadius * 0.12 * warp * cloudActivity) +
                  Math.cos(t * 0.8 + phase * 1.7) * (currentRadius * 0.06 * warp);

    // Swirl effect when thinking
    let px = cx + wrapX;
    let py = cloudBaseY + relY * currentRadius + waveY;

    if (mode === 'thinking' || swirlSpeed > 0.3) {
      const dx = px - cx;
      const dy = py - cy;
      const angle = Math.atan2(dy, dx) + t * swirlSpeed * 0.6;
      const dist = Math.sqrt(dx * dx + dy * dy);
      px = cx + Math.cos(angle) * dist;
      py = cy + Math.sin(angle) * dist;
    }

    const puffRadius = currentRadius * relSize * (1 + 0.1 * Math.sin(t * 1.1 + phase)) * (1 + bassEnergy * 0.18);
    const puffAlpha = alpha * density;

    const puffGrad = ctx.createRadialGradient(
      px - puffRadius * 0.15,
      py - puffRadius * 0.2,
      puffRadius * 0.02,
      px,
      py,
      puffRadius
    );

    // Realistic cumulus cloud colors: pure brilliant white core -> soft pale sky -> translucent periwinkle
    puffGrad.addColorStop(0, `rgba(255, 255, 255, ${Math.min(1.0, puffAlpha * 0.98)})`);
    puffGrad.addColorStop(0.28, `rgba(245, 250, 255, ${Math.min(1.0, puffAlpha * 0.9)})`);
    puffGrad.addColorStop(0.55, `rgba(220, 240, 255, ${Math.min(0.85, puffAlpha * 0.68)})`);
    puffGrad.addColorStop(0.82, `rgba(165, 210, 255, ${Math.min(0.5, puffAlpha * 0.28)})`);
    puffGrad.addColorStop(1, 'rgba(67, 97, 238, 0)');

    ctx.fillStyle = puffGrad;
    ctx.beginPath();
    ctx.arc(px, py, puffRadius, 0, Math.PI * 2);
    ctx.fill();
  }

  // -------------------------------------------------------------
  // LAYER 6: Organic Wispy Cloud Mist Strands (Fractal Wave Contours)
  // -------------------------------------------------------------
  const waveCount = 3;
  for (let w = 0; w < waveCount; w++) {
    const waveYOffset = cloudBaseY + (w - 1) * currentRadius * 0.14;
    const wavePhase = t * (0.8 + w * 0.3) + w * 2.1;
    const waveAmp = currentRadius * (0.05 + w * 0.03) * warp * cloudActivity;

    ctx.beginPath();
    ctx.moveTo(cx - currentRadius, cy + currentRadius);
    ctx.lineTo(cx - currentRadius, waveYOffset);

    const steps = 32;
    for (let s = 0; s <= steps; s++) {
      const sx = cx - currentRadius + (s / steps) * (currentRadius * 2);
      const normX = (s / steps) * Math.PI * 4;
      const sy = waveYOffset + 
                 Math.sin(normX + wavePhase) * waveAmp +
                 Math.cos(normX * 1.8 - wavePhase * 0.7) * (waveAmp * 0.4) +
                 Math.sin(normX * 0.5 + t * 0.5) * (waveAmp * 0.5);
      ctx.lineTo(sx, sy);
    }

    ctx.lineTo(cx + currentRadius, cy + currentRadius);
    ctx.closePath();

    const waveGrad = ctx.createLinearGradient(cx, waveYOffset - waveAmp, cx, waveYOffset + currentRadius * 0.6);
    const wAlpha = (0.28 - w * 0.06) * density * params.iceBlueCore;
    waveGrad.addColorStop(0, `rgba(255, 255, 255, ${wAlpha * 1.2})`);
    waveGrad.addColorStop(0.35, `rgba(224, 242, 254, ${wAlpha * 0.85})`);
    waveGrad.addColorStop(0.8, `rgba(186, 230, 253, ${wAlpha * 0.3})`);
    waveGrad.addColorStop(1, 'rgba(59, 130, 246, 0)');

    ctx.fillStyle = waveGrad;
    ctx.fill();
  }

  // -------------------------------------------------------------
  // LAYER 7: Internal Pale Ice-Blue Core Luminescence
  // Shines softly through the cloud body, denoting consciousness
  // -------------------------------------------------------------
  const coreX = cx + Math.sin(t * 0.7) * (currentRadius * 0.05 * warp);
  const coreY = cloudBaseY + Math.cos(t * 0.6) * (currentRadius * 0.05 * warp);
  const coreRadius = currentRadius * (0.45 + 0.08 * Math.sin(t * 1.5)) * (1 + midEnergy * 0.25);

  const coreGrad = ctx.createRadialGradient(
    coreX,
    coreY,
    0,
    coreX,
    coreY,
    coreRadius
  );

  const coreAlpha = Math.min(1.0, (0.75 + 0.15 * Math.sin(t * 1.4)) * params.iceBlueCore * params.innerRadiance);
  coreGrad.addColorStop(0, `rgba(255, 255, 255, ${Math.min(0.95, coreAlpha * 0.95)})`);
  coreGrad.addColorStop(0.3, `rgba(240, 249, 255, ${Math.min(0.85, coreAlpha * 0.75)})`);
  coreGrad.addColorStop(0.65, `rgba(186, 230, 253, ${Math.min(0.6, coreAlpha * 0.4)})`);
  coreGrad.addColorStop(1, 'rgba(67, 97, 238, 0)');

  ctx.fillStyle = coreGrad;
  ctx.beginPath();
  ctx.arc(coreX, coreY, coreRadius, 0, Math.PI * 2);
  ctx.fill();

  // -------------------------------------------------------------
  // LAYER 8: Volumetric Spherical Shading & Glass Rim Curvature
  // Darkens the bottom edge slightly and gives tangible 3D spherical depth
  // -------------------------------------------------------------
  const sphereShadeGrad = ctx.createRadialGradient(
    cx - currentRadius * 0.25,
    cy - currentRadius * 0.3,
    currentRadius * 0.3,
    cx,
    cy,
    currentRadius
  );
  sphereShadeGrad.addColorStop(0, 'rgba(255, 255, 255, 0.05)');
  sphereShadeGrad.addColorStop(0.7, 'rgba(0, 0, 0, 0)');
  sphereShadeGrad.addColorStop(0.92, 'rgba(15, 23, 85, 0.18)');
  sphereShadeGrad.addColorStop(1, 'rgba(10, 15, 65, 0.35)');

  ctx.fillStyle = sphereShadeGrad;
  ctx.fillRect(cx - currentRadius, cy - currentRadius, currentRadius * 2, currentRadius * 2);

  ctx.restore(); // Restore clip

  // -------------------------------------------------------------
  // LAYER 9: Ultra-Soft Perimeter Feathering (Antialiased Soft Edge)
  // Ensures boundary is beautifully smooth and never sharp/pixelated
  // -------------------------------------------------------------
  const featherGrad = ctx.createRadialGradient(
    cx,
    cy,
    currentRadius * 0.94,
    cx,
    cy,
    currentRadius * 1.02
  );
  featherGrad.addColorStop(0, 'rgba(67, 97, 238, 0)');
  featherGrad.addColorStop(0.6, `rgba(67, 97, 238, ${0.15 * params.azureVibrancy})`);
  featherGrad.addColorStop(1, 'rgba(67, 97, 238, 0)');

  ctx.fillStyle = featherGrad;
  ctx.beginPath();
  ctx.arc(cx, cy, currentRadius * 1.02, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore(); // Restore root canvas state
}

/**
 * Curated Archetype Static Keyframes for ChatGPT-Style Voice Orb
 */
export const CURATED_KEYFRAMES: {
  id: string;
  title: string;
  subtitle: string;
  mode: OrbMode;
  timeOffset: number;
  params: OrbParameters;
  description: string;
}[] = [
  {
    id: 'kf-prime',
    title: 'Balanced Living Cloud Orb (Primary Keyframe)',
    subtitle: 'Golden equilibrium state matching the ChatGPT voice orb atmosphere',
    mode: 'idle',
    timeOffset: 3.42,
    params: {
      ...DEFAULT_ORB_PARAMS,
      cobaltWeight: 0.85,
      azureVibrancy: 0.94,
      cloudDensity: 0.9,
      cloudElevation: 0.42,
      iceBlueCore: 0.95,
      organicWarp: 0.25,
      innerRadiance: 0.88,
      outerHaloSoftness: 0.22,
    },
    description: 'The definitive keyframe specification: rich periwinkle blue atmosphere with soft billowing white cumulus clouds floating across the lower-mid region, optimized for maximum legibility on both dark and light surfaces.',
  },
  {
    id: 'kf-listening',
    title: 'Receptive Fluid Mist (Listening)',
    subtitle: 'Sensitized cloud mist expanding and undulating to voice input',
    mode: 'listening',
    timeOffset: 6.88,
    params: {
      ...DEFAULT_ORB_PARAMS,
      cobaltWeight: 0.82,
      azureVibrancy: 0.96,
      cloudDensity: 0.94,
      cloudElevation: 0.40,
      iceBlueCore: 0.98,
      organicWarp: 0.32,
      innerRadiance: 0.92,
      outerHaloSoftness: 0.26,
    },
    description: 'Expanded, receptive cloud configuration with delicate harmonic ripples signaling active conversational listening.',
  },
  {
    id: 'kf-thinking',
    title: 'Swirling Azure Cloud Vortex (Thinking)',
    subtitle: 'Focused, rotating cloud vortex synthesizing strategic thought',
    mode: 'thinking',
    timeOffset: 12.15,
    params: {
      ...DEFAULT_ORB_PARAMS,
      cobaltWeight: 0.92,
      azureVibrancy: 0.90,
      cloudDensity: 0.92,
      cloudElevation: 0.44,
      iceBlueCore: 0.94,
      organicWarp: 0.28,
      innerRadiance: 0.96,
      outerHaloSoftness: 0.18,
    },
    description: 'Deep, concentrated cloud swirl with rotational vortex momentum and vibrant blue containment, denoting cognitive synthesis.',
  },
  {
    id: 'kf-speaking',
    title: 'Harmonic Vocal Billows (Speaking)',
    subtitle: 'Active vocal modulation and rising cumulus cloud billows',
    mode: 'speaking',
    timeOffset: 9.64,
    params: {
      ...DEFAULT_ORB_PARAMS,
      cobaltWeight: 0.84,
      azureVibrancy: 0.98,
      cloudDensity: 0.96,
      cloudElevation: 0.38,
      iceBlueCore: 1.0,
      organicWarp: 0.30,
      innerRadiance: 0.96,
      outerHaloSoftness: 0.28,
    },
    description: 'Vibrant harmonic pulses with billowy white cloud formations cresting in synchrony with speech frequencies.',
  },
  {
    id: 'kf-compact',
    title: 'High-Contrast Compact (Dock & Status)',
    subtitle: 'Optimized for small 24px - 48px UI scale density',
    mode: 'idle',
    timeOffset: 4.8,
    params: {
      ...DEFAULT_ORB_PARAMS,
      cobaltWeight: 0.95,
      azureVibrancy: 0.96,
      cloudDensity: 0.95,
      cloudElevation: 0.44,
      iceBlueCore: 1.0,
      organicWarp: 0.18,
      innerRadiance: 0.95,
      outerHaloSoftness: 0.12,
    },
    description: 'High-density cloud-to-blue contrast engineered specifically for micro UI sizes, navigation headers, and compact docks.',
  },
];
