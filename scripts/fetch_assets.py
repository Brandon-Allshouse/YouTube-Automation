"""Download fonts, map data and model weights with integrity verification.

Usage (inside the `fetch` container): ``python -m scripts.fetch_assets [--only fonts|maps|models]``

Security properties:
* Sources are pinned to exact commits / release tags; hosts are allowlisted (checked again after
  redirects).
* Each file has a size cap and a download timeout.
* SHA-256 checksums live in ``security/checksums.txt``. A file whose checksum is recorded must match
  exactly or it is deleted and the run fails. A file seen for the first time is recorded
  (trust-on-first-use) and reported, so the owner can review the new entry in the diff.
* Writes go to a temp file first and are renamed only after verification.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from src.common.paths import ASSETS_DIR, MODELS_DIR, REPO_ROOT, ensure_within

CHECKSUMS = REPO_ROOT / "security" / "checksums.txt"
ALLOWED_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "huggingface.co",
}
# Hugging Face redirects LFS downloads to regional CDN hosts; those files are checksum-pinned.
ALLOWED_HOST_SUFFIXES = (".hf.co",)
GOOGLE_FONTS_COMMIT = "b7c8331cf620346575487ef8db0e2c729f014675"  # pragma: allowlist secret (git commit SHA)
NATURAL_EARTH_COMMIT = "ca96624a56bd078437bca8184e78163e5039ad19"  # pragma: allowlist secret (git commit SHA)
KOKORO_RELEASE = "model-files-v1.0"
# Same Kokoro-82M v1.0 weights, exported with a per-token `durations` output (word timing).
KOKORO_TS_REPO = "onnx-community/Kokoro-82M-v1.0-ONNX-timestamped"
KOKORO_TS_COMMIT = "dd4401a9add81ac692d20e240d22ec9dda82cc29"  # pragma: allowlist secret (git commit SHA)
MB = 1024 * 1024


@dataclass(frozen=True)
class Asset:
    """One downloadable file."""

    group: str
    url: str
    dest: Path
    max_bytes: int


def _gf(path: str, dest_name: str) -> Asset:
    quoted = urllib.parse.quote(path)
    url = f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_FONTS_COMMIT}/{quoted}"
    return Asset("fonts", url, ASSETS_DIR / "fonts" / dest_name, 20 * MB)


def _ne(name: str) -> Asset:
    url = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NATURAL_EARTH_COMMIT}/geojson/{name}"
    return Asset("maps", url, ASSETS_DIR / "maps" / name, 60 * MB)


def _kokoro(name: str, max_mb: int) -> Asset:
    url = f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/{KOKORO_RELEASE}/{name}"
    return Asset("models", url, MODELS_DIR / "kokoro" / name, max_mb * MB)


MANIFEST: tuple[Asset, ...] = (
    _gf("ofl/inter/Inter[opsz,wght].ttf", "Inter[opsz,wght].ttf"),
    _gf("ofl/inter/OFL.txt", "Inter-OFL.txt"),
    _gf("ofl/fraunces/Fraunces[SOFT,WONK,opsz,wght].ttf", "Fraunces[SOFT,WONK,opsz,wght].ttf"),
    _gf("ofl/fraunces/OFL.txt", "Fraunces-OFL.txt"),
    _gf("ofl/spacegrotesk/SpaceGrotesk[wght].ttf", "SpaceGrotesk[wght].ttf"),
    _gf("ofl/spacegrotesk/OFL.txt", "SpaceGrotesk-OFL.txt"),
    _gf("ofl/jetbrainsmono/JetBrainsMono[wght].ttf", "JetBrainsMono[wght].ttf"),
    _gf("ofl/jetbrainsmono/OFL.txt", "JetBrainsMono-OFL.txt"),
    _gf("ofl/anton/Anton-Regular.ttf", "Anton-Regular.ttf"),
    _gf("ofl/anton/OFL.txt", "Anton-OFL.txt"),
    _ne("ne_110m_admin_0_countries.geojson"),
    _ne("ne_50m_admin_0_countries.geojson"),
    _ne("ne_50m_land.geojson"),
    _ne("ne_50m_rivers_lake_centerlines.geojson"),
    _ne("ne_110m_populated_places_simple.geojson"),
    _kokoro("kokoro-v1.0.onnx", 400),
    _kokoro("voices-v1.0.bin", 60),
    Asset(
        "models",
        f"https://huggingface.co/{KOKORO_TS_REPO}/resolve/{KOKORO_TS_COMMIT}/onnx/model.onnx",
        MODELS_DIR / "kokoro" / "kokoro-v1.0-timestamped.onnx",
        400 * MB,
    ),
)


def load_checksums() -> dict[str, str]:
    """Parse ``security/checksums.txt`` (``<sha256>  <repo-relative path>`` lines)."""
    out: dict[str, str] = {}
    if CHECKSUMS.is_file():
        for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                digest, rel = line.split(maxsplit=1)
                out[rel.strip()] = digest
    return out


def save_checksums(sums: dict[str, str]) -> None:
    """Write checksums sorted by path."""
    lines = ["# sha256  path   (verified by scripts/fetch_assets.py; TOFU entries reviewed in diff)"]
    lines += [f"{d}  {p}" for p, d in sorted(sums.items())]
    CHECKSUMS.parent.mkdir(parents=True, exist_ok=True)
    CHECKSUMS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(MB), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_host(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or (host not in ALLOWED_HOSTS and not host.endswith(ALLOWED_HOST_SUFFIXES)):
        raise ValueError(f"host not allowlisted: {parsed.hostname}")


def download(asset: Asset, timeout: float = 120.0) -> Path:
    """Download ``asset`` to a temp file, enforcing host allowlist and size cap."""
    _check_host(asset.url)
    dest = ensure_within(asset.dest, REPO_ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(asset.url, headers={"User-Agent": "video-portfolio-fetch/1"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
        _check_host(resp.geturl())
        total = 0
        with tmp.open("wb") as fh:
            while chunk := resp.read(MB):
                total += len(chunk)
                if total > asset.max_bytes:
                    fh.close()
                    tmp.unlink(missing_ok=True)
                    raise ValueError(f"size cap exceeded for {dest.name}")
                fh.write(chunk)
    return tmp


def main(argv: list[str] | None = None) -> int:
    """Fetch every asset in the manifest (optionally one group); verify and record checksums."""
    p = argparse.ArgumentParser(description="Fetch and verify assets")
    p.add_argument("--only", choices=("fonts", "maps", "models"))
    args = p.parse_args(argv)
    sums = load_checksums()
    failures = 0
    for asset in MANIFEST:
        if args.only and asset.group != args.only:
            continue
        rel = asset.dest.relative_to(REPO_ROOT).as_posix()
        expected = sums.get(rel)
        if asset.dest.is_file():
            actual = sha256_file(asset.dest)
            if expected is None:
                sums[rel] = actual
                print(f"recorded  {rel} (existing file, TOFU)")
            elif actual != expected:
                print(f"MISMATCH  {rel}: on-disk file does not match checksum", file=sys.stderr)
                failures += 1
            else:
                print(f"ok        {rel}")
            continue
        try:
            tmp = download(asset)
        except (OSError, ValueError) as exc:
            print(f"FAILED    {rel}: {exc}", file=sys.stderr)
            failures += 1
            continue
        actual = sha256_file(tmp)
        if expected is not None and actual != expected:
            tmp.unlink(missing_ok=True)
            print(f"MISMATCH  {rel}: download does not match recorded checksum", file=sys.stderr)
            failures += 1
            continue
        tmp.replace(asset.dest)
        if expected is None:
            sums[rel] = actual
            print(f"new       {rel} sha256={actual[:16]}… (TOFU, recorded)")
        else:
            print(f"verified  {rel}")
    save_checksums(sums)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
