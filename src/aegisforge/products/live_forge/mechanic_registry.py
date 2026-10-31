"""Immutable allowlisted cross-category stream mechanics (overlay-only)."""
from __future__ import annotations

from typing import Any

ACTIVITIES = ("gaming", "art", "coding", "science", "music", "sports", "talk")

# These packs borrow *patterns* (quests, palettes, progress) from other activities.
# They DO NOT modify game files, creative tools, playback, sports, OBS, or Twitch.
PACKS: dict[str, dict[str, Any]] = {
    "rpg_quest": {
        "name": "Misión comunitaria",
        "origin": "RPG / videojuegos",
        "description": "Aplica un objetivo opcional inspirado en misiones RPG.",
        "hints": {
            "gaming": "Desafío virtual: completa un objetivo opcional en el juego.",
            "art": "Misión creativa: incorpora un detalle nuevo a tu obra.",
            "coding": "Misión de código: explica o prueba una función en un entorno aislado.",
            "science": "Misión científica: compara dos hipótesis en una simulación.",
            "music": "Misión musical: explica una elección creativa de una pieza autorizada.",
            "sports": "Misión deportiva: analiza una jugada o estadística, sin apuestas.",
            "talk": "Misión de charla: desarrolla una pregunta elegida por la comunidad.",
        },
    },
    "creative_palette": {
        "name": "Cambio de paleta visual",
        "origin": "Arte digital",
        "description": "Superpone una temática cromática sin modificar la aplicación del creador.",
        "hints": {k: "Tema visual para el overlay: turquesa y violeta." for k in ACTIVITIES},
    },
    "progress_goal": {
        "name": "Objetivo de progreso",
        "origin": "Juegos de progreso",
        "description": "Superpone un marcador de objetivo comunitario sin efectos externos.",
        "hints": {k: "Nuevo hito virtual: completa una etapa de la actividad." for k in ACTIVITIES},
    },

    "story_branch": {
        "name": "Historia ramificada",
        "origin": "Narrativa interactiva",
        "description": "Invita a elegir una bifurcación narrativa; no controla otras aplicaciones.",
        "hints": {
            "gaming": "Historia: elige una ruta ficticia para la próxima escena del juego.",
            "art": "Historia: incorpora a tu obra un giro narrativo propuesto en el directo.",
            "coding": "Historia: presenta dos posibles recorridos de una interfaz de ejemplo.",
            "science": "Historia: explora dos resultados posibles de una simulación educativa.",
            "music": "Historia: describe el ambiente narrativo de una obra autorizada.",
            "sports": "Historia: analiza una decisión táctica hipotética, sin apuestas.",
            "talk": "Historia: la comunidad elige el siguiente tema de una narración ficticia.",
        },
    },
    "timed_challenge": {
        "name": "Reto con temporizador",
        "origin": "Speedruns / retos",
        "description": "Propone un objetivo creativo opcional, sin forzar ejercicios físicos.",
        "hints": {
            "gaming": "Reto opcional: intenta resolver un objetivo virtual en la siguiente ronda.",
            "art": "Reto opcional: crea un boceto breve con un límite de tiempo acordado.",
            "coding": "Reto opcional: explica una pequeña función de ejemplo en tiempo acotado.",
            "science": "Reto opcional: plantea una hipótesis y di cómo podría comprobarse.",
            "music": "Reto opcional: comenta un patrón rítmico de material autorizado.",
            "sports": "Reto opcional: responde una trivia deportiva breve, sin actividad física.",
            "talk": "Reto opcional: resume un tema en una explicación breve.",
        },
    },
    "science_hypothesis": {
        "name": "Duelo de hipótesis",
        "origin": "Ciencia / simulaciones",
        "description": "Contrasta hipótesis en un entorno conceptual, sin experimentos físicos.",
        "hints": {
            "gaming": "Hipótesis: predice qué estrategia virtual dará un mejor resultado.",
            "art": "Hipótesis: compara qué técnica visual comunica mejor una idea.",
            "coding": "Hipótesis: compara dos enfoques mediante una prueba de ejemplo aislada.",
            "science": "Hipótesis: formula dos predicciones para una simulación sin efectos reales.",
            "music": "Hipótesis: analiza cómo cambia una percepción musical según el arreglo autorizado.",
            "sports": "Hipótesis: contrasta dos explicaciones de una estadística deportiva.",
            "talk": "Hipótesis: enumera evidencia que permitiría distinguir dos afirmaciones.",
        },
    },
    "code_review": {
        "name": "Revisión colaborativa",
        "origin": "Ingeniería de software",
        "description": "Muestra una propuesta de revisión; jamás ejecuta código del chat.",
        "hints": {
            "gaming": "Revisión: analiza una regla del sistema del juego, sin modificar archivos.",
            "art": "Revisión: comenta una mejora posible en el flujo creativo.",
            "coding": "Revisión: identifica una mejora para un fragmento autorizado y aislado.",
            "science": "Revisión: examina la claridad de una simulación o visualización.",
            "music": "Revisión: examina la organización de una sesión musical autorizada.",
            "sports": "Revisión: describe cómo verificar una estadística antes de mostrarla.",
            "talk": "Revisión: evalúa si un argumento está bien explicado y documentado.",
        },
    },
    "rhythm_overlay": {
        "name": "Pulso visual",
        "origin": "Música / visualización",
        "description": "Indicador visual de ritmo; no genera ni reproduce música.",
        "hints": {
            "gaming": "Pulso visual: marca un ritmo gráfico para la siguiente escena virtual.",
            "art": "Pulso visual: propone una secuencia de formas para el overlay creativo.",
            "coding": "Pulso visual: muestra un indicador de etapas de una demostración.",
            "science": "Pulso visual: destaca visualmente los pasos de un proceso simulado.",
            "music": "Pulso visual: acompaña una obra autorizada sin modificar su audio.",
            "sports": "Pulso visual: resalta hitos informativos sin cambiar la competición.",
            "talk": "Pulso visual: señala los segmentos de una conversación.",
        },
    },
    "community_roles": {
        "name": "Roles comunitarios",
        "origin": "Juegos cooperativos",
        "description": "Sugiere roles simbólicos, sin dar permisos de moderación.",
        "hints": {
            "gaming": "Roles: la audiencia elige un consejero virtual para una misión.",
            "art": "Roles: una persona propone el tema y otra una restricción visual.",
            "coding": "Roles: distingue entre quien propone una idea y quien la revisa.",
            "science": "Roles: reparte funciones ficticias de observación y análisis.",
            "music": "Roles: separa las propuestas de ambiente y de visualización autorizada.",
            "sports": "Roles: asigna papeles simbólicos de comentarista y analista.",
            "talk": "Roles: la comunidad propone papeles de moderador ficticio y relator.",
        },
    },
    "stat_spotlight": {
        "name": "Dato destacado",
        "origin": "Analítica / deportes",
        "description": "Superpone una indicación para destacar una métrica informativa.",
        "hints": {
            "gaming": "Dato destacado: comenta una estadística visible del juego.",
            "art": "Dato destacado: explica una medida descriptiva del proceso artístico.",
            "coding": "Dato destacado: describe una métrica de rendimiento sin prometer mejoras.",
            "science": "Dato destacado: identifica una medida verificable de la simulación.",
            "music": "Dato destacado: comenta una propiedad de una obra autorizada.",
            "sports": "Dato destacado: explica una estadística deportiva sin apuestas.",
            "talk": "Dato destacado: presenta un dato con su fuente y contexto.",
        },
    },
    "creative_constraint": {
        "name": "Restricción creativa",
        "origin": "Diseño / arte",
        "description": "Sugiere una regla voluntaria de creatividad, solo en el overlay.",
        "hints": {
            "gaming": "Restricción creativa: prueba una estrategia virtual diferente, si es segura.",
            "art": "Restricción creativa: utiliza solo dos formas durante una composición.",
            "coding": "Restricción creativa: presenta una solución simple sin ejecutarla en vivo.",
            "science": "Restricción creativa: explica una simulación con un único gráfico.",
            "music": "Restricción creativa: describe una idea con dos motivos autorizados.",
            "sports": "Restricción creativa: analiza una jugada usando solo datos públicos.",
            "talk": "Restricción creativa: cuenta una historia usando tres conceptos propuestos.",
        },
    },
    "quest_palette_mix": {
        "name": "Mashup: misión + paleta",
        "origin": "RPG + arte digital",
        "description": "Combina una misión de otra categoría con una paleta en pantalla.",
        "hints": {k: "Combinación: " + v + " Tema turquesa/violeta." for k, v in {
            "gaming": "completa un reto opcional en el juego.",
            "art": "dibuja un detalle nuevo.",
            "coding": "explica una solución en un ejemplo aislado.",
            "science": "contrasta dos resultados simulados.",
            "music": "comenta una decisión de composición autorizada.",
            "sports": "explica una estadística deportiva.",
            "talk": "responde una pregunta comunitaria.",
        }.items()},
    },
}


def public_catalog() -> list[dict[str, str]]:
    """Public and entirely static; never load executable packs from chat."""
    return [{"id": name, "name": data["name"], "origin": data["origin"],
             "description": data["description"], "effect_type": "overlay_only"}
            for name, data in PACKS.items()]


def render_pack(pack_id: str, activity: str) -> dict[str, str]:
    if pack_id not in PACKS or activity not in ACTIVITIES:
        raise ValueError("unknown_mechanic_or_activity")
    pack = PACKS[pack_id]
    return {"id": pack_id, "name": pack["name"], "origin": pack["origin"],
            "activity": activity, "message": pack["hints"][activity],
            "effect_type": "overlay_only"}
