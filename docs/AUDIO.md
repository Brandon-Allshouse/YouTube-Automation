# Audio

Everything except the TTS model is our own code (NumPy/SciPy primitives).

```mermaid
flowchart LR
  script[Script sentences<br/>*emphasis* markup] --> tts[Kokoro per sentence<br/>src/audio/tts.py]
  tts --> cache[(data/cache/tts)]
  tts --> layout[layout_narration<br/>pause model]
  layout --> align[caption timing<br/>src/audio/captions.py]
  layout --> chain[voice chain]
  music[procedural music<br/>src/audio/music.py] --> duck[sidechain duck]
  chain --> duck
  duck --> master[loudness normalise + TP limiter<br/>-14 LUFS, ≤ -1 dBTP]
  master --> wav[48 kHz stereo WAV] --> mux[muxed AAC 320k]
  align --> spec[captions.words in scene spec]
```

## Voice (`src/audio/tts.py`)
- **Kokoro-82M v1.0** (Apache-2.0 weights) via `kokoro-onnx` + ONNX Runtime; phonemes from espeak-ng (bundled by `espeakng-loader`). 24 kHz mono output, resampled to 48 kHz for the mix.
- **Per sentence** synthesis so every sentence's duration is exact (caption + scene alignment).
- **Channel voices** are weighted blends of Kokoro voice styles (`config/channels/*.yaml`, e.g. atlas = 0.65 `bm_george` + 0.35 `bm_fable`, speed 1.04), so each channel has its own synthetic voice that imitates no real person.
- **Natural pacing:** pauses depend on punctuation (`?` 0.44 s, `!` 0.36 s, `…` 0.62 s, `:` 0.30 s, `.` 0.34 s or 0.27 s for punchy sentences ≤ 5 words), +0.22 s at segment ends, ±12 % seeded jitter. No robotic uniform gaps.
- **Cache:** `data/cache/tts/<sha256(backend, model checksum, voice, text)>.wav`.
- `SyntheticBackend` makes speech-shaped tones for CI/unit tests only; it is never used for output.
- Container note: phonemizer copies `libespeak-ng.so` to a temp dir and `dlopen`s it, which fails on a `noexec` `/tmp`. A dedicated 32 MB exec tmpfs (`/var/tmp/dlopen`) is used only during that load (see LEARNINGS).

## Captions (`src/audio/captions.py`)
Our alignment, per sentence:
1. Speech onset/offset from a 10 ms energy envelope (-38 dB relative floor).
2. Internal pauses ≥ 70 ms are matched to clause punctuation (`, ; : —`) by expected position (within 25 % of the span), so clause boundaries land where the voice actually pauses.
3. Within each clause, time is distributed by word weight = syllables + 0.35 + 0.02·letters (our syllable counter handles silent -e, -ed, hiatus pairs, years and numbers).
`*word*` in scripts marks emphasis (accent colour in captions); asterisks are stripped before TTS.
Future refinement: forced alignment if drift is noticed in review.

## Music (`src/audio/music.py`)
Styles: `cinematic` (70-84 bpm, minor, 2-bar chords, detuned saw pads + sub, Karplus-Strong plucks, big reverb), `synthwave` (96-108 bpm, arpeggiated pulse, 8th-note bass, four-on-the-floor kick with sidechain pump), `lofi` (dorian 7ths, swung hats, lazy kick/snare), `ambient` (lydian, slow pads). The channel seed picks tempo, key and progression; arrangements follow an intro → body → outro energy curve. Sound design: a noise riser that ends exactly on each scene cut and a low impact under the hook. Pads and leads get a -4 dB dip at 2.5 kHz so the voice owns the presence range.
Performance: combs, allpasses and Karplus-Strong use block-wise recursion (one vectorised op per delay length), ~0.2 s per 14 s of music (40× faster than `lfilter` with long sparse delays). Property tests prove equivalence with `scipy.signal.lfilter`.

## Mixing (`src/audio/mixer.py`, `src/audio/dsp.py`)
- Voice chain: 80 Hz HPF → -2 dB @ 260 Hz → +2.5 dB @ 3.2 kHz → +1.5 dB air shelf @ 10 kHz → split-band de-esser (5-9.5 kHz) → soft-knee RMS compressor (3:1, -24 dB, +4 dB makeup).
- Music is placed at **voice − 12 dB** (channel-configurable) and ducked **8 dB** under speech (30 ms attack, 450 ms release), breathing up in pauses.
- Master: gain to **-14 LUFS integrated** (our ITU-R BS.1770-4 meter with absolute and relative gating), then a lookahead **true-peak limiter** (4× oversampled detection, ceiling -1.3 dBTP), iterated until within 0.2 LU.
- Verified: our meter matches FFmpeg `ebur128` (e.g. -14.02 vs -14.0 LUFS, TP -1.3 vs -1.3). Reference test: 997 Hz sine at -20 dBFS → -23.0 LUFS.

## Analysis (`src/audio/analyze.py`)
Loudness, true peak, clipped samples, silence gaps > 1.5 s, speech-to-music ratio, FFmpeg cross-check, and a waveform + log-frequency spectrogram PNG drawn with our canvas.

## Targets (from `config/rules.yaml`)
| Metric | Target |
|---|---|
| Integrated loudness | -14 LUFS ± 1 |
| True peak | ≤ -1 dBTP |
| Silence gaps | none > 1.5 s |
| Clipping | 0 samples |
