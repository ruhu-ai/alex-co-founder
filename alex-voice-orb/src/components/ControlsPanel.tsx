import React from 'react';
import { Sliders, RotateCcw, Activity, Palette, Sparkles, Waves, Cloud, Zap } from 'lucide-react';
import { OrbMode, OrbParameters } from '../types/orb';
import { DEFAULT_ORB_PARAMS } from '../lib/orbRenderer';

interface ControlsPanelProps {
  params: OrbParameters;
  onChangeParams: (newParams: OrbParameters) => void;
  mode: OrbMode;
  onChangeMode: (newMode: OrbMode) => void;
}

export const ControlsPanel: React.FC<ControlsPanelProps> = ({
  params,
  onChangeParams,
  mode,
  onChangeMode,
}) => {
  const updateParam = (key: keyof OrbParameters, value: number) => {
    onChangeParams({
      ...params,
      [key]: value,
    });
  };

  const handleReset = () => {
    onChangeParams(DEFAULT_ORB_PARAMS);
  };

  const applyPreset = (presetName: string) => {
    switch (presetName) {
      case 'chatgpt-classic':
        onChangeParams({
          ...DEFAULT_ORB_PARAMS,
          cobaltWeight: 0.85,
          azureVibrancy: 0.94,
          cloudDensity: 0.9,
          cloudElevation: 0.42,
          iceBlueCore: 0.95,
          fluidity: 1.0,
          organicWarp: 0.25,
          innerRadiance: 0.88,
          outerHaloSoftness: 0.22,
        });
        break;
      case 'billowing-mist':
        onChangeParams({
          ...DEFAULT_ORB_PARAMS,
          cobaltWeight: 0.82,
          azureVibrancy: 0.98,
          cloudDensity: 1.0,
          cloudElevation: 0.38,
          iceBlueCore: 1.0,
          fluidity: 1.25,
          organicWarp: 0.35,
          innerRadiance: 0.95,
          outerHaloSoftness: 0.26,
        });
        break;
      case 'deep-periwinkle':
        onChangeParams({
          ...DEFAULT_ORB_PARAMS,
          cobaltWeight: 0.98,
          azureVibrancy: 0.96,
          cloudDensity: 0.78,
          cloudElevation: 0.46,
          iceBlueCore: 0.88,
          fluidity: 0.85,
          organicWarp: 0.2,
          innerRadiance: 0.8,
          outerHaloSoftness: 0.18,
        });
        break;
      case 'compact-dock':
        onChangeParams({
          ...DEFAULT_ORB_PARAMS,
          cobaltWeight: 0.95,
          azureVibrancy: 0.96,
          cloudDensity: 0.95,
          cloudElevation: 0.44,
          iceBlueCore: 1.0,
          fluidity: 0.9,
          organicWarp: 0.18,
          innerRadiance: 0.95,
          outerHaloSoftness: 0.12,
        });
        break;
    }
  };

  return (
    <div id="controls-panel-container" className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 p-5 space-y-6 shadow-xs">
      {/* Top Title & Presets */}
      <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <Sliders className="w-4 h-4 text-sky-500" />
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Orb Geometry & Atmosphere Controls
          </h3>
        </div>
        <button
          id="btn-reset-params"
          onClick={handleReset}
          className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 transition-colors"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Reset Defaults
        </button>
      </div>

      {/* Mode State Selector */}
      <div className="space-y-2">
        <label className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
          <Activity className="w-3.5 h-3.5 text-sky-500" />
          Operating State
        </label>
        <div className="grid grid-cols-5 gap-1.5 p-1 bg-slate-100 dark:bg-slate-800/70 rounded-xl border border-slate-200 dark:border-slate-750 text-xs">
          {(['idle', 'listening', 'thinking', 'speaking', 'muted'] as OrbMode[]).map((m) => (
            <button
              key={m}
              id={`mode-btn-${m}`}
              onClick={() => onChangeMode(m)}
              className={`py-1.5 px-2 rounded-lg font-medium capitalize text-[11px] transition-all text-center ${
                mode === m
                  ? 'bg-white dark:bg-slate-700 text-sky-600 dark:text-sky-300 shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      {/* Preset Quick Chips */}
      <div className="space-y-1.5">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">
          Atmospheric Archetypes
        </span>
        <div className="flex flex-wrap gap-1.5">
          <button
            id="preset-btn-chatgpt"
            onClick={() => applyPreset('chatgpt-classic')}
            className="px-2.5 py-1 text-[11px] rounded-lg bg-sky-500/10 hover:bg-sky-500/20 text-sky-700 dark:text-sky-300 border border-sky-500/30 font-medium transition-colors"
          >
            ChatGPT Cloud Orb
          </button>
          <button
            id="preset-btn-mist"
            onClick={() => applyPreset('billowing-mist')}
            className="px-2.5 py-1 text-[11px] rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-medium transition-colors"
          >
            Billowing Cumulus Mist
          </button>
          <button
            id="preset-btn-periwinkle"
            onClick={() => applyPreset('deep-periwinkle')}
            className="px-2.5 py-1 text-[11px] rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-medium transition-colors"
          >
            Deep Periwinkle Sky
          </button>
          <button
            id="preset-btn-dock"
            onClick={() => applyPreset('compact-dock')}
            className="px-2.5 py-1 text-[11px] rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-medium transition-colors"
          >
            Compact Micro Scale
          </button>
        </div>
      </div>

      {/* Section 1: Billowing Cloud Mist Layer */}
      <div className="space-y-3 pt-2 border-t border-slate-200 dark:border-slate-800">
        <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5">
          <Cloud className="w-3.5 h-3.5 text-sky-400" />
          Cloud Mist Atmosphere (Signature Interior)
        </h4>

        {/* Cloud Density */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>White Cumulus Mist Density</span>
            <span className="font-mono text-[11px]">{(params.cloudDensity * 100).toFixed(0)}%</span>
          </div>
          <input
            id="slider-cloud-density"
            type="range"
            min="0.2"
            max="1.0"
            step="0.02"
            value={params.cloudDensity}
            onChange={(e) => updateParam('cloudDensity', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-sky-400"
          />
        </div>

        {/* Cloud Elevation */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Cloud Layer Elevation Horizon</span>
            <span className="font-mono text-[11px]">{(params.cloudElevation * 100).toFixed(0)}%</span>
          </div>
          <input
            id="slider-cloud-elevation"
            type="range"
            min="0.1"
            max="0.8"
            step="0.02"
            value={params.cloudElevation}
            onChange={(e) => updateParam('cloudElevation', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-sky-400"
          />
        </div>

        {/* Pale Ice Core Luminescence */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Pale Ice Core Luminescence</span>
            <span className="font-mono text-[11px]">{(params.iceBlueCore * 100).toFixed(0)}%</span>
          </div>
          <input
            id="slider-ice-blue-core"
            type="range"
            min="0.2"
            max="1.0"
            step="0.05"
            value={params.iceBlueCore}
            onChange={(e) => updateParam('iceBlueCore', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-sky-300"
          />
        </div>
      </div>

      {/* Section 2: Blue Sky Palette & Contrast */}
      <div className="space-y-3 pt-2 border-t border-slate-200 dark:border-slate-800">
        <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5">
          <Palette className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
          Blue Sky Palette & Contrast
        </h4>

        {/* Azure Vibrancy */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Periwinkle / Cornflower Sky Vibrancy (#4361EE)</span>
            <span className="font-mono text-[11px]">{(params.azureVibrancy * 100).toFixed(0)}%</span>
          </div>
          <input
            id="slider-azure-vibrancy"
            type="range"
            min="0.3"
            max="1.0"
            step="0.02"
            value={params.azureVibrancy}
            onChange={(e) => updateParam('azureVibrancy', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-600"
          />
        </div>

        {/* Cobalt Base Weight */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Cobalt/Indigo Deep Anchor (#1E35B8)</span>
            <span className="font-mono text-[11px]">{(params.cobaltWeight * 100).toFixed(0)}%</span>
          </div>
          <input
            id="slider-cobalt-weight"
            type="range"
            min="0.2"
            max="1.0"
            step="0.05"
            value={params.cobaltWeight}
            onChange={(e) => updateParam('cobaltWeight', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-900"
          />
        </div>
      </div>

      {/* Section 3: Fluid Dynamics & Turbulence */}
      <div className="space-y-3 pt-2 border-t border-slate-200 dark:border-slate-800">
        <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5">
          <Waves className="w-3.5 h-3.5 text-sky-500" />
          Fluid Motion & Wave Dynamics
        </h4>

        {/* Fluid Drift Speed */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Cloud Drift & Billow Velocity</span>
            <span className="font-mono text-[11px]">{params.fluidity.toFixed(1)}x</span>
          </div>
          <input
            id="slider-fluidity"
            type="range"
            min="0.2"
            max="2.0"
            step="0.1"
            value={params.fluidity}
            onChange={(e) => updateParam('fluidity', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-sky-500"
          />
        </div>

        {/* Organic Warp / Turbulence */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Fractal Cloud Turbulence & Wave Height</span>
            <span className="font-mono text-[11px]">{(params.organicWarp * 100).toFixed(0)}%</span>
          </div>
          <input
            id="slider-organic-warp"
            type="range"
            min="0.05"
            max="0.5"
            step="0.02"
            value={params.organicWarp}
            onChange={(e) => updateParam('organicWarp', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-sky-500"
          />
        </div>

        {/* Outer Halo Glow */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Soft Ambient Halo Bloom</span>
            <span className="font-mono text-[11px]">{(params.outerHaloSoftness * 100).toFixed(0)}%</span>
          </div>
          <input
            id="slider-outer-halo"
            type="range"
            min="0.02"
            max="0.45"
            step="0.02"
            value={params.outerHaloSoftness}
            onChange={(e) => updateParam('outerHaloSoftness', parseFloat(e.target.value))}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-sky-500"
          />
        </div>
      </div>
    </div>
  );
};
