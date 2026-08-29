import React, { useEffect, useRef } from 'react';
import { OrbMode, OrbParameters } from '../types/orb';
import { DEFAULT_ORB_PARAMS, renderAlexOrb } from '../lib/orbRenderer';

interface VoiceOrbCanvasProps {
  size?: number;
  mode?: OrbMode;
  params?: OrbParameters;
  audioLevel?: number;
  audioFreqData?: Uint8Array | null;
  isStatic?: boolean;
  staticTime?: number;
  className?: string;
  id?: string;
  onClick?: () => void;
  title?: string;
}

export function VoiceOrbCanvas({
  size = 280,
  mode = 'idle' as OrbMode,
  params = DEFAULT_ORB_PARAMS,
  audioLevel = 0,
  audioFreqData = null,
  isStatic = false,
  staticTime = 0,
  className = '',
  id,
  onClick,
  title,
}: VoiceOrbCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameIdRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(performance.now());
  const effectiveMode: OrbMode = mode;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    // Handle high DPI / Retina displays for ultra-crisp edge blending
    const dpr = window.devicePixelRatio || 2;
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);

    if (isStatic) {
      // Static keyframe rendering
      ctx.save();
      ctx.scale(dpr, dpr);
      renderAlexOrb({
        ctx,
        width: size,
        height: size,
        time: staticTime,
        mode: effectiveMode,
        audioLevel: 0,
        audioFreqData: null,
        params,
        isStaticKeyframe: true,
      });
      ctx.restore();
      return;
    }

    // Dynamic 60fps organic fluid animation loop
    let running = true;

    const loop = (now: number) => {
      if (!running) return;

      const elapsedSec = (now - startTimeRef.current) / 1000;

      ctx.save();
      ctx.scale(dpr, dpr);
      renderAlexOrb({
        ctx,
        width: size,
        height: size,
        time: elapsedSec,
        mode: effectiveMode,
        audioLevel,
        audioFreqData,
        params,
        isStaticKeyframe: false,
      });
      ctx.restore();

      animFrameIdRef.current = requestAnimationFrame(loop);
    };

    animFrameIdRef.current = requestAnimationFrame(loop);

    return () => {
      running = false;
      if (animFrameIdRef.current !== null) {
        cancelAnimationFrame(animFrameIdRef.current);
      }
    };
  }, [size, mode, params, audioLevel, audioFreqData, isStatic, staticTime]);

  return (
    <canvas
      ref={canvasRef}
      id={id}
      title={title}
      onClick={onClick}
      style={{
        width: `${size}px`,
        height: `${size}px`,
        maxWidth: '100%',
        display: 'block',
      }}
      className={`touch-none select-none ${onClick ? 'cursor-pointer' : ''} ${className}`}
    />
  );
};
