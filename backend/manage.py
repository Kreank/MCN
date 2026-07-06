#!/usr/bin/env python
"""Djangos Kommandozeilen-Einstiegspunkt."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django ist nicht installiert. Umgebung mit 'uv sync' aufsetzen und "
            "Befehle mit 'uv run python manage.py ...' ausführen."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
