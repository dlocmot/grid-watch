"""Carga y guardado atómico del estado en JSON."""
from __future__ import annotations

import json
import os
import tempfile

from .models import State


def load(path: str) -> State:
    """Estado guardado, o uno nuevo si no existe o no se puede interpretar."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return State.from_dict(json.load(fh))
    except (OSError, ValueError, KeyError):
        return State()


def save(path: str, state: State) -> None:
    """Escritura atómica: un corte a media escritura no deja basura."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
