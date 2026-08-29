import React, { useState } from 'react';
import { Copy, Check, Palette, FileCode, Layers, Cloud } from 'lucide-react';
import { OrbParameters } from '../types/orb';

interface DesignTokensExportProps {
  params: OrbParameters;
}

export const DesignTokensExport: React.FC<DesignTokensExportProps> = ({ params }) => {
  const [copiedToken, setCopiedToken] = useState<string | null>(null);

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedToken(id);
    setTimeout(() => setCopiedToken(null), 2000);
  };

  const COLOR_TOKENS = [
    {
      name: 'Electric Periwinkle Sky Light',
      hex: '#6382FF',
      rgba: 'rgba(99, 130, 255, 0.98)',
      role: 'Top atmospheric zenith and ambient light source',
      swatch: 'bg-[#6382FF]',
    },
    {
      name: 'Cornflower Blue Body',
      hex: '#4361EE',
      rgba: 'rgba(67, 97, 238, 0.96)',
      role: 'Signature ChatGPT voice orb body and luminous backdrop',
      swatch: 'bg-[#4361EE]',
    },
    {
      name: 'Deep Cobalt Anchor',
      hex: '#182887',
      rgba: 'rgba(24, 40, 135, 0.98)',
      role: 'Bottom spherical grounding & high contrast on white backgrounds',
      swatch: 'bg-[#182887]',
    },
    {
      name: 'Cumulus Mist Pure White',
      hex: '#FFFFFF',
      rgba: 'rgba(255, 255, 255, 0.95)',
      role: 'Billowing internal cloud core & dense cumulus crests',
      swatch: 'bg-[#FFFFFF] border border-slate-300 dark:border-slate-600',
    },
    {
      name: 'Pale Ice-Sky Diffusion',
      hex: '#BAE6FD',
      rgba: 'rgba(186, 230, 253, 0.85)',
      role: 'Cloud edge feathering and translucent mist diffusion',
      swatch: 'bg-[#BAE6FD]',
    },
  ];

  const jsonConfig = JSON.stringify(
    {
      orbName: 'Alex Voice Cloud Orb',
      designStyle: 'ChatGPT-Style Procedural Blue Cloud Sphere',
      palette: {
        periwinkleSky: '#6382FF',
        cornflowerBody: '#4361EE',
        deepCobaltAnchor: '#182887',
        cumulusMist: '#FFFFFF',
        paleIceSky: '#BAE6FD',
      },
      atmosphere: {
        cloudDensity: params.cloudDensity,
        cloudElevation: params.cloudElevation,
        iceBlueCore: params.iceBlueCore,
        azureVibrancy: params.azureVibrancy,
        cobaltWeight: params.cobaltWeight,
      },
      dynamics: {
        fluidity: params.fluidity,
        organicWarp: params.organicWarp,
        innerRadiance: params.innerRadiance,
        outerHaloSoftness: params.outerHaloSoftness,
        breathingSpeed: params.breathingSpeed,
        voiceReactivity: params.voiceReactivity,
      },
    },
    null,
    2
  );

  return (
    <div id="design-tokens-container" className="space-y-6">
      <div className="border-b border-slate-200 dark:border-slate-800 pb-4">
        <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
          <Palette className="w-5 h-5 text-sky-500" />
          Color Tokens & Atmospheric Specifications
        </h2>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Color tokens, cloud mist density values, and JSON design tokens for ChatGPT-style voice orb integration.
        </p>
      </div>

      {/* Swatches Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {COLOR_TOKENS.map((token, i) => (
          <div
            key={i}
            id={`token-card-${i}`}
            className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-4 space-y-3 shadow-xs"
          >
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-lg shrink-0 ${token.swatch} shadow-inner`}></div>
              <div>
                <h4 className="text-xs font-semibold text-slate-900 dark:text-slate-100">
                  {token.name}
                </h4>
                <div className="flex items-center gap-2 text-[11px] font-mono text-slate-500">
                  <span>{token.hex}</span>
                </div>
              </div>
            </div>
            <p className="text-xs text-slate-600 dark:text-slate-400">
              {token.role}
            </p>
            <button
              id={`btn-copy-token-${i}`}
              onClick={() => copyToClipboard(token.hex, `hex-${i}`)}
              className="w-full py-1 px-2 text-[11px] font-mono bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 rounded transition-colors flex items-center justify-center gap-1"
            >
              {copiedToken === `hex-${i}` ? (
                <>
                  <Check className="w-3 h-3 text-emerald-500" /> Copied Hex
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" /> Copy {token.hex}
                </>
              )}
            </button>
          </div>
        ))}
      </div>

      {/* JSON Specification */}
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-5 space-y-3 shadow-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileCode className="w-4 h-4 text-sky-500" />
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Exportable Token Configuration (JSON)
            </h3>
          </div>
          <button
            id="btn-copy-json-config"
            onClick={() => copyToClipboard(jsonConfig, 'json-cfg')}
            className="flex items-center gap-1 px-3 py-1 text-xs font-medium rounded-lg bg-sky-500 hover:bg-sky-400 text-white transition-colors"
          >
            {copiedToken === 'json-cfg' ? (
              <>
                <Check className="w-3.5 h-3.5" /> Copied JSON
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" /> Copy JSON Token Spec
              </>
            )}
          </button>
        </div>
        <pre className="p-4 bg-slate-950 text-sky-300 text-xs font-mono rounded-xl overflow-x-auto border border-slate-800 leading-relaxed">
          {jsonConfig}
        </pre>
      </div>
    </div>
  );
};
