#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# MIT License
#
# Copyright (c) 2026
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
"""
Microsoft Teams webhook notifier for the KSeF invoice download script.

Sends a notification to a Teams channel via an incoming webhook
(Power Automate workflow) when invoice downloading succeeds or fails.

Two modes:

1. Wrapper mode - run the KSeF script (or any command) and post a Teams
   notification depending on its exit code:

    python teams_notifier.py --webhook-url "https://..." -- \\
        python ksef_faktury_list.py --nip 1234567890 --token-file token.txt --isSelfInvoicing true

2. Manual/test mode - send a single message:

    python teams_notifier.py --webhook-url "https://..." --status success --message "Test"

The webhook URL can also be provided with the TEAMS_WEBHOOK_URL environment
variable. The module is also importable:

    from teams_notifier import send_teams_message
"""

import argparse
import os
import re
import subprocess
import sys
import time

import requests

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 3
RETRY_DELAY = 2.0
OUTPUT_TAIL_CHARS = 700

STATUS_THEME = {
    'success': '2DA44E',
    'error': 'D1242F',
    'warning': 'BF8700',
    'info': '4472C4',
}

DEFAULT_TITLES = {
    'success': 'KSeF: pobieranie faktur zakończone sukcesem',
    'error': 'KSeF: błąd pobierania faktur',
    'warning': 'KSeF: ostrzeżenie',
    'info': 'KSeF: powiadomienie',
}


def _truncate(text, limit=OUTPUT_TAIL_CHARS):
    """Return the tail of text, trimmed to limit characters."""
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    return '…' + text[-limit:]


def build_payload(status='info', title='KSeF', text=None, facts=None,
                  payload_format='adaptive'):
    """
    Build a webhook payload in the selected format.

    Formats:
        adaptive - Adaptive Card (Power Automate Workflows webhook, default)
        card     - legacy O365 connector MessageCard
        text     - plain text (for custom Power Automate flows)
    """
    color = STATUS_THEME.get(status, STATUS_THEME['info'])
    facts = facts or {}

    if payload_format == 'text':
        lines = [title]
        for key, value in facts.items():
            lines.append(f"{key}: {value}")
        if text:
            lines.append('')
            lines.append(text)
        return {'text': '\n'.join(lines)}

    if payload_format == 'card':
        payload = {
            '@type': 'MessageCard',
            '@context': 'http://schema.org/extensions',
            'themeColor': color,
            'summary': title,
            'title': title,
            'sections': [],
        }
        if facts:
            payload['sections'].append({
                'facts': [{'name': key, 'value': str(value)} for key, value in facts.items()]
            })
        if text:
            payload['text'] = text
        return payload

    # adaptive (default)
    adaptive_colors = {
        'success': 'Good',
        'error': 'Attention',
        'warning': 'Warning',
    }
    body = [{
        'type': 'TextBlock',
        'text': title,
        'weight': 'Bolder',
        'size': 'Medium',
        'wrap': True,
        'color': adaptive_colors.get(status, 'Default'),
    }]
    if facts:
        body.append({
            'type': 'FactSet',
            'facts': [{'title': key, 'value': str(value)} for key, value in facts.items()],
        })
    if text:
        body.append({'type': 'TextBlock', 'text': text, 'wrap': True, 'isSubtle': True})
    return {
        'type': 'message',
        'attachments': [{
            'contentType': 'application/vnd.microsoft.card.adaptive',
            'content': {
                'type': 'AdaptiveCard',
                'version': '1.4',
                '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
                'body': body,
            },
        }],
    }


def send_teams_message(
    webhook_url,
    status='info',
    title=None,
    text=None,
    facts=None,
    payload_format='adaptive',
    timeout=DEFAULT_TIMEOUT,
    max_retries=DEFAULT_RETRIES,
):
    """
    Send a notification to a Teams channel webhook.

    Args:
        webhook_url: Teams incoming webhook URL (required)
        status: 'success', 'error', 'warning' or 'info' (controls the color)
        title: Message title (default depends on status)
        text: Optional message body
        facts: Optional dict of label -> value shown as a fact list
        payload_format: 'adaptive', 'card' or 'text'
        timeout: HTTP request timeout in seconds
        max_retries: Number of delivery attempts

    Raises:
        ValueError: when webhook_url is empty
        RuntimeError: when delivery fails after all retries
    """
    if not webhook_url:
        raise ValueError('Brak adresu webhooka Teams (--webhook-url / TEAMS_WEBHOOK_URL)')

    payload = build_payload(
        status=status,
        title=title or DEFAULT_TITLES.get(status, DEFAULT_TITLES['info']),
        text=text,
        facts=facts,
        payload_format=payload_format,
    )

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(webhook_url, json=payload, timeout=timeout)
            if response.status_code < 400:
                return response

            last_error = f'HTTP {response.status_code}: {response.text[:200]}'
            if attempt < max_retries:
                wait = RETRY_DELAY
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    try:
                        wait = max(float(retry_after), RETRY_DELAY)
                    except (ValueError, TypeError):
                        pass
                print(
                    f"[teams_notifier] Ponowienie {attempt}/{max_retries} po {wait}s "
                    f"({last_error})...",
                    file=sys.stderr,
                )
                time.sleep(wait)
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < max_retries:
                print(
                    f"[teams_notifier] Ponowienie {attempt}/{max_retries} po {RETRY_DELAY}s "
                    f"({last_error})...",
                    file=sys.stderr,
                )
                time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"Nie udało się wysłać powiadomienia do Teams po {max_retries} próbach: {last_error}"
    )


def _safe_send(args, status, title, text=None, facts=None):
    """Send a Teams notification, printing a warning instead of raising."""
    try:
        send_teams_message(
            webhook_url=args.webhook_url,
            status=status,
            title=title,
            text=text,
            facts=facts,
            payload_format=args.payload_format,
            timeout=args.timeout,
        )
        print(f"[teams_notifier] Powiadomienie Teams wysłane ({status}).", flush=True)
    except Exception as exc:
        print(f"[teams_notifier] UWAGA: nie udało się wysłać powiadomienia Teams: {exc}",
              file=sys.stderr)


def run_with_notification(args, cmd):
    """
    Run cmd, stream its output, then notify Teams based on the exit code.

    Returns the exit code of the executed command.
    """
    print(f"[teams_notifier] Uruchamiam: {' '.join(cmd)}", flush=True)

    child_env = dict(os.environ)
    child_env.setdefault('PYTHONIOENCODING', 'utf-8')

    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=child_env,
        )
    except FileNotFoundError as exc:
        print(f"[teams_notifier] Nie można uruchomić polecenia: {exc}", file=sys.stderr)
        if args.notify_on in ('all', 'error'):
            _safe_send(
                args, 'error', 'KSeF: nie udało się uruchomić zadania',
                text=str(exc),
                facts={'Polecenie': ' '.join(cmd)},
            )
        return 127

    if process.stdout:
        sys.stdout.write(process.stdout)
        sys.stdout.flush()
    if process.stderr:
        sys.stderr.write(process.stderr)
        sys.stderr.flush()

    success = process.returncode == 0
    should_notify = (
        args.notify_on == 'all'
        or (args.notify_on == 'success' and success)
        or (args.notify_on == 'error' and not success)
    )

    if args.webhook_url and should_notify:
        stdout = process.stdout or ''
        stderr = process.stderr or ''
        if success:
            facts = {'Wynik': 'sukces'}
            match = re.search(r'^Razem:\s*(\d+)\s+faktur', stdout, re.M)
            if match:
                facts['Liczba faktur'] = match.group(1)
            match = re.search(r'^NIP:\s*(\S+)', stdout, re.M)
            if match:
                facts['NIP'] = match.group(1)
            match = re.search(r'środowisko:\s*(\w+)', stdout)
            if match:
                facts['Środowisko'] = match.group(1)
            _safe_send(args, 'success', DEFAULT_TITLES['success'],
                       text=_truncate(stdout) or None, facts=facts)
        else:
            _safe_send(
                args, 'error',
                f"KSeF: błąd pobierania faktur (kod wyjścia {process.returncode})",
                text=_truncate(stderr + '\n' + stdout) or None,
                facts={'Kod wyjścia': str(process.returncode)},
            )

    return process.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='teams_notifier',
        description='Wysyła powiadomienia do grupy Teams (webhook) po udanym pobraniu '
                    'faktur KSeF lub w razie błędu.',
        epilog="""
Examples:
    # Test wiadomości
    %(prog)s --webhook-url "https://..." --status success --message "Test"

    # Uruchomienie skryptu KSeF z powiadomieniem o wyniku (wrapper)
    %(prog)s --webhook-url "https://..." -- python ksef_faktury_list.py \\
        --nip 1234567890 --token-file token.txt --isSelfInvoicing true
        """
    )
    parser.add_argument(
        '--webhook-url',
        default=os.environ.get('TEAMS_WEBHOOK_URL'),
        help='Adres webhooka Teams (domyślnie zmienna środowiskowa TEAMS_WEBHOOK_URL)'
    )
    parser.add_argument(
        '--status',
        choices=['success', 'error', 'warning', 'info'],
        default='info',
        help='Status wiadomości – decyduje o kolorze karty (default: info)'
    )
    parser.add_argument(
        '--title',
        help='Tytuł wiadomości (domyślnie dobierany wg statusu)'
    )
    parser.add_argument(
        '--message', '-m',
        help='Treść wiadomości (tryb ręczny)'
    )
    parser.add_argument(
        '--format',
        dest='payload_format',
        choices=['adaptive', 'card', 'text'],
        default='adaptive',
        help='Format wiadomości: adaptive (Power Automate, domyślny), '
             'card (stary łącznik O365), text (zwykły tekst)'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=DEFAULT_TIMEOUT,
        help='Timeout żądania HTTP w sekundach (default: 10)'
    )
    parser.add_argument(
        '--notify-on',
        choices=['all', 'success', 'error'],
        default='all',
        help='Tryb wrapper: kiedy wysyłać powiadomienia (default: all)'
    )
    parser.add_argument(
        'cmd',
        nargs=argparse.REMAINDER,
        metavar='-- CMD',
        help='Po "--": polecenie do uruchomienia (tryb wrapper)'
    )

    args = parser.parse_args(argv)

    cmd = args.cmd
    if cmd and cmd[0] == '--':
        cmd = cmd[1:]

    if cmd:
        if not args.webhook_url:
            parser.error('--webhook-url (lub zmienna TEAMS_WEBHOOK_URL) jest wymagane '
                         'w trybie wrapper')
        return run_with_notification(args, cmd)

    if not args.webhook_url:
        parser.error('--webhook-url (lub zmienna TEAMS_WEBHOOK_URL) jest wymagane')

    title = args.title or DEFAULT_TITLES.get(args.status, DEFAULT_TITLES['info'])
    message = args.message if args.message is not None else \
        'Test powiadomień Teams z teams_notifier.py.'
    _safe_send(args, args.status, title, text=message)
    return 0


if __name__ == '__main__':
    sys.exit(main())
