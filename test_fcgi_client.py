import unittest
import unittest.mock
from parameterized import parameterized

from fcgi_client import *


class ClientTestCase(unittest.TestCase):
    def test_call(self):
        mock_socket = unittest.mock.Mock()

        sendall_calls = [
            unittest.mock.call(b'\x01\x01\x00\x01\x00\x08\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00'),
            unittest.mock.call(b'\x01\x04\x00\x01\x00\x16\x02\x00\x0f\x05SCRIPT_FILENAME/ping\x00\x00'),
            unittest.mock.call(b'\x01\x04\x00\x01\x00\x00\x00\x00'),
            unittest.mock.call(b'\x01\x05\x00\x01\x00\x00\x00\x00'),
        ]

        mock_socket.recv.side_effect = [
            b'\x01\x06\x00\x01\x00\xaf\x01\x00', b'pong',
            b'\x01\x07\x00\x01\x00\xaf\x01\x00', b'err',
            b'\x01\x03\x00\x01\x00\x08\x00\x00', b'\x00\x00\x00\x00\x00\x00\x00\x00'
        ]

        with unittest.mock.patch.object(FCGIApp, '_get_connection', return_value=mock_socket) as mock_client:
            client = FCGIApp()
            out, err = client({
                'SCRIPT_FILENAME': '/ping',
            })

        mock_socket.sendall.assert_has_calls(sendall_calls)
        self.assertEqual(out, b'pong')
        self.assertEqual(err, b'err')
        self.assertEqual(0, len(list(mock_socket.recv.side_effect)))


class FCGIStdoutTestCase(unittest.TestCase):
    def test_encode_simple_record(self):
        record = FCGIStdout(5, b'data')
        self.assertEqual(b'\x01\x06\x00\x05\x00\x04\x04\x00data\x00\x00\x00\x00', record.encode())
        self.assertEqual(0, len(record.encode()) % 8)


class FCGIGetValuesTestCase(unittest.TestCase):
    def test_parse(self):
        buffer = bytearray(b'\x03\x00FOO\x03\x00BAR')
        record = FCGIGetValues.parse(0, buffer)
        self.assertEqual(['FOO', 'BAR'], record.keys)

    def test_encode(self):
        keys = ['FOO', 'BAR']
        record = FCGIGetValues(keys)
        self.assertEqual(b'\x01\x09\x00\x00\x00\x0a\x06\x00\x03\x00FOO\x03\x00BAR\x00\x00\x00\x00\x00\x00',
                         record.encode())
        self.assertEqual(0, len(record.encode()) % 8)


class FCGIGetValuesResultTestCase(unittest.TestCase):
    def test_parse(self):
        buffer = bytearray(b'\x03\x03FOOabc\x03\x03BARxyz')
        record = FCGIGetValuesResult.parse(0, buffer)
        self.assertEqual([('FOO', 'abc'), ('BAR', 'xyz')], record.values)

    def test_encode(self):
        values = [('FOO', 'abc'), ('BAR', 'xyz')]
        record = FCGIGetValuesResult(values)
        self.assertEqual(b'\x01\x0a\x00\x00\x00\x10\x00\x00\x03\x03FOOabc\x03\x03BARxyz', record.encode())
        self.assertEqual(0, len(record.encode()) % 8)


class FCGIBeginRequestTestCase(unittest.TestCase):
    def test_parse(self):
        buffer = bytearray(b'\x00\x01\x01\x00\x00\x00\x00\x00')
        record = FCGIBeginRequest.parse(5, buffer)
        self.assertEqual(5, record.request_id)
        self.assertEqual(1, record.role)
        self.assertEqual(1, record.flags)

    def test_encode(self):
        record = FCGIBeginRequest(5, 1, 1)
        self.assertEqual(b'\x01\x01\x00\x05\x00\x08\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00', record.encode())
        self.assertEqual(0, len(record.encode()) % 8)


class FCGIAbortRequestTestCase(unittest.TestCase):
    def test_parse(self):
        buffer = bytearray(b'')
        record = FCGIAbortRequest.parse(5, buffer)
        self.assertEqual(5, record.request_id)

    def test_encode(self):
        record = FCGIAbortRequest(5)
        self.assertEqual(b'\x01\x02\x00\x05\x00\x00\x00\x00', record.encode())
        self.assertEqual(0, len(record.encode()) % 8)


class FCGIEndRequestTestCase(unittest.TestCase):
    def test_parse(self):
        buffer = bytearray(b'\x00\x01\x00\x01\x02\x00\x00\x00')
        record = FCGIEndRequest.parse(5, buffer)
        self.assertEqual(5, record.request_id)
        self.assertEqual(65537, record.app_status)
        self.assertEqual(2, record.protocol_status)

    def test_encode(self):
        record = FCGIEndRequest(5, 65537, 2)
        self.assertEqual(b'\x01\x03\x00\x05\x00\x08\x00\x00\x00\x01\x00\x01\x02\x00\x00\x00', record.encode())
        self.assertEqual(0, len(record.encode()) % 8)


class FCGIUnknownTypeTestCase(unittest.TestCase):
    def test_encode(self):
        record = FCGIUnknownType(12)
        self.assertEqual(b'\x01\x0b\x00\x00\x00\x08\x00\x00\x0c\x00\x00\x00\x00\x00\x00\x00', record.encode())
        self.assertEqual(0, len(record.encode()) % 8)


class DecodeNameValuePairsTestCase(unittest.TestCase):
    @parameterized.expand([
        ('short_both', b'\x03\x06foobarbar\x01\x03Xxyz', [(u'foo', u'barbar'), ('X', 'xyz')]),
        ('long_value', b'\x03\x80\x01\x00\x00foo' + b'x' * 65536, [(u'foo', u'x' * 65536)]),
        ('long_name', b'\x80\x01\x00\x00\x03' + b'x' * 65536 + b'foo', [(u'x' * 65536, 'foo')]),
        ('long_both', b'\x80\x01\x00\x00\x80\x01\x00\x00' + b'x' * 65536 + b'y' * 65536,
         [('x' * 65536, 'y' * 65536)])
    ])
    def test_decode_name_value_pairs(self, name, data, expected):
        buffer = bytearray(data)
        assert decode_name_value_pairs(buffer) == expected

    @parameterized.expand([
        ('name_missing', b'\x80\x00\x00', 'not enough data to decode name length in name-value pair'),
        ('value_missing', b'\x03', 'not enough data to decode value length in name-value pair'),
        ('content_missing', b'\x03\x06foo', 'name/value data missing from buffer')
    ])
    def test_decode_name_value_pairs_incomplete(self, name, data, message):
        buffer = bytearray(data)
        with self.assertRaises(ProtocolError) as cm:
            decode_name_value_pairs(buffer)

        self.assertIn(message, cm.exception.args[0])


class EncodeNameValuePairsTestCase(unittest.TestCase):
    @parameterized.expand([
        ('short_both', [(u'foo', u'barbar'), (u'X', u'xyz')], b'\x03\x06foobarbar\x01\x03Xxyz'),
        ('long_value', [(u'foo', u'x' * 65536)], b'\x03\x80\x01\x00\x00foo' + b'x' * 65536),
        ('long_name', [(u'x' * 65536, u'foo')], b'\x80\x01\x00\x00\x03' + b'x' * 65536 + b'foo'),
        ('long_both', [(u'x' * 65536, u'y' * 65536)],
         b'\x80\x01\x00\x00\x80\x01\x00\x00' + b'x' * 65536 + b'y' * 65536)
    ])
    def test_encode_name_value_pairs(self, name, pairs, expected):
        self.assertEqual(expected, encode_name_value_pairs(pairs))


class DecodeRecordTestCase(unittest.TestCase):
    def test_decode_record(self):
        buffer = bytearray(b'\x01\x05\x00\x01\x00\x07\x00\x00content')
        record = decode_record(buffer)
        self.assertIsInstance(record, FCGIStdin)
        self.assertEqual(1, record.request_id)
        self.assertEqual(b'content', record.content)

    def test_decode_record_incomplete(self):
        buffer = bytearray(b'\x01\x05\x00\x01\x00\x07\x00\x00conten')
        self.assertIsNone(decode_record(buffer))

    def test_decode_record_wrong_version(self):
        buffer = bytearray(b'\x02\x01\x00\x01\x00\x00\x00\x00')

        with self.assertRaises(ProtocolError) as cm:
            decode_record(buffer)

        self.assertIn('unexpected protocol version: 2', cm.exception.args[0])

    def test_decode_unknown_record_type(self):
        buffer = bytearray(b'\x01\x0c\x01\x00\x00\x00\x00\x00')
        with self.assertRaises(ProtocolError) as cm:
            decode_record(buffer)

        self.assertIn('unknown record type: 12', cm.exception.args[0])


class ParseOutputTestCase(unittest.TestCase):
    def test_parse_out(self):
        out = b'Status: 200 OK\r\nContent-type: text/plain;charset=UTF-8\r\n\r\npong'
        self.assertEqual((b'200 OK', [(b'content-type', b'text/plain;charset=UTF-8')], b'pong'), parse_out(out))

    def test_parse_out_no_status(self):
        out = b'Content-type: text/plain;charset=UTF-8\r\n\r\npong'
        self.assertEqual((b'200 OK', [(b'content-type', b'text/plain;charset=UTF-8')], b'pong'), parse_out(out))

    def test_parse_out_no_reason(self):
        out = b'Status: 999\r\nContent-type: text/plain;charset=UTF-8\r\n\r\npong'
        self.assertEqual((b'999 FCGIApp', [(b'content-type', b'text/plain;charset=UTF-8')], b'pong'), parse_out(out))


if __name__ == '__main__':
    unittest.main()
