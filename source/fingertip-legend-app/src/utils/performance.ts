import type { PerformanceCue } from '../types/domain'
export function clampTime(value: number, duration: number) { return Math.max(0, Math.min(Number.isFinite(value) ? value : 0, Math.max(0, duration))) }
export function cueIndexAt(cues: PerformanceCue[], time: number) { if (!cues.length) return 0; const index = cues.findIndex(c => time >= c.start && time < c.end); return index >= 0 ? index : time < cues[0].start ? 0 : cues.length - 1 }
export function formatTime(seconds: number) { const tenths = Math.round(Math.max(0, Number.isFinite(seconds) ? seconds : 0)*10); return `${String(Math.floor(tenths / 600)).padStart(2,'0')}:${((tenths % 600)/10).toFixed(1).padStart(4,'0')}` }
