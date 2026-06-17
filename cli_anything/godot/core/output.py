"""Shared CLI output helpers, factored from the baseline godot_cli.py.

All command modules import ``emit`` and ``handle_error`` from here so the
``--json`` / human-readable output and RuntimeError formatting stay identical
across every command group.
"""

from __future__ import annotations

import functools
import json as _json
import sys

import click


def emit(ctx, data: dict) -> None:
    """Print result as JSON or human-readable based on the click context.

    Mirrors the baseline ``_out``: when ``ctx.obj['json']`` is set, dumps JSON;
    otherwise prints a friendly key/value listing. ``status == 'error'`` prints
    the message in red.
    """
    if ctx.obj.get("json"):
        click.echo(_json.dumps(data, indent=2, ensure_ascii=False))
        return

    status = data.get("status", "")
    if status == "error":
        click.secho(
            f"Error: {data.get('message', data.get('stderr', 'unknown'))}", fg="red"
        )
        return

    for key, value in data.items():
        if key == "status":
            continue
        if isinstance(value, list):
            click.secho(f"{key} ({len(value)}):", fg="cyan", bold=True)
            for item in value:
                if isinstance(item, dict):
                    parts = [f"{k}={v}" for k, v in item.items()]
                    click.echo(f"  - {', '.join(parts)}")
                else:
                    click.echo(f"  - {item}")
        elif isinstance(value, dict):
            click.secho(f"{key}:", fg="cyan", bold=True)
            for k, v in value.items():
                click.echo(f"  {k}: {v}")
        else:
            click.echo(f"{key}: {value}")


def handle_error(func):
    """Decorator: catch RuntimeError from a command and format it via ``emit``.

    Exits with code 1 unless running inside the REPL (``ctx.obj['repl']``).
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RuntimeError as e:
            ctx = click.get_current_context()
            emit(ctx, {"status": "error", "message": str(e)})
            if not ctx.obj.get("repl"):
                sys.exit(1)

    return wrapper


# Backwards-compatible aliases (baseline names).
_out = emit
_handle_error = handle_error
