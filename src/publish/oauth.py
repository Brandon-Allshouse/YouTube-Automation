"""OAuth 2.0 for the owner's channels (installed-app flow with PKCE), stdlib only.

* Least-privilege scopes: ``youtube.upload`` (uploads + thumbnails), ``youtube.readonly`` and
  ``yt-analytics.readonly`` (stats). Never the full ``youtube`` scope.
* Loopback redirect with manual paste: the owner opens the consent URL, approves, and pastes the
  final ``http://127.0.0.1:...`` URL back. No server or open port is needed (works in containers).
* Tokens are stored **outside the repository** (``VP_SECRETS_DIR``, default
  ``~/.config/video-portfolio``) with file mode 0600; the client secret file path comes from
  ``YOUTUBE_OAUTH_CLIENT_SECRETS`` and must also be outside the repo.
* Never run by agents (denied in ``.claude/settings*.json``); never run during the overnight build.

``python -m src.publish.oauth authorize --channel atlas``
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from src.common.paths import REPO_ROOT, validate_id
from src.common.secrets import get_secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
REDIRECT = "http://127.0.0.1:8765/"


class OAuthError(RuntimeError):
    """Raised for configuration or token errors (never includes secret values)."""


def secrets_dir() -> Path:
    """Directory for tokens, guaranteed to be outside the repository."""
    d = Path(os.environ.get("VP_SECRETS_DIR") or Path.home() / ".config" / "video-portfolio").resolve()
    if d == REPO_ROOT or REPO_ROOT in d.parents:
        raise OAuthError("VP_SECRETS_DIR must be outside the repository")
    return d


def _client() -> dict[str, str]:
    path = get_secret("YOUTUBE_OAUTH_CLIENT_SECRETS")
    if not path:
        raise OAuthError("YOUTUBE_OAUTH_CLIENT_SECRETS is not set")
    p = Path(path).resolve()
    if REPO_ROOT in p.parents:
        raise OAuthError("client secret file must live outside the repository")
    data = json.loads(p.read_text(encoding="utf-8"))
    inst = data.get("installed") or {}
    if not inst.get("client_id") or not inst.get("client_secret"):
        raise OAuthError("client secret file is not a Desktop-app OAuth client")
    return {"client_id": str(inst["client_id"]), "client_secret": str(inst["client_secret"])}


def _token_path(channel: str) -> Path:
    return secrets_dir() / "oauth" / f"{validate_id(channel, kind='channel id')}.json"


def _post(url: str, form: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310
            data = json.loads(resp.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"token endpoint HTTP {exc.code}") from None
    if not isinstance(data, dict):
        raise OAuthError("unexpected token response")
    return data


def _save(channel: str, tok: dict[str, Any]) -> None:
    path = _token_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(tok, fh)
    os.chmod(path, 0o600)


def authorize(channel: str, *, input_fn: Any = input) -> None:
    """Interactive one-time consent for a channel; stores a refresh token."""
    c = _client()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": c["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }  # fmt: skip
    print("Open this URL, sign in with the channel's Google account, approve, then paste the final URL here:")
    print(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")
    pasted = str(input_fn("Final URL: ")).strip()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    if q.get("state", [""])[0] != state:
        raise OAuthError("state mismatch (possible CSRF); start again")
    code = q.get("code", [""])[0]
    if not code:
        raise OAuthError("no authorization code in the pasted URL")
    tok = _post(
        TOKEN_URL,
        {
            "code": code,
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    if "refresh_token" not in tok:
        raise OAuthError("no refresh token returned")
    tok["obtained_at"] = int(time.time())
    _save(channel, tok)
    print(f"Saved credentials for channel '{channel}' (outside the repo, mode 600).")


def access_token(channel: str) -> str:
    """A valid access token for the channel, refreshing when needed."""
    path = _token_path(channel)
    if not path.is_file():
        raise OAuthError(
            f"channel '{channel}' is not authorized; run: python -m src.publish.oauth authorize --channel {channel}"
        )
    tok = json.loads(path.read_text(encoding="utf-8"))
    if tok.get("access_token") and time.time() < tok.get("obtained_at", 0) + int(tok.get("expires_in", 0)) - 120:
        return str(tok["access_token"])
    c = _client()
    fresh = _post(
        TOKEN_URL,
        {
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    tok.update(fresh, obtained_at=int(time.time()))
    _save(channel, tok)
    return str(tok["access_token"])


def main(argv: list[str] | None = None) -> int:
    """CLI: ``authorize --channel <id>``."""
    p = argparse.ArgumentParser(description="OAuth for YouTube uploads/analytics (owner only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("authorize")
    a.add_argument("--channel", required=True)
    args = p.parse_args(argv)
    authorize(args.channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
