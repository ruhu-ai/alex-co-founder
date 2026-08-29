export type OrbMode = 'idle' | 'listening' | 'thinking' | 'speaking' | 'muted';

export interface OrbParameters {
  // Rich Blue Palette & Depth
  cobaltWeight: number;      // 0.2 to 1.0 (Deep cobalt/indigo base & bottom ambient)
  azureVibrancy: number;     // 0.3 to 1.0 (Rich periwinkle/cornflower blue vibrancy)
  
  // Billowing Cloud & Mist Atmosphere (Signature ChatGPT Voice Orb)
  cloudDensity: number;      // 0.2 to 1.0 (White cumulus cloud density & opacity)
  cloudElevation: number;    // 0.1 to 0.8 (Baseline height of cloud layer in the sphere)
  iceBlueCore: number;       // 0.2 to 1.0 (Delicate pale ice-blue core luminescence)
  
  // Fluid Dynamics & Turbulence
  fluidity: number;          // 0.2 to 2.0 (Drift velocity & billow circulation speed)
  organicWarp: number;       // 0.05 to 0.5 (Fractal wave turbulence & billowing amplitude)
  innerRadiance: number;     // 0.1 to 1.0 (Internal glowing light penetrating the clouds)
  outerHaloSoftness: number; // 0.02 to 0.5 (Subtle, clean ambient glow feathering)
  
  // Dynamics & Voice Reactivity
  breathingSpeed: number;    // Respiration cycle rate
  voiceReactivity: number;   // Audio FFT & amplitude sensitivity
  precessionRate: number;    // Harmonic swirling drift
  overallScale: number;      // Base radius scale factor (0.7 to 1.3)
}

export interface KeyframeSnapshot {
  id: string;
  title: string;
  subtitle: string;
  mode: OrbMode;
  timeOffset: number;
  params: OrbParameters;
  description: string;
}

export interface CoFounderTopic {
  id: string;
  title: string;
  category: 'Strategy' | 'Fundraising' | 'Product' | 'Architecture';
  question: string;
  response: string;
  durationSec: number;
}

