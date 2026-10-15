"""Tests for DSP, loudness, caption timing, TTS orchestration, music and mixing."""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import signal

from src.audio import dsp
from src.audio.captions import align_sentence, internal_gaps, speech_span, strip_markup, syllables
from src.audio.mixer import layout_narration, master
from src.audio.music import allpass, feedback_comb, generate, plan
from src.audio.tts import (
    KOKORO_MODEL,
    KOKORO_TIMED_MODEL,
    TTS,
    TTS_SR,
    KokoroBackend,
    SyntheticBackend,
    VoiceSpec,
    align_phoneme_words,
    cut_sentences,
    pause_after,
    split_sentences,
)

SR = 48000
VOICE = VoiceSpec(blend=(("am_michael", 1.0),), speed=1.0)


def sine(freq: float, amp: float, dur: float = 3.0) -> np.ndarray:  # type: ignore[type-arg]
    t = np.arange(int(dur * SR)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


# --- loudness / dynamics --------------------------------------------------------------------


def test_bs1770_reference_tone() -> None:
    # 997 Hz sine at -20 dBFS in one channel reads -23.0 LUFS (BS.1770 reference behaviour).
    assert dsp.integrated_lufs(sine(997, dsp.db_to_gain(-20))) == pytest.approx(-23.0, abs=0.15)


def test_true_peak_of_sine() -> None:
    assert dsp.true_peak_dbtp(sine(1000, 0.5)) == pytest.approx(-6.02, abs=0.05)


def test_silence_is_minus_inf() -> None:
    assert dsp.integrated_lufs(np.zeros(SR * 2)) == float("-inf")


def test_limiter_respects_ceiling() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.5, SR * 2)
    y = dsp.limit(x, SR, ceiling_dbtp=-1.5)
    assert dsp.true_peak_dbtp(y) <= -1.4


def test_compressor_reduces_dynamics() -> None:
    quiet, loud = sine(200, 0.05, 1), sine(200, 0.8, 1)
    x = np.concatenate([quiet, loud])
    y = dsp.compress(x, SR, threshold_db=-24, ratio=4)[:, 0]
    ratio_in = np.max(np.abs(x[SR:])) / np.max(np.abs(x[:SR]))
    ratio_out = np.max(np.abs(y[SR + SR // 2 :])) / np.max(np.abs(y[SR // 2 : SR]))
    assert ratio_out < ratio_in * 0.6


def test_lowpass_attenuates_highs() -> None:
    y = dsp.eq(sine(10000, 0.5), "lowpass", 1000, SR)
    assert np.max(np.abs(y[SR:])) < 0.02


@settings(max_examples=30)
@given(d=st.integers(2, 60), g=st.floats(-0.95, 0.95), n=st.integers(1, 400))
def test_feedback_comb_matches_lfilter(d: int, g: float, n: int) -> None:
    x = np.random.default_rng(n).normal(size=n)
    a = np.zeros(d + 1)
    a[0], a[d] = 1.0, -g
    ref = signal.lfilter([1.0], a, x)
    assert np.allclose(feedback_comb(x, d, g), ref, atol=1e-9)


@settings(max_examples=30)
@given(d=st.integers(1, 60), g=st.floats(-0.9, 0.9), n=st.integers(1, 400))
def test_allpass_matches_lfilter(d: int, g: float, n: int) -> None:
    x = np.random.default_rng(n + 1).normal(size=n)
    b = np.zeros(d + 1)
    a = np.zeros(d + 1)
    b[0], b[d] = -g, 1.0
    a[0], a[d] = 1.0, -g
    assert np.allclose(allpass(x, d, g), signal.lfilter(b, a, x), atol=1e-9)


# --- text + captions ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "n"),
    [
        ("map", 1),
        ("Greenland", 2),
        ("Africa", 3),
        ("projection", 3),
        ("Mercator", 3),
        ("stretches", 2),
        ("1931", 5),
        ("the", 1),
        ("usual", 3),
    ],
)
def test_syllables(word: str, n: int) -> None:
    assert syllables(word) == n


def test_split_sentences_handles_abbreviations() -> None:
    s = split_sentences("Dr. Smith went to the U.S. in 1931. It rained! Why? Because.")
    assert s == ["Dr. Smith went to the U.S. in 1931.", "It rained!", "Why?", "Because."]


def test_pause_model() -> None:
    rng = np.random.default_rng(0)
    q = pause_after("Is it?", rng)
    d = pause_after("This is a much longer declarative sentence here.", rng)
    e = pause_after("And then…", rng)
    assert 0.3 < q < 0.6 and 0.25 < d < 0.45 and e > 0.5
    assert pause_after("End.", rng, segment_end=True) > 0.4


def test_strip_markup() -> None:
    clean, flags = strip_markup("Africa is *fourteen* times larger.")
    assert clean == "Africa is fourteen times larger."
    assert flags == [False, False, True, False, False]


def test_alignment_monotonic_and_matches_clause_gap() -> None:
    sent = "Greenland looks huge, but Africa is far bigger."
    audio = SyntheticBackend().synth(sent, VOICE)
    words = align_sentence(sent, audio, TTS_SR)
    assert [w.text for w in words] == sent.split()
    dur = len(audio) / TTS_SR
    for a, b in itertools.pairwise(words):
        assert a.start < a.end <= b.start + 1e-6
    assert words[-1].end <= dur + 1e-6
    gaps = internal_gaps(audio, TTS_SR)
    assert gaps, "synthetic backend leaves a pause after the comma"
    # 'but' starts after the detected comma pause
    assert words[3].start >= gaps[0][0] - 0.02


def test_speech_span_trims_silence() -> None:
    x = np.concatenate([np.zeros(TTS_SR // 2), sine(200, 0.3, 1.0)[:TTS_SR], np.zeros(TTS_SR // 2)])
    on, off = speech_span(x, TTS_SR)
    assert on == pytest.approx(0.5, abs=0.02) and off == pytest.approx(1.5, abs=0.02)


# --- tts orchestration ----------------------------------------------------------------------


def test_narrate_with_synthetic_backend_and_cache(tmp_path: Path) -> None:
    tts = TTS(SyntheticBackend(), cache_dir=tmp_path)
    out = tts.narrate([["One sentence here.", "Two?"], ["Three!"]], VOICE, seed=1)
    assert [s.text for s in out] == ["One sentence here.", "Two?", "Three!"]
    # one passage per segment: audio + word spans
    assert len(list(tmp_path.glob("*.wav"))) == 2
    assert len(list(tmp_path.glob("*.json"))) == 2
    again = tts.narrate([["One sentence here.", "Two?"], ["Three!"]], VOICE, seed=1)
    assert np.array_equal(out[0].samples, again[0].samples)
    assert [s.pause_after for s in out] == [s.pause_after for s in again]
    assert [s.words for s in out] == [s.words for s in again]


class _UntimedBackend:
    """Wraps the synthetic backend without word timings (like the plain Kokoro export)."""

    name = "untimed"

    def synth(self, text: str, voice: VoiceSpec) -> np.ndarray:  # type: ignore[type-arg]
        return SyntheticBackend().synth(text, voice)


def test_narrate_falls_back_to_per_sentence_without_timings(tmp_path: Path) -> None:
    out = TTS(_UntimedBackend(), cache_dir=tmp_path).narrate([["One here.", "Two?"]], VOICE, seed=1)
    assert len(list(tmp_path.glob("*.wav"))) == 2
    assert all(s.words is None for s in out)
    nar = layout_narration(out)
    assert [w.text for w in nar.words] == ["One", "here.", "Two?"]


def test_passage_words_are_model_timed_and_markup_aware(tmp_path: Path) -> None:
    seg = ["Blame a map from *{1569|fifteen sixty-nine}*.", "Sailors loved it, mostly."]
    out = TTS(SyntheticBackend(), cache_dir=tmp_path).narrate([seg], VOICE, seed=3)
    words = out[0].words
    assert words is not None
    assert [w.text for w in words] == ["Blame", "a", "map", "from", "1569."]
    assert words[-1].emph
    assert all(0 <= w.start < w.end <= out[0].duration + 1e-6 for w in words)
    assert all(a.end <= b.start for a, b in itertools.pairwise(words))
    # the clip starts just before its first word (short lead-in, no model gap)
    assert words[0].start <= 0.07
    nar = layout_narration(out)
    assert [w.text for w in nar.words][4:] == ["1569.", "Sailors", "loved", "it,", "mostly."]
    assert nar.words[5].start - nar.words[4].end >= out[0].pause_after - 0.01


def test_cut_sentences_drops_model_gap_and_keeps_tail() -> None:
    sr = TTS_SR
    tone = np.sin(2 * np.pi * 150 * np.arange(int(0.5 * sr)) / sr).astype(np.float32) * 0.4
    gap = np.zeros(int(0.8 * sr), np.float32)
    samples = np.concatenate([tone, gap, tone])
    spans = [(0.0, 0.5), (1.3, 1.8)]
    clips = cut_sentences(["Hi.", "There."], samples, spans, [0.3, 0.0])
    assert clips[0].duration == pytest.approx(0.5, abs=0.03)  # tail ends where sound decays
    assert clips[1].duration == pytest.approx(0.56, abs=0.03)  # 60 ms lead-in + word
    assert clips[1].words is not None
    assert clips[1].words[0].start == pytest.approx(0.06, abs=0.01)


def test_room_tone_fills_pauses_quietly() -> None:
    voice = np.concatenate([sine(200, 0.3, 1.0), np.zeros(SR), sine(200, 0.3, 1.0)])
    mix, _ = master(voice, None)
    pause = mix[int(1.3 * SR) : int(1.7 * SR), 0]
    level = 20 * np.log10(np.sqrt(np.mean(pause**2)))
    assert -80 < level < -55  # present, but far below speech
    dry, _ = master(voice, None, room_db=None)
    assert np.max(np.abs(dry[int(1.3 * SR) : int(1.7 * SR), 0])) < 1e-3


def test_voice_spec_rejects_bad_names() -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic ValidationError subclasses ValueError
        VoiceSpec(blend=(("../../etc", 1.0),))


@pytest.mark.needs_models
@pytest.mark.skipif(not KOKORO_MODEL.is_file(), reason="Kokoro weights not downloaded")
def test_kokoro_sentence(tmp_path: Path) -> None:
    tts = TTS(cache_dir=tmp_path)
    s = tts.sentence("Maps are flat; the Earth is not.", VOICE)
    assert 1.0 < len(s) / TTS_SR < 5.0
    assert np.max(np.abs(s)) > 0.05


@pytest.mark.needs_models
@pytest.mark.skipif(not KOKORO_TIMED_MODEL.is_file(), reason="timestamped Kokoro export not downloaded")
def test_kokoro_passage_word_spans_land_on_speech(tmp_path: Path) -> None:
    words = "Slide Greenland down to Africa at its true size. Africa is fourteen times bigger.".split()
    res = KokoroBackend().synth_words(words, VOICE)
    assert res is not None
    audio, spans = res
    assert len(spans) == len(words)
    assert all(a[1] <= b[0] + 1e-3 for a, b in itertools.pairwise(spans))
    peak = float(np.max(np.abs(audio)))
    for s, e in spans:  # every word span carries sound
        seg = audio[int(s * TTS_SR) : int(e * TTS_SR)]
        assert float(np.max(np.abs(seg))) > 0.1 * peak
    # the sentence break falls between "size." and "Africa"
    assert spans[9][0] - spans[8][1] > 0.05


# --- music + mix ----------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["cinematic", "synthwave", "lofi", "ambient"])
def test_music_deterministic_and_clean(style: str) -> None:
    a, arr = generate(style, 6.0, seed=3, cues=(3.0,))  # type: ignore[arg-type]
    b, _ = generate(style, 6.0, seed=3, cues=(3.0,))  # type: ignore[arg-type]
    c, _ = generate(style, 6.0, seed=4, cues=(3.0,))  # type: ignore[arg-type]
    assert a.shape == (6 * SR, 2)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)
    assert np.isfinite(a).all() and np.max(np.abs(a)) <= 0.7001
    assert arr == plan(style, 3)  # type: ignore[arg-type]


def test_music_rejects_bad_duration() -> None:
    with pytest.raises(ValueError, match="duration"):
        generate("cinematic", 0.0, seed=1)


def test_master_hits_loudness_and_peak_targets() -> None:
    tts = TTS(SyntheticBackend(), cache_dir=Path("/tmp/tts-test"))
    sents = tts.narrate([split_sentences("Maps lie. Greenland looks huge, but it is not. Here is why.")], VOICE)
    nar = layout_narration(sents)
    music, _ = generate("synthwave", nar.duration, seed=9)
    mix, rep = master(nar.track, music)
    assert rep.lufs == pytest.approx(-14.0, abs=0.5)
    assert rep.true_peak_dbtp <= -1.0
    assert mix.shape[1] == 2
    assert rep.voice_lufs - rep.music_lufs > 8  # voice clearly on top
    assert nar.words[0].start == pytest.approx(0.25, abs=0.05)
    assert math.isclose(nar.duration, len(nar.track) / SR, abs_tol=0.01)


def _timed(group: str, t0: float, step: float = 0.05) -> list[tuple[str, float, float]]:
    return [(p, t0 + k * step, t0 + (k + 1) * step) for k, p in enumerate(group)]


def test_align_handles_fused_and_split_words() -> None:
    # espeak fuses "from the" in context and splits "sixty-nine" into two groups
    words = ["fɹʌm", "ðə", "ɪkwˈeɪɾɚ".replace("ˈ", ""), "sɪksti-naɪn".replace("-", "")]
    groups = [_timed("fɹʌmðɪ", 0.0), _timed("ɪkweɪɾɚ", 0.5), _timed("sɪksti", 1.0), _timed("naɪn", 1.4)]
    spans = align_phoneme_words(words, groups)
    assert spans is not None
    assert spans[0] == pytest.approx((0.0, 0.2))  # "from" = first 4 of 6 phonemes
    assert spans[1] == pytest.approx((0.2, 0.3))
    assert spans[2] == pytest.approx((0.5, 0.85))
    assert spans[3] == pytest.approx((1.0, 1.6))  # both groups


def test_align_rejects_unrelated_phonemes() -> None:
    assert align_phoneme_words(["kæt", "dɔɡ"], [_timed("ʃɪp", 0.0), _timed("tɹiː", 0.3)]) is None
