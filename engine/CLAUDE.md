# engine/ conventions

- Only `engine/core/canvas.py` imports skia. Components draw through `Canvas`/`PathBuilder`/`Fonts`/`Picture`.
- Components never hard-code colors, fonts or easings: use `ctx.theme` (palette, font roles, `motion`) and `engine.components.base.entrance()`.
- Sizes scale with `ctx.unit` (1.0 at a 1080 px short side). Lay out inside `region_rect(ctx, region)`, never outside the safe zone or into the caption band.
- All randomness via `ctx.rng(key)` (deterministic per seed/key). No `random`, no time-based values.
- Heavy layout work (text fitting, path building) happens in `__init__`; `render()` must be cheap.
- Entrances: eased (no linear motion unless deliberate), staggered 30-80 ms, overshoot via theme `emphasis`.
- Every new component: spec model + JSON schema export + fixture scene + tests + a reviewed contact sheet. See `docs/ENGINE.md`.
- Review renders with `python -m src.quality.inspect <video> --duration N --every 0.5` and look at the images.
