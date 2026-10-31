"""Deterministic, display-only virtual science and planning adapters.

All inputs are the already validated, operator-controlled ActivityBridge v1 state.
There are no new write operations, side effects, Twitch events, subprocesses,
external apps, scientific claims or changes to the mission evidence schema.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .activity_bridge import validate_state

ADAPTER_SCHEMA = 'aegisforge.live_forge.virtual_activity_adapter.v1'

# Named, fictional presets; these are not calibrated biological measurements.
_EXPERIMENTS = (
    ('Luz de jardín ficticio', 'luz', (2, 2), (3, 2)),
    ('Agua de jardín ficticio', 'agua', (2, 2), (2, 3)),
    ('Ambiente de jardín ficticio', 'balance', (3, 1), (2, 2)),
    ('Restricción de jardín ficticio', 'restricción', (1, 2), (2, 3)),
)
_TASKS = (
    ('Organizar componentes', 'Definir responsabilidades en una aplicación imaginaria'),
    ('Especificar interfaz', 'Describir entradas y salidas de una pantalla de ejemplo'),
    ('Revisar criterios', 'Enumerar condiciones de una revisión conceptual'),
    ('Preparar entrega', 'Redactar una lista ilustrativa de verificación'),
)


def _hash(world: dict[str, Any]) -> str:
    return sha256(json.dumps(world, sort_keys=True, ensure_ascii=True,
                             allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _toy_index(light: int, water: int, constraint: bool) -> int:
    """Arbitrary bounded teaching function, NOT a natural-science prediction."""
    return max(0, min(100, 20 + 8 * light + 6 * water
                       - 3 * abs(light - water) - (5 if constraint else 0)))


def science_adapter(state: dict[str, Any]) -> dict[str, Any]:
    world = validate_state(state)
    if world['activity'] != 'science':
        raise ValueError('science_world_required')
    rows = []
    for index in range(world['tasks']):
        name, variable, base, alternative = _EXPERIMENTS[index % len(_EXPERIMENTS)]
        first = _toy_index(*base, world['constraint'])
        second = _toy_index(*alternative, world['constraint'])
        selected = second if world['mode'] == 'alternative' else first
        rows.append({
            'number': index + 1,
            'title': name,
            'variable': variable,
            'baseline': {'light': base[0], 'water': base[1], 'index': first},
            'alternative': {'light': alternative[0], 'water': alternative[1], 'index': second},
            'selected_index': selected,
            'difference': second - first,
            'stage': 'documentada' if index < world['milestones'] else 'pendiente',
        })
    return {
        'schema': ADAPTER_SCHEMA, 'activity': 'science',
        'revision': world['revision'], 'state_sha256': _hash(world),
        'scope': 'toy_model_only', 'model': '20 + 8·luz + 6·agua − 3·|luz−agua|; restricción −5',
        'indices_are_real_measurements': False, 'mode': world['mode'],
        'experiments': rows, 'documented_count': min(world['milestones'], world['tasks']),
        'unassigned_milestones': max(0, world['milestones'] - world['tasks']),
        'insights_requested': world['insights'],
        'note': 'Índices arbitrarios de 0 a 100; no son mediciones ni predicciones científicas.',
    }


def coding_adapter(state: dict[str, Any]) -> dict[str, Any]:
    world = validate_state(state)
    if world['activity'] != 'coding':
        raise ValueError('coding_world_required')
    tasks = []
    for index in range(world['tasks']):
        title, description = _TASKS[index % len(_TASKS)]
        tasks.append({
            'number': index + 1, 'title': title, 'description': description,
            'stage': 'revisión conceptual completada' if index < world['milestones'] else 'pendiente',
            'role': 'colaborativo' if world['roles'] else 'individual',
            'constraint': bool(world['constraint']),
        })
    return {
        'schema': ADAPTER_SCHEMA, 'activity': 'coding',
        'revision': world['revision'], 'state_sha256': _hash(world),
        'scope': 'fictional_project_board_only', 'tasks': tasks,
        'reviewed_count': min(world['tasks'], world['milestones']),
        'pending_count': max(0, world['tasks'] - world['milestones']),
        'unassigned_milestones': max(0, world['milestones'] - world['tasks']),
        'note': 'Tablero ilustrativo; no lee, edita ni ejecuta repositorios reales.',
    }


def render_virtual_activities(worlds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {'science': science_adapter(worlds['science']),
            'coding': coding_adapter(worlds['coding'])}
