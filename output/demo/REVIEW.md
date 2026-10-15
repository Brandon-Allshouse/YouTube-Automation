# Demo review log

The build agent cannot watch video, so each render is reviewed from contact sheets (1 frame/s),
key frames (first, 1 s hook, every scene start + 0.9 s), and numeric audio analysis + a
waveform/spectrogram image. Files: `<name>_review/`.

## engine_core.mp4 (step 2), 5 s engine test
- OK: antialiased text and strokes, glow, grid floor with perspective horizon, glitch transition (slice displacement + RGB split) visible mid-cut, path trim draw.
- No changes needed for an engine test.

## components_{atlas,neon}.mp4 (step 3)
Review 1 found:
1. Caption word gaps collapsed when the active word scaled ("Mostmapslie"); box-mode highlight touched neighbours. **Fix:** word gap +0.16 em (pop) / +0.34 em (box); active scale 1.10 → 1.06; box padding relative to font size.
2. StatCounter number undersized for a hero stat; label tiny. **Fix:** base 400 px, label 44-64 px.
3. ListReveal rows / QuoteCard text too small at phone scale. **Fix:** row cap 230 px, text 0.40 × row; quote base 118 px.
4. Atlas background flat. **Fix:** glow 0.30 → 0.50; caption sizes up (atlas 90, neon 84).
Review 2: all fixed; themes clearly distinct (serif/brass/wipe/pop captions vs grotesk/cyan/glitch/box captions).

## mercator-short.mp4 (step 5): the end-to-end demo Short
Render 1 (56.6 s):
1. **3 s of blank frames** in the "60° north" scene: StatCounter waited for the word "four". **Fix:** counter appears on scene entry; counting starts on the word (`count_at` / `count_at_word`).
2. Text-only scenes held 5-7 s (static > 4 s bar). **Fix:** `until_word` anchors so one segment can carry several visual beats (sailors, poles, purpose now two beats each).
3. Too long. **Fix:** tighter script lines; atlas voice speed 1.04 → 1.10 → 53.6 s (inside 60 s).
4. Comparison labels/values small. **Fix:** labels 88 px, values 54 px, bigger ratio badge.
Render 2 (53.6 s):
5. Comparison group hugged the top of its region, leaving dead space above captions; Africa square nearly touched its label. **Fix:** group vertically centred, 44 px label clearance. Verified with a full-res still.

Audio (render 2): -14.17 LUFS integrated (FFmpeg ebur128: -14.2), true peak -1.3 dBTP (-1.2 after AAC), 0 clipped samples, no silence gaps > 1.5 s, voice ≈ 19 dB over the ducked cinematic bed (73 bpm, key of C). Spectrogram: speech formants clear of the bed; risers end on each cut; hook impact at 0 s.

Known limits for the step-15 polish: visuals are typography + charts only; maps, icons and diagrams (step 6) should replace several title-only beats (e.g. an actual Mercator map stretching Greenland, a ship icon on a rhumb line).

## mercator-short.mp4, step 13 (quality checks)
Automated checks found: 10-13 readable words on the hook frame and the list, unreadable dimmed badge numbers (2.2:1), and a first sentence that was only a setup. **Fixes:** dropped the hook kicker, earlier list points recede to 30 %, hook rewritten ("…, but it isn't even close."). The check also caught camera push-in carrying text outside the safe zone (now prevented in layout).

## mercator-short.mp4, step 15 (polish with maps), FINAL
Replaced title-only beats with map scenes: Mercator hook with Greenland + Africa highlighted, a true-size slide (Greenland rotated on the sphere onto Africa, dashed Mercator outline left behind), a straight rhumb line across the Atlantic for "a straight line is a steady compass heading", and a closing Mercator → Equal Earth morph under "Check the latitude". Review found a map label entering the right-edge UI zone (labels now flip and clamp to the safe area) and empty space above the morph (payoff title added).
**Final:** 56.6 s, 1080x1920@30, 11 scenes. All quality checks pass (safe zones, ≤ 7 persistent words, contrast, longest static stretch 1.8 s, -14.18 LUFS / -1.3 dBTP, hook/readability/variety/sources).
**16:9:** `mercator-16x9.mp4` renders the same spec + audio in long-form layout. Map framing is now format-independent (same latitudes in both).

## mercator-short.mp4, voice pass (owner feedback: "too synthetic")
A/B of nine Kokoro samples of the same 18 s (`output/dev/voice_ab/`): the owner picked `am_fenrir`. Causes found: a blend of two C-grade voices, speed 1.1, sentence-by-sentence synthesis and digital silence. **Changes (ADR-031):** `am_fenrir` at speed 1.0; one continuous Kokoro pass per segment; word timings from the timestamped Kokoro export; sentence gaps still set by our pause model; lighter voice chain; room tone 54 dB under the voice. All 11 segments went through the whole-passage path (after the aligner learned that espeak fuses "from the").
**Result:** 54.8 s (was 56.6 s), -14.1 LUFS / -1.3 dBTP, no silence gaps, all quality checks pass; the contact sheet shows visual beats still landing on their words. The 16:9 twin and the TikTok/Reels exports were re-rendered with the new audio.
