import React, { useState } from 'react';
import { 
  Briefcase, 
  Terminal, 
  Smartphone, 
  Sparkles, 
  Volume2, 
  VolumeX, 
  Play, 
  Square, 
  Send,
  MessageSquare,
  ArrowRight,
  TrendingUp,
  Cpu,
  BarChart3,
  FileText,
  Clock,
  Mic
} from 'lucide-react';
import { VoiceOrbCanvas } from './VoiceOrbCanvas';
import { OrbMode, OrbParameters, CoFounderTopic } from '../types/orb';
import { COFOUNDER_TOPICS, VoiceAudioService } from '../lib/audioService';

interface ProductivityMockupsProps {
  params: OrbParameters;
  mode: OrbMode;
  audioLevel: number;
  onTriggerTopic?: (topic: CoFounderTopic) => void;
  onToggleMic?: () => void;
  isMicActive?: boolean;
}

export const ProductivityMockups: React.FC<ProductivityMockupsProps> = ({
  params,
  mode,
  audioLevel,
  onTriggerTopic,
  onToggleMic,
  isMicActive,
}) => {
  const [activeTab, setActiveTab] = useState<'desktop-boardroom' | 'ide-sidebar' | 'mobile-sheet'>('desktop-boardroom');
  const [activeTopic, setActiveTopic] = useState<CoFounderTopic>(COFOUNDER_TOPICS[0]);
  const [localTheme, setLocalTheme] = useState<'dark' | 'light'>('dark');

  return (
    <div id="productivity-mockups-container" className="space-y-6">
      {/* Header & View Switcher */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-200 dark:border-slate-800 pb-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-sky-500" />
            High-End Productivity Software Contexts
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Preview Alex’s voice-only living field across executive suites, technical IDE sidebars, and mobile sheets.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Theme switcher */}
          <div className="flex items-center bg-slate-100 dark:bg-slate-800/80 p-1 rounded-lg border border-slate-200 dark:border-slate-700 text-xs">
            <button
              id="mockup-theme-dark"
              onClick={() => setLocalTheme('dark')}
              className={`px-2.5 py-1 rounded transition-colors ${
                localTheme === 'dark'
                  ? 'bg-slate-900 text-white shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              Dark Canvas
            </button>
            <button
              id="mockup-theme-light"
              onClick={() => setLocalTheme('light')}
              className={`px-2.5 py-1 rounded transition-colors ${
                localTheme === 'light'
                  ? 'bg-white text-slate-900 shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              Light Canvas
            </button>
          </div>

          {/* Context Tab Selector */}
          <div className="flex items-center bg-slate-100 dark:bg-slate-800/80 p-1 rounded-lg border border-slate-200 dark:border-slate-700 text-xs">
            <button
              id="tab-boardroom"
              onClick={() => setActiveTab('desktop-boardroom')}
              className={`px-3 py-1 rounded transition-colors flex items-center gap-1.5 ${
                activeTab === 'desktop-boardroom'
                  ? 'bg-sky-500 text-white shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              <Briefcase className="w-3.5 h-3.5" />
              Executive Room
            </button>
            <button
              id="tab-ide"
              onClick={() => setActiveTab('ide-sidebar')}
              className={`px-3 py-1 rounded transition-colors flex items-center gap-1.5 ${
                activeTab === 'ide-sidebar'
                  ? 'bg-sky-500 text-white shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              <Terminal className="w-3.5 h-3.5" />
              IDE Workspace
            </button>
            <button
              id="tab-mobile"
              onClick={() => setActiveTab('mobile-sheet')}
              className={`px-3 py-1 rounded transition-colors flex items-center gap-1.5 ${
                activeTab === 'mobile-sheet'
                  ? 'bg-sky-500 text-white shadow-xs font-medium'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
              }`}
            >
              <Smartphone className="w-3.5 h-3.5" />
              Mobile Consult
            </button>
          </div>
        </div>
      </div>

      {/* VIEW 1: Executive Boardroom & Strategy Session */}
      {activeTab === 'desktop-boardroom' && (
        <div
          id="mockup-boardroom-container"
          className={`rounded-2xl border transition-colors overflow-hidden ${
            localTheme === 'dark'
              ? 'bg-[#0B0F19] text-slate-100 border-slate-800'
              : 'bg-[#FAFAFA] text-slate-900 border-slate-200'
          }`}
        >
          {/* Top Bar Navigation */}
          <div
            className={`px-6 py-3 border-b flex items-center justify-between text-xs ${
              localTheme === 'dark'
                ? 'bg-slate-950/60 border-slate-800/80 text-slate-400'
                : 'bg-white border-slate-200 text-slate-600'
            }`}
          >
            <div className="flex items-center gap-3">
              <span className="font-semibold text-sky-500 tracking-tight text-sm">VORTEX LABS</span>
              <span className="text-slate-400">/</span>
              <span className="font-medium text-slate-800 dark:text-slate-200">Executive Co-Founder Suite</span>
              <span className="bg-sky-500/10 text-sky-600 dark:text-sky-400 px-2 py-0.5 rounded text-[10px] font-mono border border-sky-500/20">
                VOICE SESSION ACTIVE
              </span>
            </div>

            <div className="flex items-center gap-4">
              <span className="text-[11px] flex items-center gap-1 text-slate-400 font-mono">
                <Clock className="w-3 h-3" />
                14:32 PST
              </span>
              <div className="flex items-center gap-2 pl-3 border-l border-slate-200 dark:border-slate-800">
                {/* Micro Orb in Top Navigation (24px) */}
                <div className="w-6 h-6 rounded-full flex items-center justify-center bg-slate-900 border border-slate-700">
                  <VoiceOrbCanvas size={22} mode={mode} params={params} audioLevel={audioLevel} />
                </div>
                <span className="font-medium text-slate-700 dark:text-slate-300 text-xs">Alex (AI Co-Founder)</span>
              </div>
            </div>
          </div>

          {/* Main Stage */}
          <div className="p-8 grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
            {/* Center Left: The Ambient Living Field Orb */}
            <div className="lg:col-span-5 flex flex-col items-center justify-center p-6 rounded-2xl relative">
              <div className="relative">
                <VoiceOrbCanvas
                  id="mockup-boardroom-orb"
                  size={260}
                  mode={mode}
                  params={params}
                  audioLevel={audioLevel}
                  className="transition-transform duration-300"
                />
              </div>

              {/* Status pill under Orb */}
              <div className="mt-4 flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-800">
                <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse"></span>
                <span className="text-slate-700 dark:text-slate-300 capitalize font-mono text-[11px]">
                  {mode === 'speaking' ? 'Alex is speaking...' : mode === 'listening' ? 'Alex is listening...' : mode === 'thinking' ? 'Alex is synthesizing...' : 'Alex is present (ambient voice standby)'}
                </span>
              </div>
            </div>

            {/* Right: Live Dialogue & Founder Queries */}
            <div className="lg:col-span-7 space-y-4">
              <div
                className={`p-5 rounded-xl border ${
                  localTheme === 'dark'
                    ? 'bg-slate-900/60 border-slate-800'
                    : 'bg-white border-slate-200 shadow-2xs'
                }`}
              >
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-sky-500">
                    Live Co-Founder Briefing
                  </span>
                  <span className="text-[11px] text-slate-400 font-mono">Real-time Audio Stream</span>
                </div>

                <h3 className="text-base font-medium text-slate-900 dark:text-slate-100 mb-2">
                  "{activeTopic.question}"
                </h3>

                <p className="text-xs text-slate-600 dark:text-slate-300 leading-relaxed bg-slate-50 dark:bg-slate-950/40 p-3.5 rounded-lg border border-slate-200 dark:border-slate-850">
                  {activeTopic.response}
                </p>
              </div>

              {/* Sample Founder Questions to Trigger Alex Speaking */}
              <div className="space-y-2">
                <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">
                  Simulate Founder Voice Inquiries
                </span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {COFOUNDER_TOPICS.map((topic) => (
                    <button
                      key={topic.id}
                      id={`btn-topic-${topic.id}`}
                      onClick={() => {
                        setActiveTopic(topic);
                        if (onTriggerTopic) onTriggerTopic(topic);
                      }}
                      className={`p-2.5 text-left rounded-lg text-xs border transition-all flex items-center justify-between ${
                        activeTopic.id === topic.id
                          ? 'bg-sky-500/10 border-sky-500/40 text-sky-600 dark:text-sky-300 font-medium'
                          : 'bg-slate-50 dark:bg-slate-900/40 border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-400 hover:border-slate-300 dark:hover:border-slate-700'
                      }`}
                    >
                      <span className="truncate">{topic.title}</span>
                      <Play className="w-3 h-3 shrink-0 ml-1 opacity-70" />
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* VIEW 2: IDE / Technical Workspace Sidebar */}
      {activeTab === 'ide-sidebar' && (
        <div
          id="mockup-ide-container"
          className="rounded-2xl border border-slate-800 bg-[#090D16] text-slate-200 overflow-hidden font-mono text-xs"
        >
          <div className="flex border-b border-slate-800 bg-slate-950 px-4 py-2 items-center justify-between text-slate-400">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-rose-500/80"></span>
              <span className="w-2.5 h-2.5 rounded-full bg-amber-500/80"></span>
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/80"></span>
              <span className="ml-2 text-slate-300 text-[11px]">kernel_gateway.rs — Alex Co-Founder Voice Monitor</span>
            </div>
            <span className="text-[10px] text-sky-400">LATENCY: 42ms • VOICE ACTIVE</span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 divide-y lg:divide-y-0 lg:divide-x divide-slate-800 min-h-[320px]">
            {/* Editor Code Area */}
            <div className="lg:col-span-8 p-4 text-[11px] text-slate-300 leading-relaxed space-y-1">
              <div className="text-slate-500">// Distributed voice frame routing pipeline</div>
              <div><span className="text-purple-400">pub async fn</span> <span className="text-sky-400">stream_audio_payload</span>(ctx: &amp;<span className="text-amber-300">VoiceContext</span>) -&gt; <span className="text-emerald-400">Result</span>&lt;()&gt; &#123;</div>
              <div className="pl-4 text-slate-400">let mut stream = ctx.acquire_raw_channel().await?;</div>
              <div className="pl-4 text-slate-400">let energy_tensor = <span className="text-sky-400">compute_lobe_harmonics</span>(&amp;stream);</div>
              <div className="pl-4 text-slate-500">// Alex is listening for voice interruptions in real-time...</div>
              <div className="pl-4 text-sky-300">alex::voice_orb::render_frame(energy_tensor).await?;</div>
              <div className="pl-4 text-slate-400">Ok(())</div>
              <div>&#125;</div>
            </div>

            {/* Sidebar Assistant with Alex Orb */}
            <div className="lg:col-span-4 p-5 bg-slate-950/70 flex flex-col items-center justify-between text-center space-y-4">
              <div className="w-full text-left">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider font-sans font-semibold">
                  Voice Co-Founder
                </span>
              </div>

              <div className="flex flex-col items-center">
                <VoiceOrbCanvas size={130} mode={mode} params={params} audioLevel={audioLevel} />
                <span className="mt-2 text-xs font-sans font-medium text-sky-400">Alex</span>
                <span className="text-[10px] text-slate-500 font-sans">Zero-noise voice pairing</span>
              </div>

              <div className="text-left w-full text-[11px] font-sans text-slate-400 bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                <span className="text-slate-200 block font-medium mb-1">Architecture Note:</span>
                "I monitored the buffer latency. Shifting the FFT chunking from 128ms to 32ms will shave 40ms of round-trip audio jitter."
              </div>
            </div>
          </div>
        </div>
      )}

      {/* VIEW 3: Mobile Quick Consult Sheet */}
      {activeTab === 'mobile-sheet' && (
        <div id="mockup-mobile-container" className="flex justify-center p-4">
          <div className="w-full max-w-sm rounded-[32px] border-4 border-slate-800 bg-[#0B0F19] text-white p-6 shadow-2xl space-y-6">
            {/* Status bar */}
            <div className="flex justify-between items-center text-[10px] text-slate-400 font-mono">
              <span>9:41</span>
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                <span>5G</span>
              </div>
            </div>

            {/* Top Sheet Header */}
            <div className="text-center space-y-1">
              <h3 className="text-sm font-semibold text-slate-100">Alex Co-Founder</h3>
              <p className="text-xs text-sky-400">Voice-First Quick Briefing</p>
            </div>

            {/* Centered Mobile Orb */}
            <div className="flex flex-col items-center justify-center py-4">
              <VoiceOrbCanvas size={180} mode={mode} params={params} audioLevel={audioLevel} />
              <span className="mt-3 text-xs text-slate-400 font-mono text-[11px]">
                {mode === 'speaking' ? 'Speaking...' : 'Listening...'}
              </span>
            </div>

            {/* Quick action card */}
            <div className="bg-slate-900/80 border border-slate-800 p-3.5 rounded-2xl text-xs space-y-2">
              <span className="text-[10px] uppercase font-semibold text-slate-400">Quick Prompt</span>
              <p className="text-slate-200 font-medium text-xs">
                "What's our burn multiple based on last month's AWS cloud compute spend?"
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
