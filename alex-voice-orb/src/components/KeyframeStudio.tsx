import React, { useState, useRef } from 'react';
import { Download, Copy, Check, Sparkles, Layers, Sliders, Code2, Image as ImageIcon, Eye } from 'lucide-react';
import { CURATED_KEYFRAMES, DEFAULT_ORB_PARAMS, renderAlexOrb } from '../lib/orbRenderer';
import { VoiceOrbCanvas } from './VoiceOrbCanvas';
import { OrbMode, OrbParameters } from '../types/orb';

interface KeyframeStudioProps {
  currentParams: OrbParameters;
  currentMode: OrbMode;
}

export const KeyframeStudio: React.FC<KeyframeStudioProps> = ({
  currentParams,
  currentMode,
}) => {
  const [selectedKeyframeId, setSelectedKeyframeId] = useState<string>('kf-prime');
  const [scrubberTime, setScrubberTime] = useState<number>(3.42);
  const [exportScale, setExportScale] = useState<number>(2); // 1x, 2x, 4x
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [previewBg, setPreviewBg] = useState<'transparent' | 'dark' | 'light'>('transparent');
  const [activeCodeTab, setActiveCodeTab] = useState<'canvas' | 'react'>('canvas');

  const selectedKeyframe = CURATED_KEYFRAMES.find((k) => k.id === selectedKeyframeId) || CURATED_KEYFRAMES[0];

  const handleSelectKeyframe = (id: string) => {
    setSelectedKeyframeId(id);
    const kf = CURATED_KEYFRAMES.find((k) => k.id === id);
    if (kf) {
      setScrubberTime(kf.timeOffset);
    }
  };

  const handleDownloadPNG = () => {
    const size = 360 * exportScale;
    const offCanvas = document.createElement('canvas');
    offCanvas.width = size;
    offCanvas.height = size;
    const ctx = offCanvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    renderAlexOrb({
      ctx,
      width: size,
      height: size,
      time: scrubberTime,
      mode: selectedKeyframe.mode,
      params: selectedKeyframe.params,
      isStaticKeyframe: true,
    });

    const dataUrl = offCanvas.toDataURL('image/png');
    const link = document.createElement('a');
    link.download = `alex-voice-orb-keyframe-${selectedKeyframe.id}-${exportScale}x.png`;
    link.href = dataUrl;
    link.click();
  };

  const handleCopy = (code: string, id: string) => {
    navigator.clipboard.writeText(code);
    setCopiedCode(id);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  const canvasCodeSnippet = `// Standalone HTML5 Canvas Keyframe Renderer for ChatGPT-Style Voice Cloud Orb
function drawAlexOrb(canvas, time = ${scrubberTime.toFixed(2)}) {
  const ctx = canvas.getContext('2d', { alpha: true });
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const baseR = Math.min(w, h) * 0.38;

  ctx.clearRect(0, 0, w, h);

  // 1. Subtle Outer Ambient Halo
  const haloR = baseR * 1.35;
  const halo = ctx.createRadialGradient(cx, cy, baseR * 0.6, cx, cy, haloR);
  halo.addColorStop(0, 'rgba(79, 117, 254, 0.18)');
  halo.addColorStop(0.5, 'rgba(59, 130, 246, 0.08)');
  halo.addColorStop(1, 'rgba(30, 58, 138, 0)');
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(cx, cy, haloR, 0, Math.PI * 2);
  ctx.fill();

  // 2. Spherical Clip Mask
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, baseR, 0, Math.PI * 2);
  ctx.clip();

  // 3. Rich Periwinkle Blue Atmospheric Base
  const baseGrad = ctx.createLinearGradient(cx - baseR * 0.3, cy - baseR, cx + baseR * 0.2, cy + baseR);
  baseGrad.addColorStop(0, '#6382FF');
  baseGrad.addColorStop(0.35, '#4361EE');
  baseGrad.addColorStop(0.7, '#3A56F6');
  baseGrad.addColorStop(1, '#182887');
  ctx.fillStyle = baseGrad;
  ctx.fillRect(cx - baseR, cy - baseR, baseR * 2, baseR * 2);

  // 4. Billowing White Cumulus Cloud Mist Layer
  const cloudBaseY = cy + baseR * -0.05;
  const puffs = [
    [-0.5, 0.05, 0.42, 0.32, 0.0],
    [-0.25, -0.04, 0.48, 0.38, 1.3],
    [0.02, 0.02, 0.52, 0.34, 2.7],
    [0.3, -0.05, 0.46, 0.36, 4.1],
    [0.55, 0.08, 0.40, 0.30, 5.4],
  ];

  puffs.forEach(([relX, relY, relSize, speed, phase]) => {
    const px = cx + relX * baseR * 1.5;
    const py = cloudBaseY + relY * baseR + Math.sin(time * 1.2 + phase) * (baseR * 0.08);
    const pr = baseR * relSize;

    const g = ctx.createRadialGradient(px - pr * 0.15, py - pr * 0.2, pr * 0.02, px, py, pr);
    g.addColorStop(0, 'rgba(255, 255, 255, 0.95)');
    g.addColorStop(0.3, 'rgba(240, 249, 255, 0.85)');
    g.addColorStop(0.65, 'rgba(186, 230, 253, 0.5)');
    g.addColorStop(1, 'rgba(67, 97, 238, 0)');

    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(px, py, pr, 0, Math.PI * 2);
    ctx.fill();
  });

  // 5. Internal Pale Ice-Blue Core Glow
  const coreGrad = ctx.createRadialGradient(cx, cloudBaseY, 0, cx, cloudBaseY, baseR * 0.45);
  coreGrad.addColorStop(0, 'rgba(255, 255, 255, 0.95)');
  coreGrad.addColorStop(0.35, 'rgba(224, 242, 254, 0.75)');
  coreGrad.addColorStop(0.7, 'rgba(186, 230, 253, 0.35)');
  coreGrad.addColorStop(1, 'rgba(67, 97, 238, 0)');
  ctx.fillStyle = coreGrad;
  ctx.beginPath();
  ctx.arc(cx, cloudBaseY, baseR * 0.45, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();
}`;

  const reactCodeSnippet = `import React, { useEffect, useRef } from 'react';

export const AlexVoiceOrb: React.FC<{ size?: number }> = ({ size = 120 }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    
    // Draw static keyframe or requestAnimationFrame loop here
    canvas.width = size * 2;
    canvas.height = size * 2;
    ctx.scale(2, 2);
    // Call drawAlexOrb(canvas, 3.42)...
  }, [size]);

  return (
    <canvas 
      ref={canvasRef} 
      style={{ width: size, height: size }}
      className="select-none touch-none"
    />
  );
};`;

  return (
    <div id="keyframe-studio-container" className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-200 dark:border-slate-800 pb-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-sky-500" />
            Static Keyframe Studio & Exporter
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Pristine vector-ready static keyframes capturing the soft translucent cobalt, azure, and ice-blue living field.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Background preview toggle */}
          <div className="flex items-center bg-slate-100 dark:bg-slate-800/80 p-1 rounded-lg border border-slate-200 dark:border-slate-700 text-xs">
            <button
              id="bg-btn-transparent"
              onClick={() => setPreviewBg('transparent')}
              className={`px-2.5 py-1 rounded transition-colors ${
                previewBg === 'transparent'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              Checkerboard
            </button>
            <button
              id="bg-btn-dark"
              onClick={() => setPreviewBg('dark')}
              className={`px-2.5 py-1 rounded transition-colors ${
                previewBg === 'dark'
                  ? 'bg-slate-900 text-white shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              Dark (#0B0F19)
            </button>
            <button
              id="bg-btn-light"
              onClick={() => setPreviewBg('light')}
              className={`px-2.5 py-1 rounded transition-colors ${
                previewBg === 'light'
                  ? 'bg-white text-slate-900 shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              Light (#FAFAFA)
            </button>
          </div>
        </div>
      </div>

      {/* Main Grid: Keyframe Viewer & Archetype Selector */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left / Center Viewport */}
        <div className="lg:col-span-7 space-y-4">
          <div
            id="keyframe-canvas-stage"
            className={`relative rounded-2xl border border-slate-200 dark:border-slate-800 p-8 flex flex-col items-center justify-center min-h-[380px] overflow-hidden transition-colors ${
              previewBg === 'transparent'
                ? 'bg-[linear-gradient(45deg,#f1f5f9_25%,transparent_25%),linear-gradient(-45deg,#f1f5f9_25%,transparent_25%),linear-gradient(45deg,transparent_75%,#f1f5f9_75%),linear-gradient(-45deg,transparent_75%,#f1f5f9_75%)] bg-[size:20px_20px] dark:bg-[linear-gradient(45deg,#131b2e_25%,transparent_25%),linear-gradient(-45deg,#131b2e_25%,transparent_25%),linear-gradient(45deg,transparent_75%,#131b2e_75%),linear-gradient(-45deg,transparent_75%,#131b2e_75%)]'
                : previewBg === 'dark'
                ? 'bg-[#0B0F19]'
                : 'bg-[#FAFAFA]'
            }`}
          >
            {/* The Orb Canvas in Static Keyframe Mode */}
            <div className="relative group">
              <VoiceOrbCanvas
                id="keyframe-main-orb"
                size={290}
                mode={selectedKeyframe.mode}
                params={selectedKeyframe.params}
                isStatic={true}
                staticTime={scrubberTime}
                className="drop-shadow-sm"
              />
            </div>

            {/* Overlay Specs badge */}
            <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400 bg-white/80 dark:bg-slate-900/80 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-200/80 dark:border-slate-800">
              <span className="font-mono">t = {scrubberTime.toFixed(2)}s • {selectedKeyframe.mode.toUpperCase()}</span>
              <span>Layered Cobalt + Azure + Ice-Blue</span>
            </div>
          </div>

          {/* Time Scrubber */}
          <div className="bg-slate-50 dark:bg-slate-900/60 p-4 rounded-xl border border-slate-200 dark:border-slate-800 space-y-2">
            <div className="flex items-center justify-between text-xs font-medium text-slate-700 dark:text-slate-300">
              <span className="flex items-center gap-1.5">
                <Sliders className="w-3.5 h-3.5 text-sky-500" />
                Phase Time Scrubber
              </span>
              <span className="font-mono text-slate-500">{scrubberTime.toFixed(2)}s</span>
            </div>
            <input
              id="time-scrubber-slider"
              type="range"
              min="0"
              max="20"
              step="0.02"
              value={scrubberTime}
              onChange={(e) => setScrubberTime(parseFloat(e.target.value))}
              className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-sky-500"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono">
              <span>0.00s</span>
              <span>5.00s</span>
              <span>10.00s</span>
              <span>15.00s</span>
              <span>20.00s</span>
            </div>
          </div>

          {/* Export Action Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xs">
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500 dark:text-slate-400 font-medium">Export Scale:</span>
              {[1, 2, 4].map((s) => (
                <button
                  key={s}
                  id={`scale-btn-${s}x`}
                  onClick={() => setExportScale(s)}
                  className={`px-2.5 py-1 text-xs rounded-md border font-mono transition-colors ${
                    exportScale === s
                      ? 'bg-sky-500 text-white border-sky-600 shadow-xs'
                      : 'bg-slate-50 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-700 hover:border-slate-300'
                  }`}
                >
                  {s}x ({360 * s}px)
                </button>
              ))}
            </div>

            <button
              id="download-png-btn"
              onClick={handleDownloadPNG}
              className="flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 dark:bg-sky-500 dark:hover:bg-sky-400 text-white text-xs font-medium rounded-lg transition-colors shadow-xs"
            >
              <Download className="w-4 h-4" />
              Download Keyframe PNG (Alpha)
            </button>
          </div>
        </div>

        {/* Right Panel: Curated Keyframes & Code Export */}
        <div className="lg:col-span-5 space-y-4">
          {/* Keyframe Archetypes List */}
          <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-4 space-y-3">
            <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
              Curated Keyframe Specifications
            </h3>

            <div className="space-y-2">
              {CURATED_KEYFRAMES.map((kf) => {
                const isSelected = kf.id === selectedKeyframeId;
                return (
                  <button
                    key={kf.id}
                    id={`kf-select-${kf.id}`}
                    onClick={() => handleSelectKeyframe(kf.id)}
                    className={`w-full text-left p-3 rounded-xl border transition-all flex items-start gap-3 ${
                      isSelected
                        ? 'bg-sky-50/80 dark:bg-sky-950/30 border-sky-300 dark:border-sky-700/60 shadow-xs'
                        : 'bg-slate-50/60 dark:bg-slate-800/40 border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
                    }`}
                  >
                    <div className="shrink-0 w-12 h-12 rounded-lg bg-slate-900 flex items-center justify-center overflow-hidden border border-slate-800">
                      <VoiceOrbCanvas
                        size={48}
                        mode={kf.mode}
                        params={kf.params}
                        isStatic={true}
                        staticTime={kf.timeOffset}
                      />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between">
                        <h4 className="text-xs font-semibold text-slate-900 dark:text-slate-100 truncate">
                          {kf.title}
                        </h4>
                        <span className="text-[10px] font-mono text-slate-400 shrink-0">
                          t={kf.timeOffset}s
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-500 dark:text-slate-400 line-clamp-2 mt-0.5">
                        {kf.description}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Design System Checklist & Validation */}
          <div className="bg-slate-50 dark:bg-slate-900/60 rounded-xl border border-slate-200 dark:border-slate-800 p-4 space-y-2.5">
            <h3 className="text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5">
              <Check className="w-4 h-4 text-emerald-500" />
              Design Constraint Verification
            </h3>
            <ul className="text-[11px] text-slate-600 dark:text-slate-400 space-y-1.5">
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                <span><strong>Color Palette</strong>: Cobalt (#1E3A8A), Azure (#0284C7 / #2563EB), Pale Ice-Blue (#BAE6FD / #F0F9FF).</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                <span><strong>Organic Structure</strong>: Fluid overlapping harmonic lobes; non-rigid boundary.</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                <span><strong>Restrained Lighting</strong>: Delicate inner glow, subtle ambient halo.</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                <span><strong>Pure Transparency</strong>: Clean alpha channel for seamless light/dark embedding.</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                <span><strong>Avoidances Enforced</strong>: No microphones, rings, faces, glass sphere glares, or rainbow spectrums.</span>
              </li>
            </ul>
          </div>

          {/* Code Snippet Export */}
          <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 bg-slate-950 border-b border-slate-800">
              <div className="flex items-center gap-2">
                <button
                  id="code-tab-canvas"
                  onClick={() => setActiveCodeTab('canvas')}
                  className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                    activeCodeTab === 'canvas'
                      ? 'bg-slate-800 text-sky-400'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  HTML5 Canvas
                </button>
                <button
                  id="code-tab-react"
                  onClick={() => setActiveCodeTab('react')}
                  className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                    activeCodeTab === 'react'
                      ? 'bg-slate-800 text-sky-400'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  React Component
                </button>
              </div>

              <button
                id="copy-code-btn"
                onClick={() =>
                  handleCopy(
                    activeCodeTab === 'canvas' ? canvasCodeSnippet : reactCodeSnippet,
                    'code'
                  )
                }
                className="flex items-center gap-1 text-[11px] text-slate-300 hover:text-white px-2 py-1 rounded bg-slate-800/80 hover:bg-slate-800 transition-colors"
              >
                {copiedCode === 'code' ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    Copy Code
                  </>
                )}
              </button>
            </div>

            <div className="p-3 max-h-48 overflow-y-auto font-mono text-[11px] text-slate-300 leading-relaxed scrollbar-thin">
              <pre>
                <code>{activeCodeTab === 'canvas' ? canvasCodeSnippet : reactCodeSnippet}</code>
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
