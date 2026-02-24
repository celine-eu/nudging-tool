"""CLI seed commands.

nudging-cli seed apply ./seed [options]

Authentication resolves in priority order:
  1. Explicit CLI flags
  2. Environment variables (CELINE_OIDC_* from celine-sdk OidcSettings)

Two grant types are supported:
  - Resource Owner Password Credentials: --admin-user / --admin-password
  - Client Credentials:                  --client-id / --client-secret

The token endpoint is derived from CELINE_OIDC_BASE_URL by appending
/protocol/openid-connect/token (Keycloak convention) unless --token-url
is provided explicitly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
import typer
import yaml

from celine.nudging.config.settings import settings

seed_app = typer.Typer(add_completion=False, help="Manage seed data")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEYCLOAK_TOKEN_PATH = "/protocol/openid-connect/token"


def _resolve(
    flag_value: Optional[str], env_value: Optional[str], label: str
) -> Optional[str]:
    """Return first non-empty value, preferring explicit flag."""
    return flag_value or env_value or None


def _token_url_from_base(base_url: str) -> str:
    return base_url.rstrip("/") + _KEYCLOAK_TOKEN_PATH


def _fetch_token_password(
    token_url: str,
    username: str,
    password: str,
    client_id: str,
    client_secret: Optional[str],
    scope: str,
) -> str:
    data: dict = {
        "grant_type": "password",
        "username": username,
        "password": password,
        "client_id": client_id,
        "scope": scope,
    }
    if client_secret:
        data["client_secret"] = client_secret

    resp = httpx.post(token_url, data=data, timeout=15)
    if resp.status_code != 200:
        typer.echo(f"Token request failed ({resp.status_code}): {resp.text}", err=True)
        raise typer.Exit(1)
    return resp.json()["access_token"]


def _fetch_token_client_credentials(
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str,
) -> str:
    data: dict = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }

    resp = httpx.post(token_url, data=data, timeout=15)
    if resp.status_code != 200:
        typer.echo(f"Token request failed ({resp.status_code}): {resp.text}", err=True)
        raise typer.Exit(1)
    return resp.json()["access_token"]


def _load_seed(seed_dir: Path) -> tuple[list, list, list]:
    """Load the three YAML files from seed_dir. Missing files are treated as empty."""

    def _read(name: str, key: str) -> list:
        p = seed_dir / name
        if not p.exists():
            typer.echo(f"  [warn] {name} not found — skipping.", err=True)
            return []
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return data.get(key, [])

    rules = _read("rules.yaml", "rules")
    templates = _read("templates.yaml", "templates")
    preferences = _read("preferences.yaml", "preferences")
    return rules, templates, preferences


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@seed_app.command("apply")
def apply(
    seed_dir: Path = typer.Argument(
        Path(settings.SEED_DIR or "./seed"),
        help="Directory containing rules.yaml, templates.yaml, preferences.yaml.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    api_url: str = typer.Option(
        "http://api.celine.localhost/nudging",
        "--api-url",
        envvar="NUDGING_API_URL",
        help="Base URL of the nudging service (e.g. https://nudging.example.com).",
        show_default=False,
    ),
    # --- password grant ---
    admin_user: Optional[str] = typer.Option(
        None,
        "--admin-user",
        envvar="NUDGING_ADMIN_USER",
        help="Username for resource-owner-password grant.",
    ),
    admin_password: Optional[str] = typer.Option(
        None,
        "--admin-password",
        envvar="NUDGING_ADMIN_PASSWORD",
        help="Password for resource-owner-password grant.",
    ),
    # --- client credentials ---
    # These mirror CELINE_OIDC_CLIENT_ID / CELINE_OIDC_CLIENT_SECRET from the SDK.
    client_id: Optional[str] = typer.Option(
        settings.oidc.client_id,
        "--client-id",
        envvar="CELINE_OIDC_CLIENT_ID",
        help="OAuth2 client ID (also read from CELINE_OIDC_CLIENT_ID).",
    ),
    client_secret: Optional[str] = typer.Option(
        settings.oidc.client_secret,
        "--client-secret",
        envvar="CELINE_OIDC_CLIENT_SECRET",
        help="OAuth2 client secret (also read from CELINE_OIDC_CLIENT_SECRET).",
    ),
    # --- OIDC coordinates ---
    # Mirrors CELINE_OIDC_BASE_URL from OidcSettings.
    oidc_base_url: Optional[str] = typer.Option(
        settings.oidc.base_url,
        "--oidc-base-url",
        envvar="CELINE_OIDC_BASE_URL",
        help="OIDC issuer base URL (e.g. https://keycloak/realms/celine). "
        "Token endpoint is derived automatically.",
    ),
    token_url: Optional[str] = typer.Option(
        None,
        "--token-url",
        envvar="NUDGING_TOKEN_URL",
        help="Override the token endpoint URL directly.",
    ),
    scope: str = typer.Option(
        "nudging.admin",
        "--scope",
        envvar="NUDGING_SCOPE",
        help="OAuth2 scope(s) to request.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate YAML locally without sending any HTTP request.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read seed YAML files and POST them to /admin/seed/apply."""

    # ------------------------------------------------------------------
    # 1. Load and validate YAML
    # ------------------------------------------------------------------
    rules, templates, preferences = _load_seed(seed_dir)
    total = len(rules) + len(templates) + len(preferences)

    typer.echo(
        f"Loaded seed: {len(rules)} rules, {len(templates)} templates, "
        f"{len(preferences)} preferences  (total {total})"
    )

    if dry_run:
        typer.echo("Dry-run mode — no HTTP request sent.")
        raise typer.Exit(0)

    if total == 0:
        typer.echo("Nothing to seed.", err=True)
        raise typer.Exit(0)

    # ------------------------------------------------------------------
    # 2. Resolve api_url
    # ------------------------------------------------------------------
    if not api_url:
        typer.echo("--api-url / NUDGING_API_URL is required.", err=True)
        raise typer.Exit(1)

    # ------------------------------------------------------------------
    # 3. Resolve token endpoint
    # ------------------------------------------------------------------
    resolved_token_url = token_url
    if not resolved_token_url:
        if not oidc_base_url:
            typer.echo(
                "--token-url or --oidc-base-url / CELINE_OIDC_BASE_URL is required.",
                err=True,
            )
            raise typer.Exit(1)
        resolved_token_url = _token_url_from_base(oidc_base_url)

    if verbose:
        typer.echo(f"Token URL: {resolved_token_url}")

    print(client_id, client_secret)

    # ------------------------------------------------------------------
    # 4. Obtain JWT
    # ------------------------------------------------------------------
    if admin_user and admin_password:
        # Password grant — client_id is required even for this flow.
        if not client_id:
            typer.echo(
                "--client-id / CELINE_OIDC_CLIENT_ID is required for the password grant.",
                err=True,
            )
            raise typer.Exit(1)
        if verbose:
            typer.echo(f"Using password grant for user: {admin_user}")
        token = _fetch_token_password(
            token_url=resolved_token_url,
            username=admin_user,
            password=admin_password,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
        )
    elif client_id and client_secret:
        if verbose:
            typer.echo(f"Using client-credentials grant for client: {client_id}")
        token = _fetch_token_client_credentials(
            token_url=resolved_token_url,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
        )
    else:
        typer.echo(
            "Provide either (--admin-user + --admin-password) "
            "or (--client-id + --client-secret) for authentication.",
            err=True,
        )
        raise typer.Exit(1)

    # ------------------------------------------------------------------
    # 5. POST seed payload
    # ------------------------------------------------------------------
    endpoint = api_url.rstrip("/") + "/admin/seed/apply"
    payload = {
        "rules": rules,
        "templates": templates,
        "preferences": preferences,
    }

    if verbose:
        typer.echo(f"POST {endpoint}")

    try:
        resp = httpx.post(
            endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
    except httpx.RequestError as exc:
        typer.echo(f"Connection error: {exc}", err=True)
        raise typer.Exit(1)

    # ------------------------------------------------------------------
    # 6. Result
    # ------------------------------------------------------------------
    if resp.status_code == 200:
        data = resp.json()
        typer.echo(
            f"Seed applied: {data.get('rules')} rules, "
            f"{data.get('templates')} templates, "
            f"{data.get('preferences')} preferences."
        )
    elif resp.status_code in (401, 403):
        typer.echo(
            f"Authorization error ({resp.status_code}): {resp.text}\n"
            "Check credentials and that the token has the required scope/role.",
            err=True,
        )
        raise typer.Exit(1)
    elif resp.status_code == 422:
        typer.echo(
            f"Seed data validation error (422):\n{resp.text}",
            err=True,
        )
        raise typer.Exit(1)
    else:
        typer.echo(
            f"Unexpected error ({resp.status_code}):\n{resp.text}",
            err=True,
        )
        raise typer.Exit(1)
