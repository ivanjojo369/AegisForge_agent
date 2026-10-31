"""Reproducible isolated local check; never imported by the running application.

Run: python -m aegisforge.products.live_forge.validation
Uses the existing parser, ASGI controls, adapters and Nexus verifiers. Fixtures
exist only in this process; sockets are blocked and checkpoints are temporary.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _frame(number, choice='A', *, chatter='101'):
    return {
        'metadata': {'message_type': 'notification', 'message_id': f'fixture-envelope-{number}',
                     'message_timestamp': datetime.now(timezone.utc).isoformat(),
                     'subscription_type': 'channel.chat.message', 'subscription_version': '1'},
        'payload': {'subscription': {'type': 'channel.chat.message', 'version': '1',
                                    'status': 'enabled',
                                    'condition': {'broadcaster_user_id': '900', 'user_id': '901'}},
                    'event': {'broadcaster_user_id': '900', 'chatter_user_id': chatter,
                              'message_id': f'fixture-chat-{number}',
                              'message': {'text': '!vote ' + choice}}}}


async def _transport_check():
    from .engine import LiveVoteEngine
    from . import twitch_eventsub as t
    engine = LiveVoteEngine(simulated=True)
    engine.start_round()
    message = _frame('handoff')
    welcome = lambda session, timeout: {
        'metadata': {'message_type': 'session_welcome'},
        'payload': {'session': {'id': session, 'keepalive_timeout_seconds': timeout}}}

    class Socket:
        def __init__(self, frames):
            self.frames, self.closed = iter(frames), False

        async def recv(self):
            try:
                return json.dumps(next(self.frames))
            except StopIteration:
                raise asyncio.CancelledError()

        async def close(self):
            self.closed = True

    first = Socket([welcome('first', 10), message,
                    {'metadata': {'message_type': 'session_reconnect'},
                     'payload': {'session': {'reconnect_url': t._TWITCH_WS + '?fixture=1'}}}])
    second = Socket([welcome('second', None), message])
    sockets = iter([first, second])
    posts = []

    async def connect(url, **kwargs):
        assert url.startswith(t._TWITCH_WS)
        return next(sockets)

    class HTTP:
        async def post(self, url, **kwargs):
            posts.append(kwargs['json'])
            return type('Reply', (), {'status_code': 202})()

    bridge = t.TwitchEventSubBridge(engine, t.TwitchConfig('fixture', 'fixture-not-a-token', '900', '901'))
    with patch.object(t, 'connect', connect):
        try:
            await bridge._connection(HTTP())
        except asyncio.CancelledError:
            pass
    assert len(posts) == 1 and bridge.reconnections == 1
    assert engine.snapshot()['accepted_vote_events'] == 1
    assert first.closed and second.closed and not bridge.connected
    for url in ('https://eventsub.wss.twitch.tv/ws', 'wss://example.invalid/ws',
                'wss://user@eventsub.wss.twitch.tv/ws', 'wss://eventsub.wss.twitch.tv:123/ws'):
        try:
            bridge._reconnect_url(url)
        except RuntimeError:
            continue
        raise AssertionError('unsafe reconnect accepted')


async def _check(folder):
    import httpx
    from . import server as s
    from .engine import LiveVoteEngine
    from .eventsub_ingestion import TwitchChatIngestion
    from .evidence_bridge import LiveForgeEvidenceBridge, _bounded_event, _digest
    from .creative_studio import initial_scene, plan_transition, verify_transition, LEGACY_SCHEMA, V2_SCHEMA, SCHEMA
    from .mechanic_composer import composition_id, render_mechanic, public_compositions
    from .mechanic_registry import ACTIVITIES, PACKS
    from .activity_contracts import virtual_compatible
    from .activity_bridge import initial_activity, plan_transition as plan_world, verify_transition as verify_world
    from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner
    from aegisforge.nexus.lab.scorecard import NexusScorecard
    from aegisforge.nexus.proof.proof_graph import NexusProofGraph
    from aegisforge.nexus.mission_control.mission_archive_history import NexusMissionArchiveHistory

    checks = []
    assert s.bridge is None
    s.engine = LiveVoteEngine(simulated=True)
    ingest = TwitchChatIngestion(s.engine, '900', '901')
    transport = httpx.ASGITransport(app=s.app, client=('127.0.0.1', 12345))
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8013') as client:
        async def command(action, expected=200, **fields):
            response = await client.post('/api/control', json={'action': action, **fields},
                                         headers={'Authorization': 'Bearer ' + s.operator_token})
            assert response.status_code == expected, (action, response.status_code, response.text)
            return response.json()

        state = (await client.get('/api/state')).json()
        assert state['connection'] == 'not_configured' and not state['live_connected']
        for path in ('/', '/app.js', '/studio', '/studio.js', '/bridge', '/bridge.js', '/overlay', '/overlay.js', '/api/mechanics'):
            assert (await client.get(path)).status_code == 200
        for action in ('policy', 'start', 'close', 'apply_winner', 'studio_apply', 'bridge_apply', 'stop'):
            assert (await client.post('/api/control', json={'action': action})).status_code == 403
        assert (await client.get('/api/evidence/latest')).status_code == 403
        assert (await client.post('/api/vote', json=_frame('http'))).status_code == 404
        assert (await client.post('/api/control', json={'action': 'start'}, headers={
            'Authorization': 'Bearer ' + s.operator_token, 'Origin': 'https://example.invalid'})).status_code == 403
        checks.append('offline_http_authorization_no_vote_endpoint')

        mix = composition_id('rpg_quest', 'progress_goal')
        policy = {**s.policy.active.public(), 'enabled': True, 'option_a': mix, 'cooldown_seconds': 10}
        await command('policy', policy=policy)
        await command('start')
        await command('start', 400)
        await command('policy', 400, policy=policy)
        await command('preview', 400, mechanic_id=mix)
        await command('studio_apply', 400, option='A')
        await command('apply_winner', 400, round=1, target='activity')
        assert ingest.process(None) == 'invalid'
        for mutate in (
            lambda f: f['payload']['event'].update(broadcaster_user_id='999'),
            lambda f: f['payload']['subscription']['condition'].update(user_id='999'),
            lambda f: f['metadata'].update(subscription_version='2'),
            lambda f: f['payload']['subscription'].update(status='revoked'),
            lambda f: f['metadata'].update(message_timestamp='invalid'),
            lambda f: f['metadata'].update(message_timestamp=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()),
        ):
            frame = _frame('invalid')
            mutate(frame)
            assert ingest.process(frame) == 'invalid'
        assert ingest.process(_frame('command', 'A; execute code')) == 'ignored'
        first = _frame('1')
        assert ingest.process(first) == 'accepted'
        assert ingest.process(first) == 'ignored'
        envelope = deepcopy(first)
        envelope['metadata']['message_id'] = 'different-envelope-same-chat'
        assert ingest.process(envelope) == 'ignored'
        assert ingest.process(_frame('2', 'B')) == 'accepted'
        assert ingest.process(_frame('3', 'A')) == 'accepted'
        assert ingest.process(_frame('4', 'B', chatter='102')) == 'accepted'
        assert ingest.process(_frame('5', 'A', chatter='103')) == 'accepted'
        before = s.studio.snapshot()
        closed = await command('close')
        assert closed['round']['choice_a'] == 2 and closed['round']['choice_b'] == 1
        assert closed['round']['winner'] == 'A' and s.studio.snapshot() == before
        assert ingest.process(_frame('closed')) == 'ignored'
        for action, fields in [('start', {}), ('policy', {'policy': policy}), ('studio_reset', {}),
                               ('apply_winner', {'round': 0, 'target': 'activity'})]:
            await command(action, 400, **fields)
        assert s.studio.snapshot() == before
        checks.append('eventsub_validation_dedup_frozen_round')

        # An unavailable recorder must leave the pending winner and activity intact.
        with patch.object(s.evidence, 'record', side_effect=OSError('isolated failure')):
            await command('apply_winner', 503, round=1, target='activity')
        assert s.studio.snapshot() == before and s.policy._last_effect is None
        applied = await command('apply_winner', round=1, target='activity')
        assert applied['scene']['progress'] == 1 and len(applied['scene']['elements']) == 1
        await command('apply_winner', 400, round=1, target='activity')
        assert s.studio.snapshot() == applied['scene']
        export = s.evidence.latest_export()
        assert export['record']['simulated'] is True
        assert export['record']['event_source'] == 'isolated_synthetic_eventsub_fixture'
        assert export['record']['audience_authenticity_verified'] is False
        assert export['record_sha256'] == _digest(export['record'])
        assert verify_transition(export['record']['studio_transition'])
        assert NexusEvaluationRunner.verify_report(export['replay']['evaluation'])
        assert NexusScorecard().verify(export['replay']['scorecard'], 'live_forge.local_activity', [export['replay']['evaluation']])
        assert NexusProofGraph.verify_snapshot(export['replay']['graph'])
        assert export['storage']['archived'] and all(export['checks'].values())
        assert export['laboratory']['status'] == 'passed'
        checks.append('art_winner_authorization_evidence_and_no_double_effect')

        await command('start')
        assert ingest.process(first) == 'ignored'  # Previous round replay.
        assert ingest.process(_frame('cooldown')) == 'accepted'
        await command('close')
        await command('apply_winner', 400, round=2, target='activity')
        assert s.studio.snapshot() == applied['scene']
        await command('stop')
        assert not s.policy.active.enabled and s.engine.snapshot()['resolution'] == 'cancelled'
        checks.append('cooldown_and_emergency_cancel')
        # Four persistent generations suffice to exercise rotation. Remaining
        # category checks use the same Nexus evidence path in memory.
        s.evidence = LiveForgeEvidenceBridge()

        # All seven existing adapters, catalog entries and compositions remain usable.
        for activity in ACTIVITIES:
            for mechanic in [*PACKS, *(c['id'] for c in public_compositions())]:
                assert render_mechanic(mechanic, activity)['id'] == mechanic
                if virtual_compatible(activity, mechanic):
                    transition = (plan_transition(initial_scene(), mechanic) if activity == 'art'
                                  else plan_world(initial_activity(activity), mechanic))
                    assert (verify_transition(transition) if activity == 'art' else verify_world(transition))
            if activity == 'art':
                continue
            with patch('aegisforge.products.live_forge.policy_engine.monotonic', return_value=10**12 + len(checks)*100):
                await command('policy', policy={**policy, 'activity': activity})
                started = await command('start')
                assert ingest.process(_frame(activity)) == 'accepted'
                await command('close')
                world = await command('apply_winner', round=started['round']['round'], target='activity')
                assert world['world']['tasks'] == 1 and world['world']['milestones'] == 1
                assert s.evidence.latest_export()['checks']['activity_transition_replayed']
                checks.append('category_' + activity)

        with patch('aegisforge.products.live_forge.policy_engine.monotonic', return_value=10**13):
            await command('policy', policy={**policy, 'activity': 'art', 'option_a': 'rhythm_overlay'})
            started = await command('start')
            assert ingest.process(_frame('overlay-only')) == 'accepted'
            await command('close')
            round_id = started['round']['round']
            await command('apply_winner', 400, round=round_id, target='activity')
            overlay = await command('apply_winner', round=round_id, target='overlay')
            assert overlay['effect']['id'] == 'rhythm_overlay'
            await command('apply_winner', 400, round=round_id, target='overlay')
            await command('stop')
            assert s.effects.snapshot() is None
        await command('policy', policy=policy)
        await command('start')
        empty = await command('close')
        assert empty['round']['winner'] is None
        await command('apply_winner', 400, round=empty['round']['round'], target='activity')
        await command('start')
        assert ingest.process(_frame('tie-a', chatter='201')) == 'accepted'
        assert ingest.process(_frame('tie-b', 'B', chatter='202')) == 'accepted'
        assert (await command('close'))['round']['winner'] is None
        checks.append('overlay_fallback_ties_empty_rounds')

        # Byte-identical old archives remain intact across later generations/restart.
        historical_files = {p: p.read_bytes() for p in Path(folder).rglob('*.json') if 'history' in str(p.parent)}
        assert historical_files
        loaded = LiveForgeEvidenceBridge(folder)
        assert loaded.storage_issue is None
        assert NexusMissionArchiveHistory(loaded._store, authorizer=lambda *args: False).list_history()
        assert all(p.read_bytes() == raw for p, raw in historical_files.items())
        for schema in (LEGACY_SCHEMA, V2_SCHEMA, SCHEMA):
            assert verify_transition(plan_transition(initial_scene(schema=schema), 'quest_palette_mix'))
        legacy_votes = {'round': 1, 'choice_a': 1, 'choice_b': 0, 'unique_voters': 1,
                        'accepted_vote_events': 1, 'round_open': False, 'winner': 'A'}
        for operation, expected_schema, extra in (
            ('round_closed', 'v1', {}),
            ('studio_apply', 'v2', {'studio_transition': plan_transition(initial_scene(schema=LEGACY_SCHEMA), mix)}),
            ('bridge_apply', 'v3', {'activity_transition': plan_world(initial_activity('coding'), mix)}),
        ):
            event, _ = _bounded_event(operation, legacy_votes,
                                      {**policy, 'activity': 'coding' if operation == 'bridge_apply' else 'art'},
                                      None, None, False, **extra)
            assert event['schema'].endswith(expected_schema) and 'simulated' not in event
        checks.append('historical_schemas_checkpoint_reload_and_hashes')

        # Real archive-stage failure, confined to a new temporary checkpoint.
        broken = Path(folder) / 'failure-case'
        broken.mkdir()
        s.evidence = LiveForgeEvidenceBridge(str(broken))
        await command('start')
        assert ingest.process(_frame('archive-failure')) == 'accepted'
        await command('close')
        stable = s.studio.snapshot()
        with patch('aegisforge.products.live_forge.policy_engine.monotonic', return_value=10**14):
            with patch.object(s.evidence, '_capture', side_effect=OSError('isolated archive failure')):
                await command('apply_winner', 503, round=s.engine.snapshot()['round'], target='activity')
        assert s.studio.snapshot() == stable and s.evidence.storage_issue
        assert LiveForgeEvidenceBridge(str(broken)).storage_issue
        checks.append('partial_archive_fails_closed_without_activity_change')

    # The former 1,024-ID eviction bug: replay must remain ignored.
    bounded = LiveVoteEngine(simulated=True)
    bounded.start_round()
    parser = TwitchChatIngestion(bounded, '900', '901')
    original = _frame('oldest')
    assert parser.process(original) == 'accepted'
    for i in range(1100):
        parser.process(_frame(f'volume-{i}', 'B' if i % 2 else 'A'))
    stable = bounded.snapshot()
    assert parser.process(original) == 'ignored' and bounded.snapshot() == stable
    checks.append('dedup_beyond_1024_notifications')
    await _transport_check()
    checks.append('websocket_handoff_one_subscription_no_duplicate_vote')
    return {'status': 'passed', 'checks': checks, 'check_groups': len(checks),
            'synthetic_only': True, 'real_twitch_connection_tested': False,
            'network_access': False, 'temporary_checkpoints_removed_on_exit': True,
            'art_record_sha256': export['record_sha256'],
            'art_laboratory_status': export['laboratory']['status'],
            'art_proof_graph_sha256': export['proof_graph']['graph_sha256']}


def main():
    if 'aegisforge.products.live_forge.server' in sys.modules:
        raise RuntimeError('validation_requires_a_separate_process')
    # Assign known local values before importing the app; never load real tokens.
    os.environ['LIVE_FORGE_OFFLINE'] = '1'
    os.environ['LIVE_FORGE_OPERATOR_TOKEN'] = 'isolated-validation-not-a-real-token'
    with TemporaryDirectory(prefix='live-forge-isolated-') as folder:
        os.environ['LIVE_FORGE_CHECKPOINT_DIR'] = folder
        # Windows creates a loopback self-pipe when constructing its event loop.
        # Block socket connections after that runtime-only initialization.
        with asyncio.Runner() as runner:
            with patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')), \
                 patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('network forbidden')):
                result = runner.run(_check(folder))
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
