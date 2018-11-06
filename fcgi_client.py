import socket
from asyncio import Protocol

from struct import Struct

from typing import Optional, Tuple, List, Union, Dict, Type

from retrying import retry

__all__ = [
    'FCGIApp', 'parse_out',
    'FCGIRecord', 'FCGIBytestreamRecord', 'FCGIUnknownManagementRecord', 'FCGIGetValues', 'FCGIGetValuesResult',
    'FCGIUnknownType', 'FCGIBeginRequest', 'FCGIAbortRequest', 'FCGIParams', 'FCGIStdin', 'FCGIStdout', 'FCGIStderr',
    'FCGIData', 'FCGIEndRequest', 'encode_name_value_pairs', 'ProtocolError', 'decode_record', 'decode_name_value_pairs',
    'FastCgiClientProtocol',
]

# Constants from the spec.
# Values for type component of FCGIHeader
FCGI_BEGIN_REQUEST = 1
FCGI_ABORT_REQUEST = 2
FCGI_END_REQUEST = 3
FCGI_PARAMS = 4
FCGI_STDIN = 5
FCGI_STDOUT = 6
FCGI_STDERR = 7
FCGI_DATA = 8
FCGI_GET_VALUES = 9
FCGI_GET_VALUES_RESULT = 10
FCGI_UNKNOWN_TYPE = 11

# Mask for flags component of FCGIBeginRequestBody
FCGI_KEEP_CONN = 1

# Values for role component of FCGIBeginRequestBody
FCGI_RESPONDER = 1
FCGI_AUTHORIZER = 2
FCGI_FILTER = 3

# Values for protocol_status component of FCGIEndRequestBody
FCGI_REQUEST_COMPLETE = 0
FCGI_CANT_MPX_CONN = 1
FCGI_OVERLOADED = 2
FCGI_UNKNOWN_ROLE = 3

FCGI_MAX_CONNS = 'FCGI_MAX_CONNS'
FCGI_MAX_REQS = 'FCGI_MAX_REQS'
FCGI_MPXS_CONNS = 'FCGI_MPXS_CONNS'


class FCGIApp(object):
    def __init__(self, connect=None, host=None, port=None):
        if host is not None:
            assert port is not None
            connect = (host, port)

        self._connect = connect

        # sock = self._get_connection()
        # print self._fcgi_get_values(sock, ['FCGI_MAX_CONNS', 'FCGI_MAX_REQS', 'FCGI_MPXS_CONNS'])
        # sock.close()

    def __call__(self, params: dict, input: bytes = b'', data: bytes = b'') -> Tuple[bytes, bytes]:
        # For sanity's sake, we don't care about FCGI_MPXS_CONN
        # (connection multiplexing). For every request, we obtain a new
        # transport socket, perform the request, then discard the socket.
        # This is, I believe, how mod_fastcgi does things...

        sock = self._get_connection()

        request_id = 1 # random.randrange(0xFF)

        # Begin the request
        begin_rec = FCGIBeginRequest(request_id, FCGI_RESPONDER, 0)
        sock.sendall(begin_rec.encode())

        # TODO: Handle longer values correctly. Currently the limit is 65535 bytes.
        params_rec = FCGIParams(request_id, encode_name_value_pairs(list(params.items())))
        sock.sendall(params_rec.encode())

        params_rec = FCGIParams(request_id, b'')
        sock.sendall(params_rec.encode())

        # TODO: Handle longer values correctly. Currently the limit is 65535 bytes.
        stdin_rec = FCGIStdin(request_id, input)
        sock.sendall(stdin_rec.encode())

        stdin_rec = FCGIStdin(request_id, b'')
        sock.sendall(stdin_rec.encode())

        # TODO: Handle longer values correctly. Currently the limit is 65535 bytes.
        data_rec = FCGIData(request_id, data)
        sock.sendall(data_rec.encode())

        # Main loop. Process FCGI_STDOUT, FCGI_STDERR, FCGI_END_REQUEST
        # records from the application.
        err = b''
        out = b''
        while True:
            record = self._read_packet(sock)
            if isinstance(record, FCGIStdout):
                out += record.content
            elif isinstance(record, FCGIStderr):
                err += record.content
            elif isinstance(record, FCGIEndRequest):
                if record.protocol_status != FCGI_REQUEST_COMPLETE:
                    # something went wrong. PHP-FPM never gives this protocol status!
                    pass
                # TODO: Process appStatus/protocolStatus fields?
                break

        # Done with this transport socket, close it. (FCGI_KEEP_CONN was not
        # set in the FCGI_BEGIN_REQUEST record we sent above. So the
        # application is expected to do the same.)
        sock.close()

        return out, err

    @staticmethod
    def _read_packet(sock: socket.socket):
        """
        Create a new FCGI message from the bytes in the given buffer.

        If successful, the record's data is removed from the byte array.

        :param socket.socket sock: the byte array containing the data
        :return: an instance of FCGIRecord, or ``None`` if there was not enough data

        """
        header = sock.recv(headers_struct.size)
        version, record_type, request_id, content_length, padding_length = headers_struct.unpack_from(header)

        if content_length > 65535:
            raise ProtocolError('Content length %d is more than 64 kilobytes' % content_length)

        if version != 1:
            raise ProtocolError('unexpected protocol version: %d' % header[0])

        try:
            record_class = record_classes[record_type]
        except KeyError:
            if request_id:
                raise ProtocolError('unknown record type: %d' % record_type)
            else:
                return FCGIUnknownManagementRecord(record_type)

        content = sock.recv(content_length + padding_length)

        return record_class.parse(request_id, content[:content_length])

    @retry(wait_exponential_multiplier=1000, wait_exponential_max=10000)
    def _get_connection(self):
        if self._connect is not None:
            # The simple case. Create a socket and connect to the
            # application.
            if isinstance(self._connect, str):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self._connect)
            elif hasattr(socket, 'create_connection'):
                sock = socket.create_connection(self._connect)
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(self._connect)
            return sock

        # To be done when I have more time...
        raise NotImplementedError('Launching and managing FastCGI programs not yet implemented')


def parse_out(result: bytes) -> Tuple[bytes, List[Tuple[bytes, bytes]], bytes]:
    # Parse response headers from FCGI_STDOUT
    status = b'200 OK'
    headers = []
    pos = 0
    while True:
        eol = result.find(b'\n', pos)
        if eol < 0:
            break
        line = result[pos:eol - 1]
        pos = eol + 1

        # strip in case of CR. NB: This will also strip other
        # whitespace...
        line = line.strip()

        # Empty line signifies end of headers
        if not line:
            break

        # TODO: Better error handling
        header, value = line.split(b':', 1)
        header = header.strip().lower()
        value = value.strip()

        if header == b'status':
            # Special handling of Status header
            status = value
            if status.find(b' ') < 0:
                # Append a dummy reason phrase if one was not provided
                status += b' FCGIApp'
        else:
            headers.append((header, value))

    body = result[pos:]

    # Set WSGI status, headers, and return result.
    return status, headers, body


headers_struct = Struct('>BBHHBx')
length4_struct = Struct('>I')


class FCGIRecord(object):
    __slots__ = ('request_id',)

    struct = Struct('')
    record_type = None  # type: int

    def __init__(self, request_id: int):
        self.request_id = request_id

    @classmethod
    def parse(cls, request_id, content):
        fields = cls.struct.unpack(content)
        return cls(request_id, *fields)

    def encode_header(self, content):
        return headers_struct.pack(1, self.record_type, self.request_id, len(content), -len(content) & 7)

    def encode(self):  # pragma: no cover
        raise NotImplementedError

    def __bytes__(self):
        return self.encode()


class FCGIBytestreamRecord(FCGIRecord):
    __slots__ = ('content',)

    def __init__(self, request_id: int, content: bytes):
        super(FCGIBytestreamRecord, self).__init__(request_id)
        self.content = content

    @classmethod
    def parse(cls, request_id, content):
        return cls(request_id, bytes(content))

    def encode(self):
        return self.encode_header(self.content) + self.content + (b'\x00' * (-len(self.content) & 7))


class FCGIUnknownManagementRecord(FCGIRecord):
    def __init__(self, record_type):
        super(FCGIUnknownManagementRecord, self).__init__(0)
        self.record_type = record_type

    def encode(self):
        pass


class FCGIGetValues(FCGIRecord):
    __slots__ = ('keys',)

    record_type = FCGI_GET_VALUES

    def __init__(self, keys):
        super(FCGIGetValues, self).__init__(0)
        self.keys = keys

    @classmethod
    def parse(cls, request_id, content):
        assert request_id == 0
        keys = [key for key, value in decode_name_value_pairs(content)]
        return cls(keys)

    def encode(self):
        pairs = [(key, '') for key in self.keys]
        content = encode_name_value_pairs(pairs)
        return self.encode_header(content) + content + (b'\x00' * (-len(content) & 7))


class FCGIGetValuesResult(FCGIRecord):
    __slots__ = ('values',)

    record_type = FCGI_GET_VALUES_RESULT

    def __init__(self, values):
        super(FCGIGetValuesResult, self).__init__(0)
        self.values = values

    @classmethod
    def parse(cls, request_id, content):
        assert request_id == 0
        values = decode_name_value_pairs(content)
        return cls(values)

    def encode(self):
        content = encode_name_value_pairs(self.values)
        return self.encode_header(content) + content + (b'\x00' * (-len(content) & 7))


class FCGIUnknownType(FCGIRecord):
    __slots__ = ('type',)

    struct = Struct('>B7x')
    record_type = FCGI_UNKNOWN_TYPE

    def __init__(self, type):
        assert type > FCGI_UNKNOWN_TYPE
        super(FCGIUnknownType, self).__init__(0)
        self.type = type

    def encode(self):
        content = self.struct.pack(self.type)
        return self.encode_header(content) + content


class FCGIBeginRequest(FCGIRecord):
    __slots__ = ('role', 'flags')

    struct = Struct('>HB5x')
    record_type = FCGI_BEGIN_REQUEST

    def __init__(self, request_id, role, flags):
        super(FCGIBeginRequest, self).__init__(request_id)
        self.role = role
        self.flags = flags

    def encode(self):
        content = self.struct.pack(self.role, self.flags)
        return self.encode_header(content) + content


class FCGIAbortRequest(FCGIRecord):
    __slots__ = ()

    record_type = FCGI_ABORT_REQUEST

    @classmethod
    def parse(cls, request_id, content):
        return cls(request_id)

    def encode(self):
        return self.encode_header(b'')


class FCGIParams(FCGIBytestreamRecord):
    __slots__ = ()

    record_type = FCGI_PARAMS


class FCGIStdin(FCGIBytestreamRecord):
    __slots__ = ()

    record_type = FCGI_STDIN


class FCGIStdout(FCGIBytestreamRecord):
    __slots__ = ()

    record_type = FCGI_STDOUT


class FCGIStderr(FCGIBytestreamRecord):
    __slots__ = ()

    record_type = FCGI_STDERR


class FCGIData(FCGIBytestreamRecord):
    __slots__ = ()

    record_type = FCGI_DATA


class FCGIEndRequest(FCGIRecord):
    __slots__ = ('app_status', 'protocol_status')

    struct = Struct('>IB3x')
    record_type = FCGI_END_REQUEST

    def __init__(self, request_id, app_status, protocol_status):
        super(FCGIEndRequest, self).__init__(request_id)
        self.app_status = app_status
        self.protocol_status = protocol_status

    def encode(self):
        content = self.struct.pack(self.app_status, self.protocol_status)
        return self.encode_header(content) + content


record_classes: Dict[int, Type[FCGIRecord]] = {cls.record_type: cls for cls in globals().values()
                  if isinstance(cls, type) and issubclass(cls, FCGIRecord)
                  and cls.record_type}


def decode_name_value_pairs(buffer: bytearray) -> List[Tuple[str, str]]:
    """
    Decode a name-value pair list from a buffer.

    :param bytearray buffer: a buffer containing a FastCGI name-value pair list
    :raise ProtocolError: if the buffer contains incomplete data
    :return: a list of (name, value) tuples where both elements are unicode strings
    :rtype: list

    """
    index = 0
    pairs = []
    while index < len(buffer):
        if buffer[index] & 0x80 == 0:
            name_length = buffer[index]
            index += 1
        elif len(buffer) - index > 4:
            name_length = length4_struct.unpack_from(buffer, index)[0] & 0x7fffffff
            index += 4
        else:
            raise ProtocolError('not enough data to decode name length in name-value pair')

        if len(buffer) - index > 1 and buffer[index] & 0x80 == 0:
            value_length = buffer[index]
            index += 1
        elif len(buffer) - index > 4:
            value_length = length4_struct.unpack_from(buffer, index)[0] & 0x7fffffff
            index += 4
        else:
            raise ProtocolError('not enough data to decode value length in name-value pair')

        if len(buffer) - index >= name_length + value_length:
            name = buffer[index:index + name_length].decode('ascii')
            value = buffer[index + name_length:index + name_length + value_length].decode('utf-8')
            pairs.append((name, value))
            index += name_length + value_length
        else:
            raise ProtocolError('name/value data missing from buffer')

    return pairs


def encode_name_value_pairs(pairs: List[Tuple[Union[str, bytes], Union[str, bytes]]]) -> bytes:
    """
    Encode a list of name-pair values into a binary form that FCGI understands.

    Both names and values can be either unicode strings or bytestrings and will be converted to
    bytestrings as necessary.

    :param list pairs: list of name-value pairs
    :return: the encoded bytestring

    """
    content = bytearray()
    for name, value in pairs:
        name = name if isinstance(name, bytes) else name.encode('ascii')
        value = value if isinstance(value, bytes) else value.encode('ascii')
        for item in (name, value):
            if len(item) < 128:
                content.append(len(item))
            else:
                length = len(item)
                content.extend(length4_struct.pack(length | 0x80000000))

        content.extend(name)
        content.extend(value)

    return bytes(content)


def decode_record(buffer: bytearray) -> Optional[FCGIRecord]:
    """
    Create a new FCGI message from the bytes in the given buffer.
    If successful, the record's data is removed from the byte array.
    :param bytearray buffer: the byte array containing the data
    :return: an instance of this class, or ``None`` if there was not enough data
    """
    if len(buffer) >= headers_struct.size:
        version, record_type, request_id, content_length, padding_length = \
            headers_struct.unpack_from(buffer)
        if version != 1:
            raise ProtocolError('unexpected protocol version: %d' % buffer[0])
        elif len(buffer) >= headers_struct.size + content_length + padding_length:
            content = buffer[headers_struct.size:headers_struct.size + content_length]
            del buffer[:headers_struct.size + content_length + padding_length]
            try:
                record_class = record_classes[record_type]
            except KeyError:
                if request_id:
                    raise ProtocolError('unknown record type: %d' % record_type)
                else:
                    return FCGIUnknownManagementRecord(record_type)

            return record_class.parse(request_id, content)

    return None


def decode_buffer_generator(buffer: bytearray):
    yield decode_record(buffer)


class ProtocolError(Exception):
    """Raised when the FastCGI protocol is violated."""

    def __init__(self, message):
        super(ProtocolError, self).__init__('FastCGI protocol violation: %s' % message)


class FastCgiClientProtocol(Protocol):
    def __init__(self, request_id: int, params: dict, input: bytes, data: bytes, loop):
        self.request_id = request_id
        self.params = params
        self.input = input
        self.data = data
        self.loop = loop
        self.buffer = bytearray()
        self.stdout = b''
        self.stderr = b''

    def connection_made(self, transport):
        # Begin the request
        begin_rec = FCGIBeginRequest(self.request_id, FCGI_RESPONDER, FCGI_KEEP_CONN)
        transport.write(begin_rec.encode())

        # TODO: Handle longer values correctly. Currently the limit is 65535 bytes.
        params_rec = FCGIParams(self.request_id, encode_name_value_pairs(list(self.params.items())))
        transport.write(params_rec.encode())

        params_rec = FCGIParams(self.request_id, b'')
        transport.write(params_rec.encode())

        # TODO: Handle longer values correctly. Currently the limit is 65535 bytes.
        stdin_rec = FCGIStdin(self.request_id, self.input)
        transport.write(stdin_rec.encode())

        stdin_rec = FCGIStdin(self.request_id, b'')
        transport.write(stdin_rec.encode())

        # TODO: Handle longer values correctly. Currently the limit is 65535 bytes.
        data_rec = FCGIData(self.request_id, self.data)
        transport.write(data_rec.encode())

        data_rec = FCGIData(self.request_id, b'')
        transport.write(data_rec.encode())

    def connection_lost(self, exc):
        print('The server closed the connection')
        print('Stop the event loop')
        self.loop.stop()

    def data_received(self, data):
        self.buffer.extend(data)
        record = decode_record(self.buffer)
        while record is not None:
            if isinstance(record, FCGIStdout):
                self.stdout += record.content
            elif isinstance(record, FCGIStderr):
                self.stderr += record.content
            elif isinstance(record, FCGIEndRequest):
                if record.protocol_status != FCGI_REQUEST_COMPLETE:
                    # something went wrong. PHP-FPM never gives this protocol status!
                    pass
                self.loop.stop()
            record = decode_record(self.buffer)

    def eof_received(self):
        pass
