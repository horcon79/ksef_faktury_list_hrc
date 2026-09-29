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


if __name__ == '__main__':
    unittest.main()
