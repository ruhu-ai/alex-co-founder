import { CoFounderTopic } from '../types/orb';

export class VoiceAudioService {
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private micStream: MediaStream | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private freqData: Uint8Array | null = null;
  private isListeningMic = false;
  private synthUtterance: SpeechSynthesisUtterance | null = null;
  private simulatedOscillator: OscillatorNode | null = null;
  private simGain: GainNode | null = null;
  private simulatedInterval: number | null = null;
  private simulatedLevel = 0;

  public async startMicrophone(onLevelChange?: (level: number, freq: Uint8Array) => void): Promise<boolean> {
    try {
      if (this.isListeningMic) return true;

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      this.micStream = stream;

      const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioCtx = new AudioContextClass();
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 64;
      this.analyser.smoothingTimeConstant = 0.8;

      this.sourceNode = this.audioCtx.createMediaStreamSource(stream);
      this.sourceNode.connect(this.analyser);

      this.freqData = new Uint8Array(this.analyser.frequencyBinCount);
      this.isListeningMic = true;

      return true;
    } catch (err) {
      console.warn('Microphone access unavailable or denied:', err);
      this.isListeningMic = false;
      return false;
    }
  }

  public stopMicrophone() {
    if (this.micStream) {
      this.micStream.getTracks().forEach((track) => track.stop());
      this.micStream = null;
    }
    if (this.sourceNode) {
      this.sourceNode.disconnect();
      this.sourceNode = null;
    }
    if (this.audioCtx && this.audioCtx.state !== 'closed') {
      this.audioCtx.close();
      this.audioCtx = null;
    }
    this.isListeningMic = false;
    this.analyser = null;
    this.freqData = null;
  }

  public getAudioMetrics(): { level: number; freqData: Uint8Array | null } {
    if (this.isListeningMic && this.analyser && this.freqData) {
      this.analyser.getByteFrequencyData(this.freqData);
      let sum = 0;
      for (let i = 0; i < this.freqData.length; i++) {
        sum += this.freqData[i];
      }
      const avg = sum / this.freqData.length;
      const normalizedLevel = Math.min(1, avg / 128);
      return { level: normalizedLevel, freqData: this.freqData };
    }

    if (this.simulatedLevel > 0) {
      return { level: this.simulatedLevel, freqData: null };
    }

    return { level: 0, freqData: null };
  }

  public isMicActive(): boolean {
    return this.isListeningMic;
  }

  /**
   * Play speech using Web Speech API or organic simulated vocal modulation
   */
  public speakText(
    text: string,
    onStart: () => void,
    onProgress: (level: number) => void,
    onEnd: () => void
  ) {
    this.stopSpeaking();

    // Harmonic simulation ticker for natural syllable rhythm
    let t = 0;
    this.simulatedInterval = window.setInterval(() => {
      t += 0.15;
      // Multi-harmonic modulation mimicking natural speech prosody & pauses
      const syllable = Math.sin(t * 7.5) * 0.4 + Math.sin(t * 3.2) * 0.35 + Math.sin(t * 1.1) * 0.25;
      const pauseGate = Math.sin(t * 0.7) > -0.35 ? 1 : 0.08;
      this.simulatedLevel = Math.max(0.05, Math.min(0.95, (syllable * 0.5 + 0.45) * pauseGate));
      onProgress(this.simulatedLevel);
    }, 40);

    if ('speechSynthesis' in window) {
      const utter = new SpeechSynthesisUtterance(text);
      utter.rate = 1.02;
      utter.pitch = 0.98; // Calm, trustworthy executive pitch

      // Pick high-quality English voice if available
      const voices = window.speechSynthesis.getVoices();
      const preferred = voices.find(
        (v) => (v.name.includes('Natural') || v.name.includes('Neural') || v.name.includes('Premium') || v.name.includes('Google') || v.name.includes('Daniel') || v.name.includes('Alex')) && v.lang.startsWith('en')
      );
      if (preferred) utter.voice = preferred;

      utter.onstart = () => {
        onStart();
      };
      utter.onend = () => {
        this.stopSpeaking();
        onEnd();
      };
      utter.onerror = () => {
        this.stopSpeaking();
        onEnd();
      };

      this.synthUtterance = utter;
      window.speechSynthesis.speak(utter);
    } else {
      onStart();
      // Fallback timer based on word count
      const words = text.split(' ').length;
      const durationMs = Math.max(3000, (words / 2.8) * 1000);
      setTimeout(() => {
        this.stopSpeaking();
        onEnd();
      }, durationMs);
    }
  }

  public stopSpeaking() {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    if (this.simulatedInterval !== null) {
      clearInterval(this.simulatedInterval);
      this.simulatedInterval = null;
    }
    this.simulatedLevel = 0;
  }
}

export const COFOUNDER_TOPICS: CoFounderTopic[] = [
  {
    id: 'strategy-pmf',
    title: 'Enterprise GTM & Land-and-Expand',
    category: 'Strategy',
    question: 'Alex, how should we structure our enterprise pilot agreements for the Q4 pipeline?',
    response: 'I recommend structuring 60-day paid pilots with three crisp success criteria tied directly to workflow velocity. Anchor the pricing at twenty-five thousand dollars credited against their annual contract upon conversion.',
    durationSec: 10,
  },
  {
    id: 'fundraising-series-a',
    title: 'Series A Narrative & Unit Economics',
    category: 'Fundraising',
    question: 'How do we present our net revenue retention to Tier-1 investors next Tuesday?',
    response: 'Lead with our cohort expansion curve. Show that net revenue retention hit 138% while CAC payback shortened to five months. Position this not just as product stickiness, but as an indispensable operating layer.',
    durationSec: 11,
  },
  {
    id: 'product-roadmap',
    title: 'Zero-Latency Voice Protocol Architecture',
    category: 'Architecture',
    question: 'Should we prioritize the sub-200ms streaming audio pipeline over multi-tenant workspaces?',
    response: 'Latency is our primary defensible moat. If perceived response delay drops below two hundred milliseconds, conversational fidelity feels supernatural. Let’s lock the streaming pipeline first, then ship workspace tenancy in the next sprint.',
    durationSec: 12,
  },
  {
    id: 'talent-culture',
    title: 'Founding Engineer Hiring Strategy',
    category: 'Product',
    question: 'What traits should we test for in the Principal Systems Engineer interview?',
    response: 'Look for deep intuition around state synchronization and asynchronous streaming. Have them live-debug a distributed backpressure bottleneck rather than solving generic algorithm puzzles.',
    durationSec: 10,
  },
];
