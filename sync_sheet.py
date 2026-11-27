"""Call the existing GAS sync endpoint only after the CSV commit is pushed."""
import os
import requests


def main():
    url = os.environ.get('SHEET_HOOK', '').strip()
    if not url:
        print('SHEET_HOOK未設定：既存の定期同期を継続（即時同期は未有効）')
        return False
    try:
        response = requests.get(url, timeout=75)
        ok = response.status_code == 200 and response.text.strip() == 'ok'
    except requests.RequestException:
        ok = False
    print('シート即時同期成功' if ok else 'シート即時同期失敗（定期同期で再取得）')
    return ok


if __name__ == '__main__':
    main()
