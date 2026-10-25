#!/usr/bin/env python3
"""Send a real A2A image-bearing request to an IAM-protected AWS Lambda Function URL.

Uses AWS SDK's configured credential provider chain; never asks you to paste keys.
Requires: pip install boto3 (in the user's authorized AWS Python environment).
Does not deploy, create accounts, change IAM policy, or print credentials.
The output must contain a completed Quipu Vision A2A result to count as evidence.
"""
import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True, help='AWS_IAM Lambda Function URL (HTTPS).')
    p.add_argument('--region', required=True, help='AWS region of the Lambda function.')
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--observed', type=Path, required=True)
    p.add_argument('--followup', type=Path, help='Optional third image; selects verify.')
    p.add_argument('--save-json', type=Path, help='Optional local result file; review before sharing.')
    return p.parse_args()


def make_request(args):
    files = [('reference', args.reference), ('observed', args.observed)]
    if args.followup:
        files.append(('followup', args.followup))
    parts = [{'kind': 'data', 'data': {'capability': 'quipu_vision', 'operation': 'verify' if args.followup else 'inspect'}}]
    extmap = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.bmp': 'image/bmp', '.webp': 'image/webp'}
    total = 0
    for role, file in files:
        if file.suffix.lower() not in extmap:
            raise ValueError(f'Unsupported file extension: {file.name}')
        data = file.read_bytes()
        if not data or len(data) > 10 * 1024 * 1024:
            raise ValueError(f'{file.name} must be 1 byte to 10 MiB')
        total += len(data)
        parts.append({'kind': 'file', 'file': {'name': f'{role}{file.suffix.lower()}', 'mimeType': extmap[file.suffix.lower()], 'bytes': base64.b64encode(data).decode('ascii')}})
    if total > 32 * 1024 * 1024:
        raise ValueError('Combined images exceed 32 MiB')
    return {
        'jsonrpc': '2.0', 'id': str(uuid.uuid4()), 'method': 'message/send',
        'params': {'message': {'kind': 'message', 'role': 'user', 'messageId': str(uuid.uuid4()), 'parts': parts}}
    }


def main():
    args = parse_args()
    parsed = urlparse(args.url)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.on.aws'):
        raise ValueError('Expected an HTTPS AWS Lambda Function URL (*.lambda-url.*.on.aws)')
    try:
        import boto3
        from botocore.awsrequest import AWSRequest
        from botocore.auth import SigV4Auth
    except ImportError as exc:
        raise SystemExit('Install boto3 in an authorized AWS environment: python -m pip install boto3') from exc
    session = boto3.Session(region_name=args.region)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError('AWS credentials unavailable; use an authorized AWS profile/role.')
    body = json.dumps(make_request(args), separators=(',', ':')).encode('utf-8')
    awsreq = AWSRequest(method='POST', url=args.url, data=body, headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    SigV4Auth(credentials.get_frozen_credentials(), 'lambda', args.region).add_auth(awsreq)
    signed = awsreq.prepare()
    request = urllib.request.Request(args.url, data=body, headers=dict(signed.headers), method='POST')
    try:
        with urllib.request.urlopen(request, timeout=105) as response:
            status, raw = response.status, response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        print(f'AWS HTTP error: {exc.code}; inspect IAM permissions and Lambda CloudWatch logs.', file=sys.stderr)
        return 1
    print(f'HTTP: {status}; payload bytes: {len(raw)}')
    payload = json.loads(raw)
    if args.save_json:
        args.save_json.parent.mkdir(parents=True, exist_ok=True)
        args.save_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'Response saved locally: {args.save_json}')
    if 'error' in payload:
        print('A2A JSON-RPC returned an error: processing NOT verified.')
        return 2
    task = payload.get('result') or {}
    state = (task.get('status') or {}).get('state')
    artifacts = task.get('artifacts') or []
    vision_artifacts = [a for a in artifacts if a.get('name') == 'quipu_vision_result']
    for a in vision_artifacts:
        for part in a.get('parts') or []:
            info = part.get('data') if isinstance(part, dict) else None
            if isinstance(info, dict):
                output = info.get('output') or {}
                if output.get('status') != 'completed' or info.get('ok') is not True:
                    continue
                if output.get('operation') != ('verify' if args.followup else 'inspect'):
                    continue
                cases = output.get('results') or []
                if not cases or not isinstance(cases[0], dict):
                    continue
                case = cases[0]
                evidence = case.get('evidence') or {}
                hashes = evidence.get('source_sha256') or {}
                if str(evidence.get('cv2_version', '')).split('.')[0] != '5':
                    continue
                if not all(hashes.get(k) for k in ('reference', 'observation')):
                    continue
                if args.followup and (not case.get('followup_processed') or not hashes.get('followup')):
                    continue
                print(f"A2A VISION PROCESSING VERIFIED: operation={output.get('operation')} next_action={output.get('next_action')} OpenCV={evidence.get('cv2_version')} plugin={output.get('plugin_version')}")
                return 0
    print(f'A2A not yet verified: task state={state!r}, vision artifacts={len(vision_artifacts)}. Inspect saved JSON/logs.')
    return 3


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(f'Probe failed: {error}', file=sys.stderr)
        sys.exit(1)
