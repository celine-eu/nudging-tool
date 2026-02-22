from __future__ import annotations

import base64
import re
from pathlib import Path

import typer
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

vapid_app = typer.Typer(add_completion=False, help="Manage VAPID credentials")


def _generate_keypair() -> tuple[str, str]:
    """Return (public_key_b64url, private_key_pem) for a fresh P-256 keypair."""
    private_key = ec.generate_private_key(ec.SECP256R1())

    # Uncompressed point: 0x04 || X || Y  (65 bytes) — the only format browsers accept
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    return pub_b64, priv_pem


def _env_has_key(content: str, key: str) -> bool:
    return any(
        line.startswith(f"{key}=")
        for line in content.splitlines()
        if not line.startswith("#")
    )


def _pem_to_env_value(pem: str) -> str:
    """Collapse PEM to a single line with literal \\n so dotenv parsers keep it intact."""
    return "\\n".join(pem.strip().splitlines())


@vapid_app.command("gen")
def gen(
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        "-e",
        help="Path to the .env file to update.",
        show_default=True,
    ),
    subject: str = typer.Option(
        "mailto:info@celine.localhost",
        "--subject",
        "-s",
        help=(
            "VAPID subject — a mailto: or https: URI that push services use to "
            "contact you. Required. Example: mailto:ops@example.com"
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing VAPID keys even if already set.",
    ),
) -> None:
    """Generate a VAPID keypair and write it to an .env file.

    Skips keys that are already present unless --force is given.
    The subject must be a mailto: or https: URI — push services use it to
    reach you if your application misbehaves.
    """
    if not re.match(r"^(mailto:.+@.+|https://.+)", subject):
        typer.echo(
            "Error: --subject must be a mailto: or https: URI "
            "(e.g. mailto:ops@example.com)",
            err=True,
        )
        raise typer.Exit(1)

    existing = env_file.read_text() if env_file.exists() else ""

    keys_present = {
        k: _env_has_key(existing, k)
        for k in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT")
    }

    if all(keys_present.values()) and not force:
        typer.echo(f"All VAPID keys already set in {env_file} — nothing to do.")
        typer.echo("Use --force to regenerate.")
        raise typer.Exit(0)

    pub_b64, priv_pem = _generate_keypair()

    lines: list[str] = ["\n# VAPID — Web Push"]

    if not keys_present["VAPID_PUBLIC_KEY"] or force:
        lines.append(f"VAPID_PUBLIC_KEY={pub_b64}")
    if not keys_present["VAPID_PRIVATE_KEY"] or force:
        lines.append(f"VAPID_PRIVATE_KEY={_pem_to_env_value(priv_pem)}")
    if not keys_present["VAPID_SUBJECT"] or force:
        lines.append(f"VAPID_SUBJECT={subject}")

    env_file.write_text(existing.rstrip("\n") + "\n" + "\n".join(lines) + "\n")

    typer.echo(f"Written to {env_file}:")
    typer.echo(
        f"  VAPID_PUBLIC_KEY  = {pub_b64[:24]}...  ({65} raw bytes, P-256 uncompressed)"
    )
    typer.echo(f"  VAPID_PRIVATE_KEY = <PEM>")
    typer.echo(f"  VAPID_SUBJECT     = {subject}")


@vapid_app.command("show")
def show(
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        "-e",
        help="Path to the .env file to inspect.",
        show_default=True,
    ),
) -> None:
    """Show which VAPID keys are set in an .env file."""
    existing = env_file.read_text() if env_file.exists() else ""

    for key in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        status = "✓ set" if _env_has_key(existing, key) else "✗ missing"
        typer.echo(f"  {key:<20} {status}")
