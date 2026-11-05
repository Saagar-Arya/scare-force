"""Make a clip last exactly one scare, ending at a natural break instead of mid-sound.

    r = render(samples, rate, seconds=5.0, search_s=1.0, fade_s=0.5)
    r.samples    exactly round(seconds * rate) frames: the clip (repeated if it is shorter than
                 the scare), ended cleanly, then silence to the end
    r.audible_s  where the sound itself ends (<= seconds)
    r.plays      how many times the clip starts (1 when it covers the scare by itself)
    r.ending     "break" (stopped in a quiet moment) or "fade" (faded out)

A gap is a stretch at least MIN_GAP_S long whose 10 ms envelope is GAP_DB below the clip's loud
level (its 95th-percentile envelope): the pause between barks, words, calls or siren yelps.

Repeats: a clip shorter than (seconds - search_s) plays again straight after itself. A clip
that ends quietly repeats whole, keeping its own rhythm. A clip the build cut off mid-sound is
repeated only up to its last gap in its second half (if it has one), so the join falls in a
pause. Each copy starts abruptly, as the build made it; the copy before fades out over 10 ms
(50 ms if it ends loud, i.e. a clip with no usable gap).

How it ends, in order of preference:
  1. break: the last quiet moment in the final search_s (a gap, or a copy's quiet end). The
     sound stops there with a 10 ms fade, so no bark, word or shout is cut through.
  2. fade: no quiet moment there but a copy ends loud in that window (a clip cut mid-sound by the
     build): fade that copy out over fade_s, ending where it ends.
  3. fade: neither (the continuous sirens, a bark over street noise): fade out over fade_s,
     ending exactly at `seconds`.
So the audible part ends 0..search_s before the light and the arms stop; search_s = 0 always
fades out exactly on time.

Levels are unchanged (no gain, only fades towards silence), so every clip still plays at the
library's reference level (D-21, TR4 Test 4).

numpy only (already on the Pi for the detector); WAV in and out with the stdlib wave module.
"""

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HOP_S = 0.010        # envelope step
GAP_DB = 25.0        # a gap is this far below the clip's loud level (its 95th-percentile envelope)
MIN_GAP_S = 0.030    # and lasts at least this long
CLICK_FADE_S = 0.010  # fade at a cut in a quiet moment, and at a quiet join
REPEAT_TAIL = 1 / 2  # a repeated copy is trimmed only at a gap in its second half
JOIN_FADE_S = 0.050  # fade at a join where the copy ends loud (the build cut it mid-sound)


@dataclass
class Rendered:
    samples: np.ndarray  # int16, (frames,) or (frames, channels)
    rate: int
    audible_s: float
    plays: int
    ending: str          # "break" | "fade"


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: only 16-bit PCM WAVs are supported")
        ch, rate = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return (x.reshape(-1, ch) if ch > 1 else x), rate


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    ch = 1 if samples.ndim == 1 else samples.shape[1]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.ascontiguousarray(samples, dtype="<i2").tobytes())


def gaps(x: np.ndarray, rate: int) -> list[tuple[int, int]]:
    """Quiet gaps in a clip as (start, end) frame indices, in order."""
    mono = x.astype(np.float64)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    hop = max(1, round(HOP_S * rate))
    n = len(mono) // hop
    if n == 0:
        return []
    rms = np.sqrt((mono[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)
    db = 20 * np.log10(rms)
    quiet = db < np.percentile(db, 95) - GAP_DB
    min_hops = max(1, round(MIN_GAP_S / HOP_S))
    out, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i >= min_hops:
                out.append((i * hop, j * hop))
            i = j
        else:
            i += 1
    return out


def _fade_out(x: np.ndarray, end: int, frames: int) -> None:
    """Linear fade to silence over the `frames` before `end` (in place, float array)."""
    frames = min(frames, end)
    if frames <= 0:
        return
    ramp = np.linspace(1.0, 0.0, frames, endpoint=False)
    if x.ndim > 1:
        ramp = ramp[:, None]
    x[end - frames:end] *= ramp


def render(samples: np.ndarray, rate: int, seconds: float, search_s: float = 1.0,
           fade_s: float = 0.5) -> Rendered:
    if seconds <= 0:
        raise ValueError("seconds must be > 0")
    if search_s < 0 or fade_s <= 0:
        raise ValueError("search_s must be >= 0 and fade_s > 0")
    total = round(seconds * rate)
    window = total - round(search_s * rate)  # the ending may fall in [window, total]
    click = max(1, round(CLICK_FADE_S * rate))
    src = samples.astype(np.float64)
    clip_gaps = gaps(samples, rate)
    hop = max(1, round(HOP_S * rate))

    # 1. What one copy is, if the clip has to repeat.
    repeat = len(src) < window
    body = len(src)
    quiet_end = bool(clip_gaps) and clip_gaps[-1][1] >= len(src) - hop  # ends in a pause
    if repeat and not quiet_end:
        tail = [g for g in clip_gaps if g[0] >= len(src) * (1 - REPEAT_TAIL)]
        if tail:
            body, quiet_end = tail[-1][0] + click, True
    join_fade = click if quiet_end else round(JOIN_FADE_S * rate)

    # 2. Lay copies end to end until the scare is covered (one copy if it already is).
    out = np.zeros((total,) + src.shape[1:], dtype=np.float64)
    copies, pos = [], 0  # (start, frames)
    while pos < total and (repeat or not copies):
        n = min(body, total - pos)
        seg = src[:n].copy()
        if repeat and pos + n < total:  # a join
            _fade_out(seg, n, join_fade)
        out[pos:pos + n] = seg
        copies.append((pos, n))
        pos += n

    # 3. Where to stop.
    quiet = [s + a + click for s, n in copies for a, _ in clip_gaps if a < n]
    ends = [s + n for s, n in copies if n == body]  # copies that play to their end
    if quiet_end:
        quiet += ends
    quiet = [q for q in quiet if window <= q <= total]
    loud_ends = [e for e in ends if window <= e <= total] if not quiet_end else []
    if quiet:
        cut, fade, ending = max(quiet), click, "break"
    elif loud_ends:
        cut, fade, ending = max(loud_ends), round(fade_s * rate), "fade"
    else:
        cut, fade, ending = min(total, pos), round(fade_s * rate), "fade"
    _fade_out(out, cut, fade)
    out[cut:] = 0
    plays = sum(1 for s, _ in copies if s < cut - click)
    pcm = np.clip(np.round(out), -32768, 32767).astype(np.int16)
    return Rendered(pcm, rate, cut / rate, plays, ending)
