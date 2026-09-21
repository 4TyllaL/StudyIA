"""Repeticao espacada no estilo SM-2 (Anki), com passos de aprendizado.

Notas de grade:
    0 = Errei   (again)     -> volta em minutos
    1 = Dificil (hard)
    2 = Bom     (good)
    3 = Facil   (easy)
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DAY = 1440.0  # minutos em um dia

LEARNING_STEPS = [1.0, 10.0]     # cartao novo: 1 min -> 10 min -> formado
RELEARN_STEPS = [10.0]           # apos errar um cartao ja formado
GRADUATING_INTERVAL = 1 * DAY    # primeiro intervalo apos formar
EASY_INTERVAL = 4 * DAY          # formar direto com "Facil"
MIN_EASE = 1.3
MAX_EASE = 3.2
MAX_INTERVAL = 365 * DAY
FUZZ = 0.05                      # +-5% para nao acumular cartoes no mesmo dia

GRADE_LABELS = {0: "Errei", 1: "Dificil", 2: "Bom", 3: "Facil"}


@dataclass
class SchedState:
    state: str = "new"
    step: int = 0
    ease: float = 2.5
    interval_min: float = 0.0
    reps: int = 0
    lapses: int = 0

    @classmethod
    def from_row(cls, row) -> "SchedState":
        """Aceita qualquer linha que traga as colunas de `schedule`."""
        if row is None:
            return cls()
        base = cls()
        keys = row.keys()
        get = lambda k, d: row[k] if k in keys and row[k] is not None else d
        return cls(
            state=get("state", base.state),
            step=get("step", base.step),
            ease=get("ease", base.ease),
            interval_min=get("interval_min", base.interval_min),
            reps=get("reps", base.reps),
            lapses=get("lapses", base.lapses),
        )


def _clamp_ease(ease: float) -> float:
    return max(MIN_EASE, min(MAX_EASE, ease))


def _fuzz(minutes: float) -> float:
    """Espalha intervalos longos; intervalos curtos (aprendizado) ficam exatos."""
    if minutes < DAY:
        return minutes
    delta = minutes * FUZZ
    return max(DAY, minutes + random.uniform(-delta, delta))


def apply_grade(s: SchedState, grade: int) -> SchedState:
    """Retorna o novo estado do agendamento para a nota informada."""
    state, step, ease = s.state, s.step, s.ease
    interval, reps, lapses = s.interval_min, s.reps + 1, s.lapses

    if state in ("new", "learning", "relearning"):
        steps = RELEARN_STEPS if state == "relearning" else LEARNING_STEPS
        if state == "new":
            state = "learning"

        if grade == 0:
            step = 0
            interval = steps[0]
        elif grade == 1:
            interval = steps[min(step, len(steps) - 1)]
        elif grade == 2:
            step += 1
            if step >= len(steps):
                state, step = "review", 0
                interval = GRADUATING_INTERVAL
            else:
                interval = steps[step]
        else:  # facil: forma na hora
            state, step = "review", 0
            interval = EASY_INTERVAL
    else:  # review
        if grade == 0:
            lapses += 1
            ease = _clamp_ease(ease - 0.20)
            state, step = "relearning", 0
            interval = RELEARN_STEPS[0]
        elif grade == 1:
            ease = _clamp_ease(ease - 0.15)
            interval = max(interval * 1.2, interval + DAY * 0.5, DAY)
        elif grade == 2:
            interval = max(interval * ease, interval + DAY)
        else:
            ease = _clamp_ease(ease + 0.15)
            interval = max(interval * ease * 1.3, interval + 2 * DAY)

    interval = min(_fuzz(interval), MAX_INTERVAL)
    return SchedState(
        state=state, step=step, ease=ease,
        interval_min=interval, reps=reps, lapses=lapses,
    )


def due_from(interval_min: float, base: datetime | None = None) -> str:
    base = base or datetime.now(timezone.utc)
    return (base + timedelta(minutes=interval_min)).isoformat(timespec="seconds")


def preview(s: SchedState) -> dict[str, str]:
    """Quanto tempo cada botao adia o cartao ('Errei' -> '10 min', etc.)."""
    return {str(g): humanize(apply_grade(s, g).interval_min) for g in (0, 1, 2, 3)}


def humanize(minutes: float) -> str:
    if minutes < 1:
        return "menos de 1 min"
    if minutes < 60:
        return f"{round(minutes)} min"
    if minutes < DAY:
        hours = minutes / 60
        return f"{hours:.0f} h" if hours >= 2 else "1 h"
    days = minutes / DAY
    if days < 30:
        return f"{days:.0f} dia" if round(days) == 1 else f"{days:.0f} dias"
    if days < 365:
        months = days / 30
        return f"{months:.0f} mes" if round(months) == 1 else f"{months:.1f} meses"
    return f"{days / 365:.1f} anos"
