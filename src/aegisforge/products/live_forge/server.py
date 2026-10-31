"""Live Forge private operator console and local read-only stream overlay.

Run: python -m aegisforge.products.live_forge.server
Twitch is read-only; only a local authenticated operator can open/close rounds.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .engine import LiveVoteEngine
from .effect_dispatcher import OverlayEffectDispatcher
from .creative_studio import CreativeStudio
from .activity_bridge import ActivityBridge, ACTIVITIES as BRIDGE_ACTIVITIES
from .activity_narratives import ActivityNarratives
from .virtual_activity_adapter import render_virtual_activities
from .mechanic_registry import ACTIVITIES, public_catalog
from .activity_contracts import public_contracts, virtual_compatible
from .mechanic_composer import public_compositions, preview_composition
from .policy_engine import PolicyEngine
from .twitch_eventsub import TwitchConfig, TwitchEventSubBridge
from .evidence_bridge import LiveForgeEvidenceBridge
from .codex_bridge import CodexEngineeringBridge

ASSETS = Path(__file__).resolve().parents[2] / 'web' / 'live_forge'
engine = LiveVoteEngine()
policy = PolicyEngine()
effects = OverlayEffectDispatcher()
studio = CreativeStudio()
activity_bridge = ActivityBridge()
narratives = ActivityNarratives()
control_lock = RLock()
# Explicitly separate history from AegisGrid; no on-disk writes by default.
evidence = LiveForgeEvidenceBridge(os.getenv("LIVE_FORGE_CHECKPOINT_DIR"))
codex_engineering = CodexEngineeringBridge(Path(__file__).resolve().parent)
operator_token = os.getenv("LIVE_FORGE_OPERATOR_TOKEN") or secrets.token_urlsafe(32)
if len(operator_token) < 24:
    raise ValueError("LIVE_FORGE_OPERATOR_TOKEN must contain at least 24 characters")
config_error: str | None = None
try:
    # Explicit offline mode never reads Twitch environment credentials.
    config = None if os.getenv('LIVE_FORGE_OFFLINE') == '1' else TwitchConfig.from_env()
except ValueError as exc:
    config, config_error = None, str(exc)
bridge = TwitchEventSubBridge(engine, config) if config is not None else None


@asynccontextmanager
async def lifespan(_app: Starlette):
    task = asyncio.create_task(bridge.run()) if bridge is not None else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def index(_):
    return Response((ASSETS / 'index.html').read_bytes(), media_type='text/html')


async def overlay(_):
    return Response((ASSETS / 'overlay.html').read_bytes(), media_type='text/html')


async def studio_page(_):
    return Response((ASSETS / 'studio.html').read_bytes(), media_type='text/html')


async def studio_script(_):
    return Response((ASSETS / 'studio.js').read_bytes(), media_type='text/javascript')


async def bridge_page(_):
    return Response((ASSETS / 'bridge.html').read_bytes(), media_type='text/html')


async def bridge_script(_):
    return Response((ASSETS / 'bridge.js').read_bytes(), media_type='text/javascript')


async def bridge_state(_):
    with control_lock:
        worlds = activity_bridge.snapshot()
        details = narratives.bridge(worlds)
        functional = render_virtual_activities(worlds)
    return JSONResponse({'worlds': worlds, 'details': details, 'functional': functional,
                         'supported_activities': list(BRIDGE_ACTIVITIES),
                         'contracts': public_contracts(),
                         'write_access': 'operator_only', 'external_actions': False,
                         'persistence': 'memory_only'}, headers={'Cache-Control': 'no-store'})


async def studio_state(_):
    with control_lock:
        current = studio.snapshot()
        details = narratives.studio(current)
    return JSONResponse({'scene': current, 'details': details,
                         'write_access': 'operator_only',
                         'persistence': 'memory_only', 'external_actions': False},
                        headers={'Cache-Control': 'no-store'})


async def asset(_):
    return Response((ASSETS / 'app.js').read_bytes(), media_type='text/javascript')


async def overlay_asset(_):
    return Response((ASSETS / 'overlay.js').read_bytes(), media_type='text/javascript')


async def state(_):
    with control_lock:
        snapshot = engine.snapshot()
        p = policy.active.public()
        effect = effects.snapshot()
    return JSONResponse({**snapshot,
        'connection': bridge.status if bridge else 'not_configured',
        'live_connected': bool(bridge and bridge.connected),
        'last_issue_code': bridge.last_issue if bridge else config_error,
        'eventsub_diagnostics': bridge.public_diagnostics() if bridge else None,
        'source': 'Twitch EventSub channel.chat.message only',
        'offline_mode': os.getenv('LIVE_FORGE_OFFLINE') == '1',
        'policy': p,
        'studio_cooldown_remaining_seconds': policy.cooldown_remaining_seconds(),
        'effect': effect,
        'writes_to_twitch': False,
        'external_stream_actions': False,
        'latest_evidence': evidence.last_summary(),
        'evidence_issue': evidence.storage_issue,
        'evidence_mode': 'persistent' if os.getenv('LIVE_FORGE_CHECKPOINT_DIR') else 'volatile',
    }, headers={'Cache-Control': 'no-store'})


async def catalog(_):
    return JSONResponse({'activities': ACTIVITIES, 'mechanics': public_catalog(),
                         'compositions': public_compositions(),
                         'contracts': public_contracts(),
                         'effect_scope': 'operator_selected_overlay_or_local_virtual_activity'})


def _authorize(request) -> bool:
    header = request.headers.get('authorization', '')
    provided = header[7:] if header.startswith('Bearer ') else ''
    # Browser POSTs must originate from this local UI; token also required.
    origin = request.headers.get('origin')
    if origin is not None and origin not in {'http://127.0.0.1:8013',
                                               'http://localhost:8013'}:
        return False
    return hmac.compare_digest(provided.encode('utf-8'), operator_token.encode('utf-8'))


async def operator_session(request):
    # Read-only credential check; never returns or stores the token.
    if not _authorize(request):
        return JSONResponse({'error': 'operator_auth_required'}, status_code=403,
                            headers={'Cache-Control': 'no-store'})
    return JSONResponse({'operator_authenticated': True},
                        headers={'Cache-Control': 'no-store'})




async def codex_status(request):
    # Codex prompts and results are private operator-only engineering material.
    if not _authorize(request):
        return JSONResponse({'error': 'operator_auth_required'}, status_code=403)
    return JSONResponse(codex_engineering.status(), headers={'Cache-Control': 'no-store'})


async def codex_start(request):
    if not _authorize(request):
        return JSONResponse({'error': 'operator_auth_required'}, status_code=403)
    if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        return JSONResponse({'error': 'json_required'}, status_code=415)
    try:
        size = int(request.headers.get('content-length', '0') or '0')
        if size > 512:
            return JSONResponse({'error': 'body_too_large'}, status_code=413)
        data = await request.json()
        if type(data) is not dict or set(data) != {'topic', 'activity', 'consent'} or data['consent'] is not True:
            raise ValueError('explicit_operator_consent_required')
        return JSONResponse(codex_engineering.start(topic=data['topic'], activity=data['activity']),
                            status_code=202, headers={'Cache-Control': 'no-store'})
    except (ValueError, TypeError, KeyError) as exc:
        return JSONResponse({'error': str(exc)[:100]}, status_code=400)

async def evidence_export(request):
    if not _authorize(request):
        return JSONResponse({'error': 'operator_auth_required'}, status_code=403)
    export = evidence.latest_export()
    if export is None:
        return JSONResponse({'error': 'no_evidence_this_session'}, status_code=404)
    return JSONResponse(export, headers={'Cache-Control': 'no-store'})


class EvidenceUnavailable(RuntimeError):
    pass


def _record_decision(operation, votes, *, effect=None, studio_transition=None,
                     activity_transition=None):
    try:
        return evidence.record(
            operation=operation, votes=votes, policy=policy.active.public(),
            effect=effect, blocked_reason=None,
            live_connected=bool(bridge and bridge.connected),
            studio_transition=studio_transition, activity_transition=activity_transition)
    except Exception as exc:
        raise EvidenceUnavailable('record_or_archive_unavailable') from exc


def _close_round():
    closed = engine.close_round(commit=False)
    recorded = _record_decision('round_closed', closed)
    return {'round': engine.close_round(), 'effect': None, 'evidence': recorded}


def _apply_winner(round_id, target):
    votes = engine.snapshot()
    if type(round_id) is not int or round_id != votes['round']:
        raise ValueError('stale_round')
    if votes['round_open'] or votes['resolution'] != 'pending_approval':
        raise ValueError('round_not_pending')
    if target not in ('overlay', 'activity'):
        raise ValueError('invalid_effect_target')
    selected = getattr(policy.active, 'option_' + votes['winner'].lower())
    allowed, reason = policy.apply_check(selected)
    if not allowed:
        raise ValueError(reason)
    activity = policy.active.activity
    scene_transition = world_transition = prepared_effect = None
    if target == 'activity':
        if not virtual_compatible(activity, selected):
            raise ValueError('mechanic_has_no_virtual_adapter')
        if activity == 'art':
            scene_transition = studio.preview(selected, activity)
        else:
            world_transition = activity_bridge.preview(selected, activity)
    else:
        prepared_effect = effects.prepare(selected, activity, policy.active.duration_seconds)
    recorded = _record_decision('round_applied', votes, effect=prepared_effect,
                                studio_transition=scene_transition,
                                activity_transition=world_transition)
    # All planning, policy and archive checks precede the only mutation boundary.
    result = {'effect': None, 'target': target, 'evidence': recorded}
    if scene_transition is not None:
        result['scene'] = studio.commit(scene_transition)
        narratives.capture(scene_transition, activity)
    elif world_transition is not None:
        result['world'] = activity_bridge.commit(world_transition)
        narratives.capture(world_transition, activity)
    else:
        result['effect'] = effects.commit(prepared_effect, policy.active.duration_seconds)
    policy.mark_effect()
    engine.resolve_round(round_id)
    result['round'] = engine.snapshot()
    return result


async def control(request):
    if not _authorize(request):
        return JSONResponse({'error': 'operator_auth_required'}, status_code=403)
    if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        return JSONResponse({'error': 'json_required'}, status_code=415)
    try:
        body_length = int(request.headers.get('content-length', '0') or 0)
    except ValueError:
        return JSONResponse({'error': 'invalid_content_length'}, status_code=400)
    if body_length > 4096:
        return JSONResponse({'error': 'body_too_large'}, status_code=413)
    try:
        data = await request.json()
        if type(data) is not dict or type(data.get('action')) is not str:
            raise ValueError('invalid_request')
        action = data['action']
        with control_lock:
            round_state = engine.snapshot()
            if (round_state['resolution'] == 'pending_approval' and
                    action in ('policy', 'start', 'preview', 'studio_apply', 'studio_reset',
                               'bridge_apply', 'bridge_reset')):
                raise ValueError('resolve_pending_round_first')
            if action == 'compose':
                if set(data) != {'action', 'first', 'second', 'activity'}:
                    raise ValueError('invalid_compose_request')
                result = {'composition': preview_composition(
                    data['first'], data['second'], data['activity'])}
            elif action == 'policy':
                if set(data) != {'action', 'policy'}:
                    raise ValueError('unexpected_parameters')
                if engine.snapshot()['round_open']:
                    raise ValueError('finish_round_before_changing_policy')
                result = {'policy': policy.configure(data.get('policy'))}
                effects.clear()
            elif action == 'start':
                if set(data) != {'action'}:
                    raise ValueError('unexpected_parameters')
                if not policy.active.enabled:
                    raise ValueError('policy_paused')
                result = {'round': engine.start_round()}
            elif action == 'close':
                if set(data) != {'action'}:
                    raise ValueError('unexpected_parameters')
                result = _close_round()
            elif action == 'apply_winner':
                if set(data) != {'action', 'round', 'target'}:
                    raise ValueError('unexpected_parameters')
                result = _apply_winner(data['round'], data['target'])
            elif action == 'finish':
                # Legacy close+approve overlay command, never reapplies a closed round.
                if set(data) != {'action'}:
                    raise ValueError('unexpected_parameters')
                result = _close_round()
                if result['round']['winner'] is not None:
                    result = _apply_winner(result['round']['round'], 'overlay')
            elif action == 'preview':
                if set(data) != {'action', 'mechanic_id'} or type(data.get('mechanic_id')) is not str:
                    raise ValueError('invalid_preview_request')
                if round_state['round_open']:
                    raise ValueError('close_round_before_preview')
                pack_id = data['mechanic_id']
                allowed, reason = policy.apply_check(pack_id)
                if not allowed:
                    raise ValueError(reason)
                prepared = effects.prepare(pack_id, policy.active.activity,
                                           policy.active.duration_seconds, preview=True)
                recorded = _record_decision('operator_preview', engine.snapshot(), effect=prepared)
                result = {'effect': effects.commit(prepared, policy.active.duration_seconds),
                          'evidence': recorded}
                policy.mark_effect()
            elif action == 'studio_apply':
                if set(data) != {'action', 'option'} or data['option'] not in ('A', 'B'):
                    raise ValueError('invalid_studio_action')
                if engine.snapshot()['round_open']:
                    raise ValueError('close_round_before_studio_change')
                selected = getattr(policy.active, 'option_' + data['option'].lower())
                allowed, reason = policy.apply_check(selected)
                if not allowed:
                    raise ValueError(reason)
                transition = studio.preview(selected, policy.active.activity)
                # Save the independently replayable operator event BEFORE changing
                # scene state, so a failed archive does not leave unrecorded changes.
                try:
                    recorded = evidence.record(
                        operation='studio_apply', votes=engine.snapshot(),
                        policy=policy.active.public(), effect=None, blocked_reason=None,
                        live_connected=bool(bridge and bridge.connected),
                        studio_transition=transition,
                    )
                except Exception:
                    return JSONResponse({'error': 'studio_record_or_archive_unavailable'},
                                        status_code=503)
                scene = studio.commit(transition)
                narratives.capture(transition, "art")
                policy.mark_effect()
                result = {'scene': scene, 'transition': {
                    'before_sha256': transition['before_sha256'],
                    'after_sha256': transition['after_sha256'],
                    'mechanic_id': selected,
                    'scope': transition['scope'],
                    'source': 'explicit_operator_action_no_twitch_vote'},
                    'evidence': recorded}
            elif action == 'bridge_apply':
                if set(data) != {'action', 'option'} or data['option'] not in ('A', 'B'):
                    raise ValueError('invalid_bridge_action')
                if engine.snapshot()['round_open']:
                    raise ValueError('close_round_before_bridge_change')
                if policy.active.activity not in BRIDGE_ACTIVITIES:
                    raise ValueError('bridge_activity_not_supported')
                selected = getattr(policy.active, 'option_' + data['option'].lower())
                allowed, reason = policy.apply_check(selected)
                if not allowed:
                    raise ValueError(reason)
                if not virtual_compatible(policy.active.activity, selected):
                    raise ValueError('bridge_mechanic_not_supported')
                transition = activity_bridge.preview(selected, policy.active.activity)
                try:
                    recorded = evidence.record(
                        operation='bridge_apply', votes=engine.snapshot(),
                        policy=policy.active.public(), effect=None, blocked_reason=None,
                        live_connected=bool(bridge and bridge.connected),
                        activity_transition=transition)
                except Exception:
                    return JSONResponse({'error': 'bridge_record_or_archive_unavailable'},
                                        status_code=503)
                world = activity_bridge.commit(transition)
                narratives.capture(transition, policy.active.activity)
                policy.mark_effect()
                result = {'world': world, 'transition': {
                    'mechanic_id': selected, 'before_sha256': transition['before_sha256'],
                    'after_sha256': transition['after_sha256'],
                    'scope': transition['scope'],
                    'source': 'explicit_operator_action_no_twitch_vote'}, 'evidence': recorded}
            elif action == 'bridge_reset':
                if set(data) != {'action'}:
                    raise ValueError('invalid_bridge_reset')
                if engine.snapshot()['round_open']:
                    raise ValueError('close_round_before_bridge_reset')
                if policy.active.activity not in BRIDGE_ACTIVITIES:
                    raise ValueError('bridge_activity_not_supported')
                transition = activity_bridge.preview_reset(policy.active.activity)
                try:
                    recorded = evidence.record(
                        operation='bridge_reset', votes=engine.snapshot(),
                        policy=policy.active.public(), effect=None, blocked_reason=None,
                        live_connected=bool(bridge and bridge.connected),
                        activity_transition=transition)
                except Exception:
                    return JSONResponse({'error': 'bridge_record_or_archive_unavailable'},
                                        status_code=503)
                world = activity_bridge.commit(transition)
                narratives.capture(transition, policy.active.activity)
                result = {'world': world, 'evidence': recorded,
                          'source': 'explicit_operator_reset_no_twitch_vote'}
            elif action == 'studio_reset':
                if set(data) != {'action'}:
                    raise ValueError('unexpected_parameters')
                if engine.snapshot()['round_open']:
                    raise ValueError('close_round_before_studio_reset')
                transition = studio.preview_reset()
                try:
                    recorded = evidence.record(
                        operation='studio_reset', votes=engine.snapshot(),
                        policy=policy.active.public(), effect=None, blocked_reason=None,
                        live_connected=bool(bridge and bridge.connected),
                        studio_transition=transition,
                    )
                except Exception:
                    return JSONResponse({'error': 'studio_record_or_archive_unavailable'},
                                        status_code=503)
                scene = studio.commit(transition)
                narratives.capture(transition, "art")
                result = {'scene': scene, 'evidence': recorded,
                          'source': 'explicit_operator_reset_no_twitch_vote'}
            elif action == 'stop':
                if set(data) != {'action'}:
                    raise ValueError('unexpected_parameters')
                if engine.snapshot()['round_open']:
                    engine.close_round()
                if engine.snapshot()['resolution'] == 'pending_approval':
                    engine.resolve_round(engine.snapshot()['round'], cancelled=True)
                effects.clear()
                policy.configure({**policy.active.public(), 'enabled': False})
                result = {'paused': True, 'effect': None,
                          'round': engine.snapshot()}
            else:
                raise ValueError('unknown_action')
            # Recorded only after the authorized operator action has completed.
            # No chats, OAuth tokens, browser text or Twitch IDs are copied.
            operation = {'stop': 'emergency_stop'}.get(action)
            if operation:
                votes = (result['round'] if action == 'finish' else engine.snapshot())
                # A stopped or preview-only action does not assert a round winner.
                try:
                    result['evidence'] = evidence.record(
                        operation=operation, votes=votes, policy=policy.active.public(),
                        effect=result.get('effect'), blocked_reason=result.get('blocked_reason'),
                        live_connected=bool(bridge and bridge.connected),
                    )
                except Exception:
                    # Evidence failure must not leave a newly generated overlay running.
                    effects.clear()
                    if action != 'stop':
                        result['effect'] = None
                    result['evidence_error'] = 'record_or_archive_unavailable'
    except EvidenceUnavailable:
        return JSONResponse({'error': 'record_or_archive_unavailable',
                             'activity_changed': False}, status_code=503)
    except (ValueError, TypeError, KeyError) as exc:
        return JSONResponse({'error': str(exc)[:100]}, status_code=400)
    return JSONResponse({'ok': True, **result}, headers={'Cache-Control': 'no-store'})


app = Starlette(debug=False, routes=[
    Route('/', index), Route('/overlay', overlay), Route('/studio', studio_page),
    Route('/bridge', bridge_page), Route('/bridge.js', bridge_script),
    Route('/api/bridge', bridge_state),
    Route('/app.js', asset), Route('/overlay.js', overlay_asset),
    Route('/studio.js', studio_script), Route('/api/studio', studio_state),
    Route('/api/state', state), Route('/api/mechanics', catalog),
    Route('/api/control', control, methods=['POST']),
    Route('/api/operator/session', operator_session, methods=['GET']),
    Route('/api/evidence/latest', evidence_export, methods=['GET']),
    Route('/api/codex/status', codex_status, methods=['GET']),
    Route('/api/codex/start', codex_start, methods=['POST']),
], lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]'])


async def security_headers(request, call_next):
    if not request.client:
        return JSONResponse({'error': 'loopback_only'}, status_code=403)
    try:
        if not ipaddress.ip_address(request.client.host).is_loopback:
            return JSONResponse({'error': 'loopback_only'}, status_code=403)
    except ValueError:
        return JSONResponse({'error': 'loopback_only'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response

app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)

if __name__ == '__main__':
    import uvicorn
    print('LIVE FORGE — token de operador local (no compartir):', operator_token, flush=True)
    uvicorn.run(app, host='127.0.0.1', port=8013, log_level='warning')
