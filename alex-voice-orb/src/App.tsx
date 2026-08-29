import React, { useState, useMemo, useEffect } from 'react';
import { 
  Sparkles, 
  Layers, 
  Briefcase, 
  Sliders, 
  Palette, 
  Download, 
  Mic, 
  MicOff, 
  Moon, 
  Sun,
  Activity,
  Maximize2
} from 'lucide-react';
import { OrbMode, OrbParameters, CoFounderTopic } from './types/orb';
import { DEFAULT_ORB_PARAMS } from './lib/orbRenderer';
import { VoiceAudioService } from './lib/audioService';
import { VoiceOrbCanvas } from './components/VoiceOrbCanvas';
import { VoiceSessionStage } from './components/VoiceSessionStage';
import { KeyframeStudio } from './components/KeyframeStudio';
import { ScaleMatrix } from './components/ScaleMatrix';
import { ProductivityMockups } from './components/ProductivityMockups';
import { ControlsPanel } from './components/ControlsPanel';
import { DesignTokensExport } from './components/DesignTokensExport';

type ActiveViewTab = 'stage' | 'keyframes' | 'scales' | 'mockups' | 'tokens';

export default function App() {
  const [activeTab, setActiveTab] = useState<ActiveViewTab>('stage');
  const [orbMode, setOrbMode] = useState<OrbMode>('idle');
  const [orbParams, setOrbParams] = useState<OrbParameters>(DEFAULT_ORB_PARAMS);
  const [audioLevel, setAudioLevel] = useState<number>(0);
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');

  // Single audio service instance
  const audioService = useMemo(() => new VoiceAudioService(), []);

  // Update theme class on HTML element
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  };

  return (
    <div id="alex-orb-app-root" className="min-h-screen bg-slate-100 dark:bg-[#070A12] text-slate-900 dark:text-slate-100 transition-colors duration-200 flex flex-col font-sans selection:bg-sky-500 selection:text-white">
      {/* Top Navigation Bar */}
      <header id="main-app-header" className="sticky top-0 z-40 bg-white/90 dark:bg-[#0B0F19]/90 backdrop-blur-md border-b border-slate-200 dark:border-slate-850 px-4 sm:px-6 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
          {/* Brand & Mini Orb Live Status */}
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-slate-900 border border-slate-700 flex items-center justify-center overflow-hidden shrink-0 shadow-xs">
              <VoiceOrbCanvas
                id="header-mini-orb"
                size={30}
                mode={orbMode}
                params={orbParams}
                audioLevel={audioLevel}
              />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-900 dark:text-white text-sm tracking-tight">
                  Alex Orb
                </span>
                <span className="text-[10px] font-mono font-medium px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20">
                  AI Co-Founder Living Field
                </span>
              </div>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 hidden sm:block">
                Voice-First Translucent Energy Globe • Canvas Design System
              </p>
            </div>
          </div>

          {/* Center Tabs Navigation */}
          <nav id="nav-tabs-group" className="hidden md:flex items-center bg-slate-100 dark:bg-slate-900/80 p-1 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
            <button
              id="tab-btn-stage"
              onClick={() => setActiveTab('stage')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'stage'
                  ? 'bg-white dark:bg-slate-800 text-sky-600 dark:text-sky-400 shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              Live Stage
            </button>
            <button
              id="tab-btn-keyframes"
              onClick={() => setActiveTab('keyframes')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'keyframes'
                  ? 'bg-white dark:bg-slate-800 text-sky-600 dark:text-sky-400 shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              <Download className="w-3.5 h-3.5" />
              Static Keyframes
            </button>
            <button
              id="tab-btn-scales"
              onClick={() => setActiveTab('scales')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'scales'
                  ? 'bg-white dark:bg-slate-800 text-sky-600 dark:text-sky-400 shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              Scale Matrix
            </button>
            <button
              id="tab-btn-mockups"
              onClick={() => setActiveTab('mockups')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'mockups'
                  ? 'bg-white dark:bg-slate-800 text-sky-600 dark:text-sky-400 shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              <Briefcase className="w-3.5 h-3.5" />
              UI Mockups
            </button>
            <button
              id="tab-btn-tokens"
              onClick={() => setActiveTab('tokens')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'tokens'
                  ? 'bg-white dark:bg-slate-800 text-sky-600 dark:text-sky-400 shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              <Palette className="w-3.5 h-3.5" />
              Color Tokens
            </button>
          </nav>

          {/* Right Header Actions */}
          <div className="flex items-center gap-2">
            <button
              id="btn-global-theme-toggle"
              onClick={toggleTheme}
              className="p-2 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700 transition-colors"
              title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} Mode`}
            >
              {theme === 'dark' ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-slate-700" />}
            </button>
          </div>
        </div>

        {/* Mobile Tab Scroller */}
        <div className="flex md:hidden items-center gap-1 mt-2.5 overflow-x-auto pb-1 text-xs scrollbar-none">
          {(['stage', 'keyframes', 'scales', 'mockups', 'tokens'] as ActiveViewTab[]).map((tab) => (
            <button
              key={tab}
              id={`mobile-tab-${tab}`}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-1 rounded-lg font-medium capitalize shrink-0 ${
                activeTab === tab
                  ? 'bg-sky-500 text-white'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400'
              }`}
            >
              {tab === 'stage' ? 'Live Stage' : tab === 'keyframes' ? 'Keyframes' : tab === 'scales' ? 'Scales' : tab === 'mockups' ? 'Mockups' : 'Tokens'}
            </button>
          ))}
        </div>
      </header>

      {/* Main Workspace Layout */}
      <main id="main-workspace-content" className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 lg:p-8">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* Main Primary View (8 cols or full depending on view) */}
          <div className={activeTab === 'keyframes' || activeTab === 'scales' || activeTab === 'tokens' ? 'lg:col-span-12' : 'lg:col-span-8'}>
            {activeTab === 'stage' && (
              <VoiceSessionStage
                params={orbParams}
                mode={orbMode}
                onChangeMode={setOrbMode}
                audioLevel={audioLevel}
                audioFreqData={null}
                audioService={audioService}
                onSetAudioLevel={setAudioLevel}
              />
            )}

            {activeTab === 'keyframes' && (
              <KeyframeStudio
                currentParams={orbParams}
                currentMode={orbMode}
              />
            )}

            {activeTab === 'scales' && (
              <ScaleMatrix
                params={orbParams}
                mode={orbMode}
                audioLevel={audioLevel}
              />
            )}

            {activeTab === 'mockups' && (
              <ProductivityMockups
                params={orbParams}
                mode={orbMode}
                audioLevel={audioLevel}
                onTriggerTopic={(topic) => {
                  setOrbMode('speaking');
                  audioService.speakText(
                    topic.response,
                    () => setOrbMode('speaking'),
                    (lvl) => setAudioLevel(lvl),
                    () => {
                      setOrbMode('idle');
                      setAudioLevel(0);
                    }
                  );
                }}
              />
            )}

            {activeTab === 'tokens' && (
              <DesignTokensExport params={orbParams} />
            )}
          </div>

          {/* Right Floating Tuning Panel (Visible in stage & mockups view) */}
          {(activeTab === 'stage' || activeTab === 'mockups') && (
            <div className="lg:col-span-4 sticky top-20">
              <ControlsPanel
                params={orbParams}
                onChangeParams={setOrbParams}
                mode={orbMode}
                onChangeMode={setOrbMode}
              />
            </div>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer id="main-app-footer" className="border-t border-slate-200 dark:border-slate-850 py-4 px-6 text-center text-xs text-slate-500 dark:text-slate-400 bg-white/50 dark:bg-slate-950/50">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-2">
          <span>Alex AI Co-Founder Voice Orb Specification • High-End Productivity Software Design System</span>
          <span className="font-mono text-[11px]">Layered Cobalt (#1E3A8A) • Azure (#1D4ED8/#0EA5E9) • Pale Ice-Blue (#BAE6FD/#F0F9FF)</span>
        </div>
      </footer>
    </div>
  );
}
