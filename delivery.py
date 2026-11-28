"""Reserve notification IDs in GitHub before delivery (compare-and-swap).

The remote reservation survives a failed runner or a failed final logs push.
Pending/unknown reservations are never retried automatically. This trades a
possible missing notification for avoiding duplicate betting notifications.
No topic, token, message, or relay address is stored in the public ledger.
"""
import base64
import hashlib
import json
import os
import re

import requests


class Ledger:
    def __init__(self):
        self.token = os.environ.get('GITHUB_TOKEN', '')
        self.repo = os.environ.get('GITHUB_REPOSITORY', '')
        self.enabled = os.environ.get('GITHUB_ACTIONS') == 'true'

    def reserve(self, notification_id, receipt=None):
        if not self.enabled:
            return {'local': True}
        if not self.token or not self.repo:
            return None  # Fail closed in Actions.
        key = hashlib.sha256(notification_id.encode()).hexdigest()
        url = f'https://api.github.com/repos/{self.repo}/contents/logs/delivery/{key}.json'
        headers = {'Authorization': 'Bearer ' + self.token,
                   'Accept': 'application/vnd.github+json'}
        try:
            r = requests.get(url, headers=headers, timeout=15)
            sha = None
            if r.status_code == 200:
                obj = r.json()
                prior = json.loads(base64.b64decode(obj['content']))
                retry = prior.get('status') == '送信失敗' or (
                    re.fullmatch(r'consensus-v1-[0-9]{8}', notification_id) is not None
                    and prior.get('status') == '締切により中止')
                if not retry:
                    return None
                sha = obj['sha']
            elif r.status_code != 404:
                return None
            record = {'notification_id': notification_id, 'status': '送信予約',
                      'run_id': os.environ.get('GITHUB_RUN_ID', '')}
            if receipt is not None:
                record['receipt'] = receipt
            body = self.payload(record, sha)
            r = requests.put(url, headers=headers, json=body, timeout=20)
            if r.status_code not in (200, 201):
                return None
            return {'url': url, 'headers': headers,
                    'sha': r.json()['content']['sha'], 'record': record}
        except (requests.RequestException, ValueError, KeyError):
            # A timeout might have committed: do not send without confirmation.
            return None

    @staticmethod
    def payload(record, sha=None):
        body = {'message': 'notification ledger', 'branch': 'main',
                'content': base64.b64encode(json.dumps(record, ensure_ascii=False).encode()).decode()}
        if sha:
            body['sha'] = sha
        return body

    def finish(self, claim, status):
        if claim.get('local'):
            return
        record = dict(claim['record'], status=status)
        try:
            r = requests.put(claim['url'], headers=claim['headers'],
                             json=self.payload(record, claim['sha']), timeout=20)
            if r.status_code not in (200, 201):
                print('通知台帳の完了記録に失敗（送信予約を維持）')
        except requests.RequestException:
            print('通知台帳の完了記録に失敗（送信予約を維持）')
