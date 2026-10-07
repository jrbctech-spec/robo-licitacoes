"""
som_alerta.py – Utilitário multiplataforma para alertas sonoros em tempo real.

Emite sinais acústicos imediatos no sistema operacional quando ocorrem eventos críticos,
como perda de liderança ou iminência de encerramento de item na disputa.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def tocar_alerta_perda_lideranca() -> None:
    """
    Aciona alerta sonoro imediato no sistema operacional (Linux ou Windows).
    Não-bloqueante para evitar atrasos no ciclo de disputa.
    """
    try:
        if sys.platform.startswith("linux"):
            sons_linux = [
                "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
                "/usr/share/sounds/freedesktop/stereo/complete.oga",
                "/usr/share/sounds/freedesktop/stereo/bell.oga",
                "/usr/share/sounds/ubuntu/stereo/bell.ogg",
            ]
            som_encontrado = next((s for s in sons_linux if os.path.exists(s)), None)
            if som_encontrado:
                subprocess.Popen(
                    ["paplay", som_encontrado],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Fallback: campainha ASCII no terminal
                sys.stdout.write("\a\a\a")
                sys.stdout.flush()
        elif sys.platform == "win32":
            import winsound
            winsound.Beep(1200, 300)
            winsound.Beep(1500, 250)
        else:
            sys.stdout.write("\a\a\a")
            sys.stdout.flush()
    except Exception as e:
        logger.debug(f"[SOM] Aviso ao reproduzir som de alerta: {e}")
