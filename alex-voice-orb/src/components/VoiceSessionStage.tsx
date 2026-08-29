import React, { useState, useEffect, useRef } from 'react';
import { Mic, MicOff, Volume2, Sparkles, Play, Square, Pause, Shield, Check, Info } from 'lucide-react';
import { VoiceOrbCanvas } from './VoiceOrbCanvas';
import { OrbMode, OrbParameters, CoFounderTopic } from '../types/orb';
import { COFOUNDER_TOPICS, VoiceAudioService } from '../lib/audioService';

interface VoiceSessionStageProps {
  params: OrbParameters;
  mode: OrbMode;
  onChangeMode: (mode: OrbMode) => void;
  audioLevel: number;
  audioFreqData: Uint8Array | null;
  audioService: VoiceAudioService;
  onSetAudioLevel: (lvl: number) => void;
}

export const VoiceSessionStage: React.FC<VoiceSessionStageProps> = ({
  params,
  mode,
  onChangeMode,
  audioLevel,
  audioFreqData,
  audioService,
  onSetAudioLevel,
}) => {
  const [isMicOn, setIsMicOn] = useState<boolean>(false);
  const [isSpeaking, setIsSpeaking] = useState<boolean>(false);
  const [currentTopic, setCurrentTopic] = useState<CoFounderTopic>(COFOUNDER_TOPICS[0]);
  const [speechProgressText, setSpeechProgressText] = useState<string>('');
  const [transcriptHistory, setTranscriptHistory] = useState<{ sender: 'user' | 'alex'; text: string; time: string }[]>([
    {
      sender: 'alex',
      text: "I'm standing by, ready to brainstorm growth mechanics, audit architecture latency, or review your pitch narrative.",
      time: 'Just now',
    },
  ]);

  const pollIntervalRef = useRef<number | null>(null);

  // Poll mic audio metrics if mic is active
  useEffect(() => {
    if (isMicOn) {
      pollIntervalRef.current = window.setInterval(() => {
        const metrics = audioService.getAudioMetrics();
        onSetAudioLevel(metrics.level);
        if (metrics.level > 0.08 && mode !== 'speaking') {
          onChangeMode('listening');
        } else if (metrics.level <= 0.08 && mode === 'listening') {
          onChangeMode('idle');
        }
      }, 30);
    } else {
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    }

    return () => {
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [isMicOn, mode, audioService, onChangeMode, onSetAudioLevel]);

  const handleToggleMic = async () => {
    if (isMicOn) {
      audioService.stopMicrophone();
      setIsMicOn(false);
      onSetAudioLevel(0);
      onChangeMode('idle');
    } else {
      const ok = await audioService.startMicrophone();
      if (ok) {
        setIsMicOn(true);
        onChangeMode('listening');
      }
    }
  };

  const handlePlayCoFounderSpeech = (topic: CoFounderTopic) => {
    setCurrentTopic(topic);
    if (isSpeaking) {
      audioService.stopSpeaking();
      setIsSpeaking(false);
      onChangeMode('idle');
      onSetAudioLevel(0);
      return;
    }

    // Add user question to transcript
    setTranscriptHistory((prev) => [
      ...prev,
      {
        sender: 'user',
        text: topic.question,
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      },
    ]);

    onChangeMode('thinking');
    setTimeout(() => {
      onChangeMode('speaking');
      setIsSpeaking(true);
      setSpeechProgressText(topic.response);

      audioService.speakText(
        topic.response,
        () => {
          onChangeMode('speaking');
          setIsSpeaking(true);
        },
        (lvl) => {
          onSetAudioLevel(lvl);
        },
        () => {
          setIsSpeaking(false);
          onChangeMode('idle');
          onSetAudioLevel(0);
          setTranscriptHistory((prev) => [
            ...prev,
            {
              sender: 'alex',
              text: topic.response,
              time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            },
          ]);
        }
      );
    }, 600);
  };

  return (
    <div id="voice-session-stage-container" className="space-y-6">
      {/* Top Banner / Concept Card */}
      <div className="bg-slate-900 text-white rounded-2xl p-6 sm:p-8 border border-slate-800 relative overflow-hidden shadow-md">
        {/* Subtle background ambient blur */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-blue-600/10 rounded-full blur-3xl pointer-events-none"></div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center relative z-10">
          {/* Main Orb Centerpiece */}
          <div className="lg:col-span-6 flex flex-col items-center justify-center text-center">
            <div className="relative group cursor-pointer" onClick={() => handlePlayCoFounderSpeech(currentTopic)}>
              <VoiceOrbCanvas
                id="main-stage-alex-orb"
                size={310}
                mode={mode}
                params={params}
                audioLevel={audioLevel}
                audioFreqData={audioFreqData}
                className="transition-transform duration-300 group-hover:scale-102"
              />
            </div>

            {/* Living Field Mode Indicator */}
            <div className="mt-4 flex items-center gap-3">
              <div className="flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono bg-slate-800/90 border border-slate-700">
                <span className={`w-2 h-2 rounded-full ${
                  mode === 'speaking' ? 'bg-sky-400 animate-pulse' :
                  mode === 'listening' ? 'bg-emerald-400 animate-pulse' :
                  mode === 'thinking' ? 'bg-indigo-400 animate-spin' :
                  'bg-blue-400'
                }`}></span>
                <span className="text-slate-200 uppercase tracking-wide text-[11px]">
                  {mode === 'idle' ? 'Ambient Living Presence' :
                   mode === 'speaking' ? 'Harmonic Voice Resonance' :
                   mode === 'listening' ? 'Receptive Audio Ingestion' :
                   mode === 'thinking' ? 'Cognitive Azure Synthesis' : 'Subdued Standby'}
                </span>
              </div>

              {audioLevel > 0 && (
                <span className="text-[10px] font-mono text-sky-400 bg-sky-950/60 px-2 py-1 rounded border border-sky-800">
                  {(audioLevel * 100).toFixed(0)}% Audio FFT
                </span>
              )}
            </div>

            {/* Quick Action Controls */}
            <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
              <button
                id="btn-toggle-mic-session"
                onClick={handleToggleMic}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-medium transition-all ${
                  isMicOn
                    ? 'bg-rose-500 hover:bg-rose-600 text-white shadow-xs'
                    : 'bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700'
                }`}
              >
                {isMicOn ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4 text-sky-400" />}
                {isMicOn ? 'Mute Microphone' : 'Enable Real Microphone Input'}
              </button>

              <button
                id="btn-simulate-speech"
                onClick={() => handlePlayCoFounderSpeech(currentTopic)}
                className="flex items-center gap-2 px-4 py-2 bg-sky-500 hover:bg-sky-400 text-white text-xs font-medium rounded-xl transition-all shadow-xs"
              >
                {isSpeaking ? <Square className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
                {isSpeaking ? 'Halt Speech' : 'Simulate Co-Founder Response'}
              </button>
            </div>
          </div>

          {/* Right Column: Co-Founder Voice Inquiries & Context */}
          <div className="lg:col-span-6 space-y-4">
            <div className="space-y-1">
              <span className="text-xs font-mono uppercase tracking-wider text-sky-400 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5" />
                AI Co-Founder Voice Intelligence
              </span>
              <h1 className="text-2xl font-semibold text-white tracking-tight">
                Alex — Living Translucent Energy Orb
              </h1>
              <p className="text-xs text-slate-300 leading-relaxed">
                Engineered as a soft organic cloud-like blue energy globe for an AI co-founder. Formed by harmonic cobalt depth, radiant azure body, and delicate ice-blue conscious center without artificial rings or microphone cliches.
              </p>
            </div>

            {/* Speech Topic Selector */}
            <div className="space-y-2 pt-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 block">
                Select Strategic Inquiry to Audition Vocal Harmonics
              </span>
              <div className="space-y-2">
                {COFOUNDER_TOPICS.map((topic) => {
                  const isCur = currentTopic.id === topic.id;
                  return (
                    <div
                      key={topic.id}
                      id={`topic-card-${topic.id}`}
                      onClick={() => {
                        setCurrentTopic(topic);
                        handlePlayCoFounderSpeech(topic);
                      }}
                      className={`p-3 rounded-xl border text-left cursor-pointer transition-all ${
                        isCur
                          ? 'bg-sky-950/40 border-sky-600/80 shadow-xs'
                          : 'bg-slate-800/50 border-slate-700/60 hover:border-slate-600'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-mono text-sky-300 uppercase tracking-wide">
                          {topic.category}
                        </span>
                        <span className="text-[10px] text-slate-400">~{topic.durationSec}s voice</span>
                      </div>
                      <p className="text-xs font-medium text-slate-200 mt-1">"{topic.question}"</p>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Transcript Log & Voice Modulation Readout */}
      <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 p-5 space-y-4 shadow-xs">
        <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-700 dark:text-slate-300 flex items-center gap-2">
            <Info className="w-4 h-4 text-sky-500" />
            Live Voice Session Stream
          </h3>
          <span className="text-[11px] font-mono text-slate-400">
            {transcriptHistory.length} Exchanges Logged
          </span>
        </div>

        <div className="space-y-3 max-h-48 overflow-y-auto pr-2">
          {transcriptHistory.map((item, idx) => (
            <div
              key={idx}
              className={`p-3 rounded-xl text-xs ${
                item.sender === 'alex'
                  ? 'bg-sky-50 dark:bg-sky-950/30 border border-sky-200/80 dark:border-sky-800/50 text-slate-800 dark:text-slate-200'
                  : 'bg-slate-100 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 text-slate-900 dark:text-slate-100'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-[11px] text-sky-600 dark:text-sky-400">
                  {item.sender === 'alex' ? 'Alex (AI Co-Founder)' : 'Founder (You)'}
                </span>
                <span className="text-[10px] text-slate-400 font-mono">{item.time}</span>
              </div>
              <p className="leading-relaxed">{item.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
