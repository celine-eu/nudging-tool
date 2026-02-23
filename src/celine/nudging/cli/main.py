from __future__ import annotations

import typer
from celine.nudging.cli.vapid import vapid_app


def create_app():
    app = typer.Typer(add_completion=True, help="CELINE Nudging tool CLI")
    app.add_typer(vapid_app, name="vapid")
    app()


if __name__ == "__main__":
    app = create_app()
