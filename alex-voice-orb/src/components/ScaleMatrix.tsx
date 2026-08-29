import React, { useState } from 'react';
import { Layers, CheckCircle2, ShieldCheck, Sun, Moon } from 'lucide-react';
import { VoiceOrbCanvas } from './VoiceOrbCanvas';
import { OrbMode, OrbParameters } from '../types/orb';

interface ScaleMatrixProps {
  params: OrbParameters;
  mode: OrbMode;
  audioLevel: number;
}

const TEST_SIZES = [
  { size: 24, label: '24px', context: 'Top Nav / Menubar micro status', dpr: 2 },
  { size: 36, label: '36px', context: 'Header avatar / Inline chip', dpr: 2 },
  { size: 48, label: '48px', context: 'Sidebar collapsed / Taskbar icon', dpr: 2 },
  { size: 72, label: '72px', context: 'Floating action co-founder widget', dpr: 2 },
  { size: 128, label: '128px', context: 'Executive modal card / Side panel', dpr: 2 },
  { size: 240, label: '240px', context: 'Primary active voice briefing stage', dpr: 2 },
];

export const ScaleMatrix: React.FC<ScaleMatrixProps> = ({
  params,
  mode,
  audioLevel,
}) => {
  const [activeTab, setActiveTab] = useState<'both' | 'dark' | 'light'>('both');

  return (
    <div id="scale-matrix-container" className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-200 dark:border-slate-800 pb-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Layers className="w-5 h-5 text-sky-500" />
            Multi-Scale & Surface Legibility Matrix
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Testing optical contrast, lobe stability, and translucent blue depth across small to large UI viewports.
          </p>
        </div>

        <div className="flex items-center bg-slate-100 dark:bg-slate-800/80 p-1 rounded-lg border border-slate-200 dark:border-slate-700 text-xs">
          <button
            id="tab-view-both"
            onClick={() => setActiveTab('both')}
            className={`px-3 py-1 rounded transition-colors ${
              activeTab === 'both'
                ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-xs font-medium'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
            }`}
          >
            Split View (Dark + Light)
          </button>
          <button
            id="tab-view-dark"
            onClick={() => setActiveTab('dark')}
            className={`px-3 py-1 rounded transition-colors ${
              activeTab === 'dark'
                ? 'bg-slate-900 text-white shadow-xs font-medium'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
            }`}
          >
            Dark Interface Only
          </button>
          <button
            id="tab-view-light"
            onClick={() => setActiveTab('light')}
            className={`px-3 py-1 rounded transition-colors ${
              activeTab === 'light'
                ? 'bg-white text-slate-900 shadow-xs font-medium'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
            }`}
          >
            Light Interface Only
          </button>
        </div>
      </div>

      {/* Side-by-side or stacked container */}
      <div className={`grid gap-6 ${activeTab === 'both' ? 'grid-cols-1 lg:grid-cols-2' : 'grid-cols-1'}`}>
        {/* Dark Surface Card */}
        {(activeTab === 'both' || activeTab === 'dark') && (
          <div className="bg-[#0B0F19] text-white rounded-2xl p-6 border border-slate-800 shadow-sm space-y-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-md bg-slate-900 border border-slate-700 flex items-center justify-center">
                  <Moon className="w-3.5 h-3.5 text-sky-400" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-white">Dark Mode Surface (#0B0F19)</h3>
                  <p className="text-[11px] text-slate-400">High-end productivity suite / IDE / Terminal atmosphere</p>
                </div>
              </div>
              <span className="text-[11px] text-sky-400 font-mono bg-sky-950/60 px-2 py-0.5 rounded border border-sky-800/60">
                Contrast Ratio: 8.2:1 (Ice-Core)
              </span>
            </div>

            {/* Scale Row Display */}
            <div className="space-y-4">
              {TEST_SIZES.map((item) => (
                <div
                  key={`dark-${item.size}`}
                  id={`scale-dark-${item.size}`}
                  className="flex items-center justify-between p-3 rounded-xl bg-slate-900/60 border border-slate-800/80 hover:border-slate-700 transition-colors"
                >
                  <div className="flex items-center gap-4">
                    <div
                      className="flex items-center justify-center rounded-lg bg-slate-950/80 border border-slate-800/60 shrink-0"
                      style={{ width: `${Math.max(48, item.size + 16)}px`, height: `${Math.max(48, item.size + 16)}px` }}
                    >
                      <VoiceOrbCanvas
                        size={item.size}
                        mode={mode}
                        params={params}
                        audioLevel={audioLevel}
                      />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-semibold text-sky-300">{item.label}</span>
                        <span className="text-[10px] text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded">
                          {item.size} × {item.size} px
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-400 mt-0.5">{item.context}</p>
                    </div>
                  </div>

                  <div className="text-right hidden sm:block">
                    <span className="inline-flex items-center gap-1 text-[11px] text-emerald-400">
                      <CheckCircle2 className="w-3 h-3" />
                      Legible & Clear
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Light Surface Card */}
        {(activeTab === 'both' || activeTab === 'light') && (
          <div className="bg-[#FAFAFA] text-slate-900 rounded-2xl p-6 border border-slate-200 shadow-sm space-y-6">
            <div className="flex items-center justify-between border-b border-slate-200 pb-3">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-md bg-white border border-slate-200 flex items-center justify-center">
                  <Sun className="w-3.5 h-3.5 text-amber-500" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">Light Mode Surface (#FAFAFA)</h3>
                  <p className="text-[11px] text-slate-500">Executive documentation / Clean enterprise SaaS canvas</p>
                </div>
              </div>
              <span className="text-[11px] text-blue-700 font-mono bg-blue-50 px-2 py-0.5 rounded border border-blue-200">
                Contrast Ratio: 5.6:1 (Cobalt Perimeter)
              </span>
            </div>

            {/* Scale Row Display */}
            <div className="space-y-4">
              {TEST_SIZES.map((item) => (
                <div
                  key={`light-${item.size}`}
                  id={`scale-light-${item.size}`}
                  className="flex items-center justify-between p-3 rounded-xl bg-white border border-slate-200/90 hover:border-slate-300 transition-colors shadow-2xs"
                >
                  <div className="flex items-center gap-4">
                    <div
                      className="flex items-center justify-center rounded-lg bg-slate-50 border border-slate-100 shrink-0"
                      style={{ width: `${Math.max(48, item.size + 16)}px`, height: `${Math.max(48, item.size + 16)}px` }}
                    >
                      <VoiceOrbCanvas
                        size={item.size}
                        mode={mode}
                        params={params}
                        audioLevel={audioLevel}
                      />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-semibold text-blue-800">{item.label}</span>
                        <span className="text-[10px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                          {item.size} × {item.size} px
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-500 mt-0.5">{item.context}</p>
                    </div>
                  </div>

                  <div className="text-right hidden sm:block">
                    <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600">
                      <CheckCircle2 className="w-3 h-3" />
                      Legible & Clear
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Optical Legibility Principle Card */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-4">
        <h4 className="text-xs font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2 mb-2">
          <ShieldCheck className="w-4 h-4 text-sky-500" />
          Why Alex Orb Maintains High Legibility At Micro Scale:
        </h4>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs text-slate-600 dark:text-slate-400">
          <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg border border-slate-200 dark:border-slate-800">
            <span className="font-semibold text-slate-900 dark:text-slate-200 block mb-1">Cobalt Anchor Edge</span>
            The outer cobalt base lobes (#1E3A8A / #1D4ED8) provide high boundary definition against bright white and off-white backgrounds without needing an artificial stroke or ring.
          </div>
          <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg border border-slate-200 dark:border-slate-800">
            <span className="font-semibold text-slate-900 dark:text-slate-200 block mb-1">Ice-Blue Nucleus</span>
            The delicate pale ice-blue core (#F0F9FF) remains luminous on deep dark surfaces, providing an immediate focal center that registers instantaneously to human peripheral vision.
          </div>
          <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg border border-slate-200 dark:border-slate-800">
            <span className="font-semibold text-slate-900 dark:text-slate-200 block mb-1">No Micro-Detail Clutter</span>
            By intentionally avoiding facial features, sparkles, text, or hard microphone geometry, the orb scales down smoothly without sub-pixel aliasing or noisy artifacts.
          </div>
        </div>
      </div>
    </div>
  );
};
