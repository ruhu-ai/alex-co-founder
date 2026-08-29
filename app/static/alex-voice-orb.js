/* Alex Voice Op — dependency-free Canvas 2D renderer.
   Receives a trusted view model from index.html. It never reads media, starts
   capture, changes live state, persists audio, or emits telemetry. */
(function installAlexVoiceOrb(global) {
  "use strict";

  const STATES = Object.freeze({
    CONNECTING: { speed: 0.52, cloud: 0.64, glow: 0.66 },
    LISTENING: { speed: 0.76, cloud: 0.82, glow: 0.78 },
    THINKING: { speed: 1.18, cloud: 0.76, glow: 0.88 },
    PROCESSING: { speed: 1.02, cloud: 0.72, glow: 0.82 },
    SPEAKING: { speed: 0.92, cloud: 0.94, glow: 0.94 },
    INTERRUPTED: { speed: 0.34, cloud: 0.62, glow: 0.62 },
    AWAITING_APPROVAL: { speed: 0.28, cloud: 0.76, glow: 0.72 },
    RECONNECTING: { speed: 0.64, cloud: 0.58, glow: 0.58 },
    PAUSED: { speed: 0, cloud: 0.48, glow: 0.44 },
    HELD: { speed: 0, cloud: 0.54, glow: 0.46 },
    ERROR: { speed: 0, cloud: 0.42, glow: 0.42 },
    MICROPHONE_OFF: { speed: 0, cloud: 0.56, glow: 0.50 },
  });

  const PALETTES = Object.freeze({
    normal: [[28, 75, 184], [49, 105, 222], [80, 154, 241]],
    error: [[61, 91, 145], [78, 115, 174], [111, 151, 202]],
  });

  // These profiles reshape only the cloud field inside the invariant circle.
  // Listening opens horizontally, Thinking gathers into a taller centre, and
  // Speaking/Responding lifts and billows with outgoing-audio energy.
  const CLOUD_PROFILES = Object.freeze({
    DEFAULT: { spread: 1, lift: 0, puffX: 1, puffY: 1, wave: 1, drift: 0.65,
      coreX: 1, coreY: 1 },
    LISTENING: { spread: 1.12, lift: 0.02, puffX: 1.20, puffY: 0.82,
      wave: 1.12, drift: 0.72, coreX: 1.16, coreY: 0.86 },
    THINKING: { spread: 0.72, lift: -0.08, puffX: 0.78, puffY: 1.24,
      wave: 0.72, drift: 0.34, coreX: 0.80, coreY: 1.18 },
    PROCESSING: { spread: 0.78, lift: -0.06, puffX: 0.84, puffY: 1.18,
      wave: 0.78, drift: 0.42, coreX: 0.86, coreY: 1.12 },
    SPEAKING: { spread: 0.98, lift: -0.10, puffX: 0.94, puffY: 1.22,
      wave: 1.30, drift: 0.96, coreX: 0.94, coreY: 1.16 },
  });

  function cloudSilhouette(ctx, cx, cy, radius) {
    // The private Voice Op reference uses a true spherical clip. This exact
    // circle is invariant across every voice lifecycle state.
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.closePath();
  }

  function rgba(rgb, alpha) {
    return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`;
  }

  function draw({ ctx, width, height, time = 0, state = "CONNECTING",
                  energy = 0, reducedMotion = false }) {
    const mode = STATES[state] || STATES.CONNECTING;
    const profile = CLOUD_PROFILES[state] || CLOUD_PROFILES.DEFAULT;
    const palette = state === "ERROR" ? PALETTES.error : PALETTES.normal;
    const t = reducedMotion ? 3.42 : time * mode.speed;
    const e = Math.max(0, Math.min(1, energy || 0));
    const cx = width / 2;
    const cy = height / 2;
    // Exterior geometry is one stable product mark. State and outgoing audio
    // animate the internal cloud volume only.
    const radius = Math.min(width, height) * 0.385;
    ctx.clearRect(0, 0, width, height);
    if (radius < 2) return;

    // Reference layer 1: restrained cobalt/azure ambient bloom.
    ctx.save();
    const halo = ctx.createRadialGradient(
      cx, cy, radius * 0.58, cx, cy, radius * 1.34);
    halo.addColorStop(0, rgba(palette[1], 0.086));
    halo.addColorStop(0.48, rgba(palette[1], 0.054));
    halo.addColorStop(1, rgba(palette[0], 0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.34, 0, Math.PI * 2);
    ctx.fill();

    // Reference layers 2–3: true circular globe mask and a top-lit periwinkle
    // atmosphere anchored by deep cobalt at the bottom.
    cloudSilhouette(ctx, cx, cy, radius);
    ctx.save();
    ctx.clip();
    const sky = ctx.createLinearGradient(
      cx - radius * 0.30, cy - radius,
      cx + radius * 0.20, cy + radius);
    sky.addColorStop(0, state === "ERROR" ? rgba(palette[2], 0.90) : "rgba(99,130,255,0.98)");
    sky.addColorStop(0.32, state === "ERROR" ? rgba(palette[1], 0.94) : "rgba(67,97,238,0.97)");
    sky.addColorStop(0.66, state === "ERROR" ? rgba(palette[0], 0.96) : "rgba(49,82,224,0.97)");
    sky.addColorStop(0.90, "rgba(28,58,164,0.98)");
    sky.addColorStop(1, "rgba(17,35,112,0.99)");
    ctx.fillStyle = sky;
    ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);

    const topLight = ctx.createRadialGradient(
      cx - radius * 0.30, cy - radius * 0.38, radius * 0.04,
      cx - radius * 0.08, cy - radius * 0.16, radius * 1.02);
    topLight.addColorStop(0, `rgba(185,211,255,${0.58 * mode.glow})`);
    topLight.addColorStop(0.48, `rgba(116,157,255,${0.24 * mode.glow})`);
    topLight.addColorStop(1, "rgba(67,97,238,0)");
    ctx.fillStyle = topLight;
    ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);

    // Reference layer 4: a pale atmospheric shelf behind the signature cloud.
    const cloudBaseY = cy + radius * 0.055;
    const mist = ctx.createLinearGradient(
      cx, cloudBaseY - radius * 0.52, cx, cloudBaseY + radius * 0.72);
    mist.addColorStop(0, "rgba(255,255,255,0)");
    mist.addColorStop(0.28, `rgba(226,242,255,${0.30 * mode.cloud})`);
    mist.addColorStop(0.60, `rgba(188,226,255,${0.46 * mode.cloud})`);
    mist.addColorStop(0.86, `rgba(132,183,248,${0.20 * mode.cloud})`);
    mist.addColorStop(1, "rgba(67,97,238,0)");
    ctx.fillStyle = mist;
    ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);

    // Reference layer 5: a distinct lower-mid cumulus bank. Horizontal drift,
    // bright white cores, and layered crests are the recognizable Voice Op
    // composition that the earlier generic glow was missing.
    const puffs = [
      [-0.55, 0.05, 0.42, 0.32, 0.0, 0.95], [-0.28, -0.04, 0.48, 0.38, 1.3, 1.0],
      [0.02, 0.02, 0.52, 0.34, 2.7, 1.0], [0.32, -0.06, 0.46, 0.36, 4.1, 0.96],
      [0.58, 0.08, 0.40, 0.30, 5.4, 0.92], [-0.42, 0.22, 0.38, 0.28, 0.8, 0.85],
      [-0.10, 0.18, 0.45, 0.32, 2.2, 0.92], [0.22, 0.20, 0.42, 0.30, 3.6, 0.88],
      [0.48, 0.24, 0.36, 0.26, 4.9, 0.82], [-0.20, -0.16, 0.32, 0.42, 1.9, 0.75],
      [0.12, -0.18, 0.35, 0.40, 3.3, 0.78], [0.38, -0.12, 0.28, 0.44, 4.7, 0.70],
    ];
    const driftSpan = radius * 2.6 * profile.spread;
    for (const [x, y, size, speed, phase, opacity] of puffs) {
      const rawX = x * radius * 1.80 * profile.spread
        + (reducedMotion ? 0 : t * speed * 24 * profile.drift);
      const drift = ((rawX % driftSpan) + driftSpan) % driftSpan - driftSpan / 2;
      const billow = 1 + (reducedMotion ? 0 : Math.sin(t * 1.1 + phase) * 0.065) + e * 0.10;
      const px = cx + drift;
      const py = cloudBaseY + (y + profile.lift) * radius
        + (reducedMotion ? 0 : Math.sin(t * speed * 1.3 + phase) * radius * 0.025);
      // The reference was authored at a much larger stage size. Slightly
      // tighter puffs preserve its separate cumulus crests at the product's
      // compact 144px live size instead of blending into one white disc band.
      const pr = radius * size * 0.84 * billow;
      ctx.save();
      ctx.translate(px, py);
      ctx.scale(profile.puffX, profile.puffY);
      const cloud = ctx.createRadialGradient(
        -pr * 0.15, -pr * 0.20, pr * 0.02, 0, 0, pr);
      cloud.addColorStop(0, `rgba(255,255,255,${0.98 * mode.cloud * opacity})`);
      cloud.addColorStop(0.28, `rgba(247,251,255,${0.90 * mode.cloud * opacity})`);
      cloud.addColorStop(0.55, `rgba(220,240,255,${0.68 * mode.cloud * opacity})`);
      cloud.addColorStop(0.82, `rgba(165,210,255,${0.28 * mode.cloud * opacity})`);
      cloud.addColorStop(1, "rgba(67,97,238,0)");
      ctx.fillStyle = cloud;
      ctx.beginPath();
      ctx.arc(0, 0, pr, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    // Reference layer 6: three translucent wave contours bind the puffs into
    // one volumetric bank instead of a field of unrelated glowing dots.
    for (let wave = 0; wave < 3; wave += 1) {
      const baseY = cloudBaseY + (profile.lift + (wave - 1) * 0.14) * radius;
      const phase = t * (0.8 + wave * 0.3) + wave * 2.1;
      const amplitude = radius * (0.028 + wave * 0.012)
        * mode.cloud * profile.wave;
      ctx.beginPath();
      ctx.moveTo(cx - radius, cy + radius);
      ctx.lineTo(cx - radius, baseY);
      for (let step = 0; step <= 24; step += 1) {
        const unit = step / 24;
        const x = cx - radius + unit * radius * 2;
        const theta = unit * Math.PI * 4;
        const y = baseY + Math.sin(theta + phase) * amplitude
          + Math.cos(theta * 1.8 - phase * 0.7) * amplitude * 0.38;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(cx + radius, cy + radius);
      ctx.closePath();
      const veil = ctx.createLinearGradient(cx, baseY - amplitude, cx, baseY + radius * 0.62);
      const alpha = (0.24 - wave * 0.045) * mode.cloud;
      veil.addColorStop(0, `rgba(255,255,255,${alpha})`);
      veil.addColorStop(0.38, `rgba(224,242,254,${alpha * 0.72})`);
      veil.addColorStop(1, "rgba(59,130,246,0)");
      ctx.fillStyle = veil;
      ctx.fill();
    }

    // Reference layer 7: pale ice-blue internal radiance located inside the
    // cloud shelf rather than a generic full-orb white glow.
    const coreX = cx + (reducedMotion ? 0 : Math.sin(t * 0.7) * radius * 0.025);
    const coreY = cloudBaseY + profile.lift * radius
      + (reducedMotion ? 0 : Math.cos(t * 0.6) * radius * 0.022);
    const coreRadius = radius * (0.40 + e * 0.04);
    ctx.save();
    ctx.translate(coreX, coreY);
    ctx.scale(profile.coreX, profile.coreY);
    const core = ctx.createRadialGradient(0, 0, 0, 0, 0,
      radius * (0.40 + e * 0.04));
    core.addColorStop(0, `rgba(255,255,255,${0.62 * mode.glow})`);
    core.addColorStop(0.30, `rgba(240,249,255,${0.46 * mode.glow})`);
    core.addColorStop(0.66, `rgba(186,230,253,${0.24 * mode.glow})`);
    core.addColorStop(1, "rgba(67,97,238,0)");
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(0, 0, coreRadius, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Reference layer 8: bottom-edge cobalt depth. It reads as atmosphere and
    // volume, not a glass rim or hard ring.
    const shade = ctx.createRadialGradient(
      cx - radius * 0.24, cy - radius * 0.30, radius * 0.28,
      cx, cy, radius);
    shade.addColorStop(0, "rgba(255,255,255,0.04)");
    shade.addColorStop(0.70, "rgba(0,0,0,0)");
    shade.addColorStop(0.92, "rgba(15,23,85,0.11)");
    shade.addColorStop(1, "rgba(8,15,65,0.23)");
    ctx.fillStyle = shade;
    ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);
    ctx.restore();

    // Reference layer 9: ultra-soft perimeter feathering; no reflective glass.
    const feather = ctx.createRadialGradient(
      cx, cy, radius * 0.92, cx, cy, radius * 1.04);
    feather.addColorStop(0, "rgba(67,97,238,0)");
    feather.addColorStop(0.58, "rgba(67,97,238,0.086)");
    feather.addColorStop(1, "rgba(67,97,238,0)");
    ctx.fillStyle = feather;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.04, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  global.AlexVoiceOrb = Object.freeze({ draw, states: STATES, version: 6 });
})(window);
