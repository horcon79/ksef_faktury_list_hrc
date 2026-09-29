"""Testy offline nowego filtra oraz oryginalnych ścieżek PDF/SMTP."""
import contextlib
import datetime
import email
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ksef_faktury_list as app


XML = '''<?xml version="1.0" encoding="utf-8"?>
<Faktura xmlns="http://crd.gov.pl/wzor/2025/06/25/13775/">
  <Podmiot1><DaneIdentyfikacyjne><NIP>1234567890</NIP><Nazwa>Sprzedawca testowy</Nazwa></DaneIdentyfikacyjne></Podmiot1>
  <Podmiot2><DaneIdentyfikacyjne><NIP>9876543210</NIP><Nazwa>Nabywca testowy</Nazwa></DaneIdentyfikacyjne></Podmiot2>
  <Fa><KodWaluty>PLN</KodWaluty><P_1>2026-09-28</P_1><P_2>TEST/1</P_2>
    <P_13_1>100.00</P_13_1><P_14_1>23.00</P_14_1><P_15>123.00</P_15>
    <Adnotacje><P_17>1</P_17></Adnotacje>
    <FaWiersz><NrWierszaFa>1</NrWierszaFa><P_7>Usługa testowa</P_7><P_8A>szt.</P_8A><P_8B>1</P_8B><P_9A>100</P_9A><P_11>100</P_11><P_12>23</P_12></FaWiersz>
  </Fa>
</Faktura>'''.encode('utf-8')


class FilterTests(unittest.TestCase):
    def test_api_boolean_and_original_request_when_omitted(self):
        c = app.KSeFClient.from_token('offline-test-token', environment='prod')
        c.access_token = 'offline-test-access'
        c._make_request = Mock(return_value={'invoices': []})
        for flag in (None, True, False):
            with self.subTest(flag=flag):
                c.query_invoices(date_from=datetime.date(2026, 9, 26),
                    date_to=datetime.date(2026, 9, 28), is_self_invoicing=flag)
                body = c._make_request.call_args.kwargs['data']
                if flag is None:
                    self.assertNotIn('isSelfInvoicing', body)
                else:
                    self.assertIs(body['isSelfInvoicing'], flag)
                self.assertEqual(body['subjectType'], 'Subject2')
                self.assertEqual(body['dateRange']['dateType'], 'Invoicing')
        with self.assertRaises(ValueError):
            c.query_invoices(is_self_invoicing='false')

    def test_cli_aliases_and_false(self):
        for switches, expected in [([], None), (['--is-self-invoicing'], True),
            (['--isSelfInvoicing'], True), (['--isSelfInvoicing', 'true'], True),
            (['--is-self-invoicing=false'], False), (['--isSelfInvoicing', 'FALSE'], False)]:
            with self.subTest(switches=switches):
                client = Mock()
                client.init_session_token.return_value = {'reference_number': 'offline'}
                client.query_invoices.return_value = {'invoices': []}
                with patch.object(app.KSeFClient, 'from_token', return_value=client), \
                     patch('sys.argv', ['script', '--nip', '1234567890', '--token', 'offline'] + switches), \
                     contextlib.redirect_stdout(io.StringIO()):
                    app.main()
                self.assertIs(client.query_invoices.call_args.kwargs['is_self_invoicing'], expected)

    def test_invalid_value_stops_before_connecting(self):
        with patch.object(app.KSeFClient, 'from_token') as connect, \
             patch('sys.argv', ['script', '--nip', '1234567890', '--token', 'offline', '--isSelfInvoicing', 'maybe']), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                app.main()
            self.assertEqual(raised.exception.code, 2)
            connect.assert_not_called()

    def test_filtered_pdf_xml_and_smtp_single_and_grouped(self):
        rows = [
            {'ksefNumber': '1234567890-20260928-010080615740-E4', 'invoiceNumber': 'TEST/1', 'isSelfInvoicing': True},
            {'ksefNumber': '1234567890-20260928-010080615741-E5', 'invoiceNumber': 'TEST/2', 'isSelfInvoicing': True},
            {'ksefNumber': '1234567890-20260928-010080615742-E6', 'invoiceNumber': 'TEST/3', 'isSelfInvoicing': False},
        ]
        for group in ('single', 'all'):
            with self.subTest(group=group), tempfile.TemporaryDirectory() as temp:
                c = app.KSeFClient.from_token('offline', environment='prod')
                c.access_token = 'offline'
                c.init_session_token = Mock(return_value={'reference_number': 'offline'})
                c.terminate_session = Mock()
                c.get_invoice_xml = Mock(return_value=XML)
                def query(method, endpoint, data=None, **kwargs):
                    self.assertIs(data['isSelfInvoicing'], True)
                    return {'invoices': [r for r in rows if r['isSelfInvoicing'] is data['isSelfInvoicing']]}
                c._make_request = Mock(side_effect=query)
                argv = ['script', '--nip', '1234567890', '--token', 'offline', '--isSelfInvoicing',
                    '--env', 'prod', '--subject-type', 'Subject1', '--download-xml', '--xml-output-dir', temp,
                    '--download-pdf', '--pdf-output-dir', temp, '--smtp-host', 'smtp.example.invalid',
                    '--smtp-user', 'sender@example.invalid', '--smtp-password', 'offline',
                    '--email-to', 'recipient@example.invalid', '--email-group', group]
                with patch.object(app.KSeFClient, 'from_token', return_value=c), \
                     patch.object(app.smtplib, 'SMTP') as smtp, patch('sys.argv', argv), \
                     contextlib.redirect_stdout(io.StringIO()):
                    app.main()
                self.assertEqual(len(list(Path(temp).glob('*.pdf'))), 2)
                self.assertEqual(len(list(Path(temp).glob('*.xml'))), 2)
                for pdf in Path(temp).glob('*.pdf'):
                    self.assertTrue(pdf.read_bytes().startswith(b'%PDF-'))
                    self.assertGreater(pdf.stat().st_size, 1000)
                self.assertEqual(c.get_invoice_xml.call_count, 2)  # original cache shared by XML/PDF/email
                send = smtp.return_value.__enter__.return_value.sendmail
                self.assertEqual(send.call_count, 2 if group == 'single' else 1)
                attachments = []
                for call in send.call_args_list:
                    message = email.message_from_string(call.args[2])
                    attachments.extend(p for p in message.walk() if p.get_content_disposition() == 'attachment')
                self.assertEqual(len(attachments), 4)
                self.assertEqual(sum(p.get_filename().endswith('.pdf') for p in attachments), 2)
                self.assertEqual(sum(p.get_filename().endswith('.xml') for p in attachments), 2)
                for part in attachments:
                    if part.get_filename().endswith('.xml'):
                        self.assertEqual(part.get_payload(decode=True), XML)
                    else:
                        self.assertTrue(part.get_payload(decode=True).startswith(b'%PDF-'))
                c.terminate_session.assert_called_once()


class RateLimitAndCacheTests(unittest.TestCase):
    @patch('ksef_faktury_list.time.sleep')
    @patch('ksef_faktury_list.requests.get')
    def test_get_invoice_xml_retry_429_success(self, mock_get, mock_sleep):
        client = app.KSeFClient.from_token('token', environment='test')
        client.access_token = 'active-session-token'

        # First 2 calls return 429 (one with Retry-After header), 3rd returns 200
        resp_429_1 = Mock(status_code=429, headers={'Retry-After': '3'})
        resp_429_2 = Mock(status_code=429, headers={})
        resp_200 = Mock(status_code=200, content=b'<xml>ok</xml>')
        mock_get.side_effect = [resp_429_1, resp_429_2, resp_200]

        result = client.get_invoice_xml('1234567890-20260928-010080615740-E4')
        self.assertEqual(result, b'<xml>ok</xml>')
        self.assertEqual(mock_get.call_count, 3)
        # First retry waited 3.0s (from Retry-After), second waited 2.0s (default)
        self.assertEqual(mock_sleep.call_args_list, [
            unittest.mock.call(3.0),
            unittest.mock.call(2.0),
        ])

    @patch('ksef_faktury_list.time.sleep')
    @patch('ksef_faktury_list.requests.get')
    def test_get_invoice_xml_retry_429_exhausted(self, mock_get, mock_sleep):
        client = app.KSeFClient.from_token('token', environment='test')
        client.access_token = 'active-session-token'

        resp_429 = Mock(status_code=429, headers={})
        mock_get.return_value = resp_429

        with self.assertRaises(app.KSeFError) as cm:
            client.get_invoice_xml('1234567890-20260928-010080615740-E4', max_retries=3, retry_delay=2.0)

        self.assertEqual(cm.exception.status_code, 429)
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)  # retried twice after attempts 1 and 2

    @patch('ksef_faktury_list.time.sleep')
    def test_disk_cache_skips_api_call(self, mock_sleep):
        with tempfile.TemporaryDirectory() as temp:
            ksef_nr = '1234567890-20260928-010080615740-E4'
            xml_file = Path(temp) / f"{ksef_nr}.xml"
            xml_file.write_bytes(XML)

            c = app.KSeFClient.from_token('offline', environment='prod')
            c.access_token = 'offline'
            c.init_session_token = Mock(return_value={'reference_number': 'offline'})
            c.terminate_session = Mock()
            c.get_invoice_xml = Mock()
            c._make_request = Mock(return_value={'invoices': [{'ksefNumber': ksef_nr, 'invoiceNumber': 'INV/1'}]})

            argv = ['script', '--nip', '1234567890', '--token', 'offline',
                    '--download-xml', '--xml-output-dir', temp,
                    '--download-pdf', '--pdf-output-dir', temp]

            with patch.object(app.KSeFClient, 'from_token', return_value=c), \
                 patch('sys.argv', argv), \
                 contextlib.redirect_stdout(io.StringIO()):
                app.main()

            # get_invoice_xml should NOT be called at all because file was already on disk
            c.get_invoice_xml.assert_not_called()
            mock_sleep.assert_not_called()
            pdf_path = Path(temp) / f"{ksef_nr}.pdf"
            self.assertTrue(pdf_path.is_file())

    @patch('ksef_faktury_list.time.sleep')
    def test_throttling_on_api_fetch(self, mock_sleep):
        with tempfile.TemporaryDirectory() as temp:
            ksef_nr = '1234567890-20260928-010080615740-E4'
            c = app.KSeFClient.from_token('offline', environment='prod')
            c.access_token = 'offline'
            c.init_session_token = Mock(return_value={'reference_number': 'offline'})
            c.terminate_session = Mock()
            c.get_invoice_xml = Mock(return_value=XML)
            c._make_request = Mock(return_value={'invoices': [{'ksefNumber': ksef_nr, 'invoiceNumber': 'INV/1'}]})

            argv = ['script', '--nip', '1234567890', '--token', 'offline',
                    '--download-xml', '--xml-output-dir', temp]

            with patch.object(app.KSeFClient, 'from_token', return_value=c), \
                 patch('sys.argv', argv), \
                 contextlib.redirect_stdout(io.StringIO()):
                app.main()

            c.get_invoice_xml.assert_called_once_with(ksef_nr)
            mock_sleep.assert_called_once_with(0.3)


class BuyerNipFilterTests(unittest.TestCase):
    def setUp(self):
        self.invoices_data = [
            {
                'ksefNumber': '1234567890-20260928-010080615740-E4',
                'invoiceNumber': 'FV/1',
                'buyer': {'identifier': {'type': 'Nip', 'value': '9876543210'}, 'name': 'Nabywca A'}
            },
            {
                'ksefNumber': '1234567890-20260928-010080615741-E5',
                'invoiceNumber': 'FV/2',
                'buyer': {'nip': '5555555555', 'name': 'Nabywca B'}
            },
            {
                'ksefNumber': '1234567890-20260928-010080615742-E6',
                'invoiceNumber': 'FV/3',
                'buyerIdentifier': '9876543210'
            },
            {
                'ksefNumber': '1234567890-20260928-010080615743-E7',
                'invoiceNumber': 'FV/4',
                'buyer': {'identifier': {'value': 'PL9876543210'}}
            }
        ]

    def test_query_invoices_filters_by_buyer_nip(self):
        client = app.KSeFClient.from_token('token', environment='test')
        client.access_token = 'token'
        client._make_request = Mock(return_value={'invoices': list(self.invoices_data), 'numberOfElements': 4})

        # Match 9876543210 (FV/1, FV/3, FV/4 should match; FV/2 with 5555555555 should not)
        res = client.query_invoices(buyer_nip='987-654-32-10')
        filtered = res['invoices']
        self.assertEqual(len(filtered), 3)
        self.assertEqual(res['numberOfElements'], 3)
        inv_numbers = [i['invoiceNumber'] for i in filtered]
        self.assertIn('FV/1', inv_numbers)
        self.assertIn('FV/3', inv_numbers)
        self.assertIn('FV/4', inv_numbers)
        self.assertNotIn('FV/2', inv_numbers)

    def test_query_invoices_invalid_buyer_nip_raises(self):
        client = app.KSeFClient.from_token('token', environment='test')
        client.access_token = 'token'
        with self.assertRaises(ValueError):
            client.query_invoices(buyer_nip='invalid_nip')
        with self.assertRaises(ValueError):
            client.query_invoices(buyer_nip='12345')

    def test_cli_buyer_nip_switches(self):
        for switch in ('--buyer-nip', '--buyerNip'):
            for nip_input in ('9876543210', '987-654-32-10', 'PL9876543210'):
                with self.subTest(switch=switch, nip_input=nip_input):
                    client = Mock()
                    client.init_session_token.return_value = {'reference_number': 'offline'}
                    client.query_invoices.return_value = {'invoices': []}
                    argv = ['script', '--nip', '1234567890', '--token', 'offline', switch, nip_input]
                    with patch.object(app.KSeFClient, 'from_token', return_value=client), \
                         patch('sys.argv', argv), \
                         contextlib.redirect_stdout(io.StringIO()):
                        app.main()
                    self.assertEqual(client.query_invoices.call_args.kwargs['buyer_nip'], '9876543210')

    def test_cli_invalid_buyer_nip_stops_before_connecting(self):
        with patch.object(app.KSeFClient, 'from_token') as connect, \
             patch('sys.argv', ['script', '--nip', '1234567890', '--token', 'offline', '--buyer-nip', '123']), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                app.main()
            self.assertEqual(raised.exception.code, 2)
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()

