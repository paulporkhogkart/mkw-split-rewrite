"""Generate the call page's phone tones: authentic Australian network tones,
synthesized. 8 kHz mono 16-bit wav, peak ~25% FS to match spoken-voice levels.

  ringback.wav  413 + 438 Hz (25 Hz beat = the purr), 400/200/400/2000 ms;
                the same double-ring cadence the 802's physical bell does.
  busy.wav      425 Hz, 375 ms on / 375 ms off, three beeps; played once when
                a call ends (what a real AU phone does when the far end hangs up).
  dialtone.wav  413 + 438 Hz continuous, 2 s; the speaker-test sound.

Run from hotline/server/:  python scripts/gen_sfx.py
Overwrites hotline/static/sfx/. The old MicroSIP wavs these replaced were
generic European tones (ringing.wav was a flat 425 Hz sine).
"""
from __future__ import annotations

import array
import math
from pathlib import Path

FR = 8000
AMP = 4100          # per-tone amplitude; two summed tones peak ~8200 (~25% FS)
RAMP_MS = 10        # raised-cosine edges so bursts never click

OUT = Path(__file__).resolve().parent.parent / "hotline" / "static" / "sfx"


def burst(ms: int, freqs: tuple[float, ...], amp: float = AMP) -> array.array:
    n = FR * ms // 1000
    ramp = FR * RAMP_MS // 1000
    out = array.array("h")
    for i in range(n):
        t = i / FR
        v = amp * sum(math.sin(2 * math.pi * f * t) for f in freqs)
        if i < ramp:
            v *= (1 - math.cos(math.pi * i / ramp)) / 2
        elif i > n - ramp:
            v *= (1 - math.cos(math.pi * (n - i) / ramp)) / 2
        out.append(int(v))
    return out


def silence(ms: int) -> array.array:
    return array.array("h", [0] * (FR * ms // 1000))


def write(name: str, chunks: list[array.array]) -> None:
    import wave

    data = array.array("h")
    for c in chunks:
        data += c
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FR)
        w.writeframes(data.tobytes())
    print(f"{name}: {len(data) / FR:.2f}s")


def main() -> None:
    purr = (413.0, 438.0)
    write("ringback.wav", [burst(400, purr), silence(200),
                           burst(400, purr), silence(2000)])
    beep = burst(375, (425.0,), amp=8200 / 1)
    write("busy.wav", [beep, silence(375), beep, silence(375), beep])
    write("dialtone.wav", [burst(2000, purr)])


if __name__ == "__main__":
    main()
