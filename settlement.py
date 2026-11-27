"""Settle 100-yen trifecta tickets from explicit official evidence only."""
import json
import re


def settle(pick, result):
    raw = result.get('tri_payouts', {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    payouts = raw if isinstance(raw, dict) else {}
    tri = result.get('tri')
    pay = result.get('tri_pay')
    if isinstance(tri, str) and re.fullmatch(r'[1-6]-[1-6]-[1-6]', tri):
        try:
            if float(pay) > 0:
                payouts.setdefault(tri, int(float(pay)))
        except (ValueError, TypeError):
            pass
    refunds = set(re.findall(r'[1-6]', str(result.get('refund_boats', ''))))
    if result.get('race_status') == 'cancelled' or refunds.intersection(pick.split('-')):
        return 'refunded', 0, 100, '返還'
    if payouts:
        return 'settled', int(pick in payouts), int(payouts.get(pick, 0)), '/'.join(payouts)
    return 'pending', None, None, ''
