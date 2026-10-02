"""Testy offline modułu teams_notifier oraz integracji z ksef_faktury_list."""
import contextlib
import io
import sys
import unittest
from unittest.mock import Mock, patch

import teams_notifier as tn
import ksef_faktury_list as app


class PayloadTests(unittest.TestCase):
    def test_adaptive_payload_structure(self):
        payload = tn.build_payload(
            status='error', title='Błąd', text='Szczegóły',
            facts={'NIP': '1234567890', 'Kod HTTP': '500'},
        )
        self.assertEqual(payload['type'], 'message')
        card = payload['attachments'][0]['content']
        self.assertEqual(payload['attachments'][0]['contentType'],
                         'application/vnd.microsoft.card.adaptive')
        self.assertEqual(card['type'], 'AdaptiveCard')
        types = [item['type'] for item in card['body']]
        self.assertIn('FactSet', types)
        self.assertEqual(card['body'][0]['text'], 'Błąd')
        self.assertEqual(card['body'][0]['color'], 'Attention')

    def test_legacy_card_payload(self):
        payload = tn.build_payload(status='success', title='OK',
                                   facts={'Liczba faktur': '3'}, payload_format='card')
        self.assertEqual(payload['@type'], 'MessageCard')
        self.assertEqual(payload['themeColor'], '2DA44E')
        self.assertEqual(payload['sections'][0]['facts'][0]['value'], '3')

    def test_text_payload(self):
        payload = tn.build_payload(status='info', title='Tytuł',
                                   text='Treść', payload_format='text')
        self.assertEqual(payload, {'text': 'Tytuł\n\nTreść'})


class SendTests(unittest.TestCase):
    def test_sends_and_accepts_202(self):
        response = Mock(status_code=202, text='')
        with patch('teams_notifier.requests.post', return_value=response) as post:
            result = tn.send_teams_message('https://example.invalid/hook',
                                           status='success', title='OK')
        self.assertIs(result, response)
        self.assertEqual(post.call_args.kwargs['json']['type'], 'message')

    def test_retry_on_error_then_success(self):
        bad = Mock(status_code=500, headers={}, text='server error')
        good = Mock(status_code=202, text='')
        with patch('teams_notifier.requests.post', side_effect=[bad, good]) as post, \
             patch('teams_notifier.time.sleep') as sleep:
            result = tn.send_teams_message('https://example.invalid/hook')
        self.assertIs(result, good)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called()

    def test_fails_after_all_retries(self):
        bad = Mock(status_code=500, headers={}, text='server error')
        with patch('teams_notifier.requests.post', return_value=bad) as post, \
             patch('teams_notifier.time.sleep'):
            with self.assertRaises(RuntimeError):
                tn.send_teams_message('https://example.invalid/hook')
        self.assertEqual(post.call_count, tn.DEFAULT_RETRIES)

    def test_missing_webhook_url_raises(self):
        with self.assertRaises(ValueError):
            tn.send_teams_message(None)


class WrapperTests(unittest.TestCase):
    def run_notifier(self, argv):
        with patch.object(tn, 'send_teams_message') as notify, \
             contextlib.redirect_stdout(io.StringIO()):
            code = tn.main(argv)
        return code, notify

    def test_wrapper_success_parses_facts(self):
        cmd = [sys.executable, '-c',
               "print('NIP: 1234567890');"
               "print('Łączenie z KSeF (środowisko: prod)...');"
               "print('Razem: 3 faktur(a/y)')"]
        code, notify = self.run_notifier(
            ['--webhook-url', 'https://example.invalid/hook', '--'] + cmd)
        self.assertEqual(code, 0)
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs['status'], 'success')
        self.assertEqual(kwargs['facts']['Liczba faktur'], '3')
        self.assertEqual(kwargs['facts']['NIP'], '1234567890')
        self.assertEqual(kwargs['facts']['Środowisko'], 'prod')

    def test_wrapper_error_reports_exit_code(self):
        cmd = [sys.executable, '-c',
               "import sys; print('boom', file=sys.stderr); sys.exit(1)"]
        code, notify = self.run_notifier(
            ['--webhook-url', 'https://example.invalid/hook', '--'] + cmd)
        self.assertEqual(code, 1)
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs['status'], 'error')
        self.assertEqual(kwargs['facts']['Kod wyjścia'], '1')

    def test_wrapper_notify_on_error_skips_success(self):
        cmd = [sys.executable, '-c', "print('ok')"]
        code, notify = self.run_notifier(
            ['--webhook-url', 'https://example.invalid/hook',
             '--notify-on', 'error', '--'] + cmd)
        self.assertEqual(code, 0)
        notify.assert_not_called()

    def test_wrapper_without_webhook_fails_fast(self):
        with patch.dict('os.environ', {'TEAMS_WEBHOOK_URL': ''}), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                tn.main(['--', sys.executable, '-c', "print('ok')"])
        self.assertEqual(raised.exception.code, 2)

    def test_manual_mode_sends_message(self):
        code, notify = self.run_notifier(
            ['--webhook-url', 'https://example.invalid/hook',
             '--status', 'success', '--message', 'Test ręczny'])
        self.assertEqual(code, 0)
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs['status'], 'success')
        self.assertEqual(kwargs['text'], 'Test ręczny')


class KSeFIntegrationTests(unittest.TestCase):
    def test_success_notification_after_download(self):
        client = Mock()
        client.init_session_token.return_value = {'reference_number': 'offline'}
        client.query_invoices.return_value = {'invoices': [
            {'ksefNumber': 'K1'}, {'ksefNumber': 'K2'},
        ]}
        with patch.object(app.KSeFClient, 'from_token', return_value=client), \
             patch.object(app, 'send_teams_message') as notify, \
             patch('sys.argv', ['script', '--nip', '1234567890', '--token', 'offline',
                                '--teams-webhook-url', 'https://example.invalid/hook']), \
             contextlib.redirect_stdout(io.StringIO()):
            app.main()
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs['status'], 'success')
        self.assertEqual(kwargs['facts']['Liczba faktur'], '2')
        self.assertEqual(kwargs['facts']['NIP'], '1234567890')

    def test_error_notification_on_ksef_error(self):
        client = Mock()
        client.init_session_token.return_value = {'reference_number': 'offline'}
        client.query_invoices.side_effect = app.KSeFError(
            'Błąd testowy', status_code=500)
        with patch.object(app.KSeFClient, 'from_token', return_value=client), \
             patch.object(app, 'send_teams_message') as notify, \
             patch('sys.argv', ['script', '--nip', '1234567890', '--token', 'offline',
                                '--teams-webhook-url', 'https://example.invalid/hook']), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                app.main()
        self.assertEqual(raised.exception.code, 1)
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs['status'], 'error')
        self.assertEqual(kwargs['text'], 'Błąd testowy')
        self.assertEqual(kwargs['facts']['Kod HTTP'], '500')

    def test_no_notification_without_webhook(self):
        client = Mock()
        client.init_session_token.return_value = {'reference_number': 'offline'}
        client.query_invoices.return_value = {'invoices': []}
        with patch.object(app.KSeFClient, 'from_token', return_value=client), \
             patch.object(app, 'send_teams_message') as notify, \
             patch('sys.argv', ['script', '--nip', '1234567890', '--token', 'offline']), \
             contextlib.redirect_stdout(io.StringIO()):
            app.main()
        notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
