"""Modellzugang aus einer lokalen Datei ``.env`` statt aus ``export`` im Terminal.

Die Datei liegt im Wurzelverzeichnis des Repositorys und ist per ``.gitignore`` von Git
ausgenommen. Gelesen werden nur Zeilen ``NAME=WERT`` fuer die Variablen des Labors; bereits
gesetzte Umgebungsvariablen haben Vorrang.
"""

from __future__ import annotations

import os
from pathlib import Path

PREFIXES = ("AUTONOMY_RECOVERY_", "CHATAI_", "SAIA_")


def load_env_file(path: Path | None = None) -> list[str]:
    """Setzt fehlende Variablen aus ``path`` (Standard: ``.env`` im aktuellen Verzeichnis)."""
    if path is None and os.environ.get("AUTONOMY_RECOVERY_DOTENV") == "0":
        return []  # Tests laufen ohne Modellzugang aus .env
    path = path or Path.cwd() / ".env"
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip().strip("\"'")
        if name.startswith(PREFIXES) and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded
