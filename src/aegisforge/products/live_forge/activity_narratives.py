"""Public, bounded explanations of local virtual transitions.

This module does not alter the established transition/evidence schemas or infer
Twitch authenticity. No viewer data, arbitrary content, tools or generated code.
"""
from __future__ import annotations

from copy import deepcopy

from .activity_contracts import BRIDGE_ACTIVITIES, CATEGORY_LABELS
from typing import Any

SHAPES = {
    "star": "Estrella", "circle": "Círculo", "diamond": "Diamante",
    "leaf": "Flor", "spark": "Destello", "moon": "Luna",
    "sun": "Sol", "heart": "Corazón",
}
THEMES = {"teal": "turquesa", "violet": "violeta", "amber": "ámbar", "blue": "azul"}
SETTINGS = {"garden": "Jardín creativo", "night_sky": "Cielo de posibilidades"}
SCIENCE_IDEAS = (
    ("Luz y crecimiento virtual", "Comparar dos niveles de luz en una planta simulada"),
    ("Consumo de energía conceptual", "Comparar dos perfiles hipotéticos de consumo"),
    ("Temperatura ilustrativa", "Plantear cómo cambia una variable al modificar la temperatura"),
    ("Agua en un ecosistema ficticio", "Comparar dos condiciones de disponibilidad de agua"),
)
CODING_IDEAS = (
    ("Organizar componentes", "Definir responsabilidades de una interfaz ficticia"),
    ("Planificar una revisión", "Revisar una lista ilustrativa de requisitos"),
    ("Describir una mejora", "Plantear un cambio de diseño en un proyecto inventado"),
    ("Preparar una comprobación", "Definir criterios para una prueba conceptual"),
)


def _note(revision: int, part: str, kind: str, title: str, detail: str) -> dict[str, Any]:
    return {"revision": revision, "mechanic": part, "kind": kind,
            "title": title, "description": detail,
            "source": "explicit_local_operator_action", "external_actions": False}


def transition_notes(transition: dict[str, Any], activity: str) -> list[dict[str, Any]]:
    """Describe an already validated transition, without changing its semantics."""
    before, after = transition["before"], transition["after"]
    rev = after["revision"]
    if transition["pack_id"] in ("studio.reset", "bridge.reset"):
        return [_note(rev, "reset", "reset", "Actividad reiniciada",
                      "Se restauró el estado inicial local. El archivo de evidencias es independiente.")]
    result: list[dict[str, Any]] = []
    added_tasks = added_milestones = added_insights = added_shapes = 0
    for part in transition["parts"]:
        if activity in BRIDGE_ACTIVITIES:
            if part in ("science_hypothesis", "code_review", "rpg_quest"):
                added_tasks += 1
                number = before["tasks"] + added_tasks
                if activity == "science":
                    title, description = SCIENCE_IDEAS[(number - 1) % len(SCIENCE_IDEAS)]
                    result.append(_note(rev, part, "hypothesis", f"Hipótesis virtual {number}: {title}",
                        f"{description}. Sin experimentos físicos, mediciones ni resultados científicos verificados."))
                elif activity == "coding":
                    title, description = CODING_IDEAS[(number - 1) % len(CODING_IDEAS)]
                    result.append(_note(rev, part, "task", f"Tarea ilustrativa {number}: {title}",
                        f"{description}. No se leyó, modificó ni ejecutó un repositorio real."))
                else:
                    label = CATEGORY_LABELS[activity][1]
                    result.append(_note(rev, part, "task", f"{label} {number}",
                        "Nueva entrada virtual inspirada en la mecánica; no se modificó la actividad real."))
            elif part == "progress_goal":
                added_milestones += 1
                n = before["milestones"] + added_milestones
                result.append(_note(rev, part, "milestone", f"Hito virtual {n}",
                    "Se alcanzó una etapa de la actividad conceptual, sin certificar trabajo externo."))
            elif part == "stat_spotlight":
                added_insights += 1
                n = before["insights"] + added_insights
                result.append(_note(rev, part, "insight", f"Dato ilustrativo {n}",
                    "Se destacó una observación descriptiva; no representa una medición real."))
        elif activity == "art":
            if part == "rpg_quest":
                added_shapes += 1
                element = after["elements"][len(before["elements"]) + added_shapes - 1]
                result.append(_note(rev, part, "object",
                    f"Objeto {element['id']}: {SHAPES[element['shape']]}",
                    "Se agregó una figura al lienzo virtual, sin modificar aplicaciones de dibujo externas."))
            elif part == "progress_goal":
                added_milestones += 1
                n = before["progress"] + added_milestones
                result.append(_note(rev, part, "milestone", f"Objetivo creativo {n}",
                    "Se completó una etapa simbólica del proyecto artístico virtual."))
        if part == "story_branch":
            setting = (SETTINGS.get(after["setting"], "escenario") if activity == "art"
                       else "modo " + after["mode"])
            result.append(_note(rev, part, "mode", "Cambio de escenario o modo", f"Nuevo estado: {setting}."))
        elif part == "creative_palette":
            result.append(_note(rev, part, "theme", "Paleta modificada",
                "Nuevo tema visual: " + THEMES.get(after["palette"] if activity == "art" else after["theme"], "predeterminado") + "."))
        elif part in ("timed_challenge", "community_roles", "creative_constraint"):
            field = {"timed_challenge": "challenge", "community_roles": "roles",
                     "creative_constraint": "constraint"}[part]
            title = {"timed_challenge": "Reto opcional", "community_roles": "Roles comunitarios",
                     "creative_constraint": "Restricción creativa"}[part]
            result.append(_note(rev, part, "toggle", title,
                                "Activado en la actividad virtual." if after[field] else "Desactivado en la actividad virtual."))
    return result


class ActivityNarratives:
    """Short memory-only history, segregated by activity.

    Committed transitions are already verified and archived by the caller.
    The historian never stores a token, voter identity or raw chat message.
    """
    def __init__(self) -> None:
        self._entries: dict[str, list[dict[str, Any]]] = {x: [] for x in ("art", *BRIDGE_ACTIVITIES)}

    def capture(self, transition: dict[str, Any], activity: str) -> None:
        rows = transition_notes(transition, activity)
        self._entries[activity].extend(rows)
        del self._entries[activity][:-96]

    def _latest(self, activity: str) -> list[dict[str, Any]]:
        return deepcopy(self._entries[activity][-10:][::-1])

    def bridge(self, states: dict[str, dict[str, Any]]) -> dict[str, Any]:
        result = {}
        for activity in BRIDGE_ACTIVITIES:
            world = states[activity]
            history = self._entries[activity]
            # Keep only entries since the most recent reset for the active view.
            last_reset = max((i for i, e in enumerate(history) if e["kind"] == "reset"), default=-1)
            active = history[last_reset + 1:]
            cards = [deepcopy(e) for e in active if e["kind"] in ("hypothesis", "task", "milestone", "insight")]
            result[activity] = {
                "activity": activity, "revision": world["revision"],
                "cards": cards[-72:], "history": self._latest(activity),
                "note": "Ejemplos conceptuales creados localmente; no son hechos, mediciones ni cambios externos.",
                "history_persistence": "memory_only",
            }
        return result

    def studio(self, scene: dict[str, Any]) -> dict[str, Any]:
        objects = [{"id": e["id"], "shape": e["shape"],
                    "name": SHAPES[e["shape"]], "palette": THEMES[scene["palette"]],
                    "setting": SETTINGS[scene["setting"]]} for e in scene["elements"]]
        objectives = [{"number": i, "title": f"Objetivo creativo {i}",
                       "description": "Etapa simbólica completada en el estudio virtual."}
                      for i in range(1, scene["progress"] + 1)]
        return {"objects": objects, "objectives": objectives,
                "history": self._latest("art"), "history_persistence": "memory_only",
                "description": "Propiedades y objetivos ilustrativos; no hay edición de software externo."}
