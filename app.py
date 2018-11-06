import subprocess
import atexit
import os
import logging
import sys
import urllib.parse
import cgi
from typing import Dict, List, Tuple, Any

from fcgi_client import *

logger = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.propagate = False
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

php_fpm = subprocess.Popen([os.environ['LAMBDA_TASK_ROOT'] + '/php-fpm/sbin/php-fpm',
                            '--force-stderr',
                            '-c', os.environ['LAMBDA_TASK_ROOT'] + '/php-fpm/etc/php.ini',
                            '-d', 'extension_dir=' + os.environ['LAMBDA_TASK_ROOT'] + '/php-fpm/ext',
                            '--prefix', os.environ['LAMBDA_TASK_ROOT'] + '/php-fpm',
                            '--fpm-config', os.environ['LAMBDA_TASK_ROOT'] + '/php-fpm/etc/php-fpm.conf'
                            ])


def shutdown_php_fpm():
    php_fpm.terminate()


atexit.register(shutdown_php_fpm)
app = FCGIApp(connect='/tmp/fpm.sock')


def main(event: dict, context) -> Dict[str, Any]:
    # logger.info(event)

    out, err = app(*make_fcgi_params_and_input_from_event(event))
    if len(err) > 0:
        logger.error(str(err, 'ascii'))

    status, headers, body = parse_out(out)
    status = str(status, 'ascii')

    return {
        'statusCode': int(status.split(None, 2)[0]),
        'headers': {str(k, 'ascii'): str(v, 'ascii') for k, v in headers},
        'multiValueHeaders': {},
        'body': str(body, charset_from_response(headers))
    }


def transform_header_name_for_php(k: str) -> str:
    """

    :param k: Header name
    :return: Header name capitalized with dashes replaced by underscores.
    """
    key = k.upper().replace('-', '_')
    if not (key == 'CONTENT_TYPE' or key == 'CONTENT_LENGTH'):
        key = 'HTTP_' + key
    return key


def charset_from_response(headers: List[Tuple[bytes, bytes]]):
    charsets = [cgi.parse_header(str(header[1], 'ascii')) for header in headers if header[0] == b'content-type']
    if charsets:
        return charsets[0][1]['charset']
    return 'iso-8859-1'


def charset_from_event(event: dict) -> str:
    charsets = [cgi.parse_header(event['headers'].get('Content-Type', 'text/html;charset=iso-8859-1'))]
    if charsets:
        return charsets[0][1]['charset']
    return 'iso-8859-1'


def query_string(event: dict) -> str:
    query_string_parameters = event.get('queryStringParameters', {})
    if query_string_parameters:
        return urllib.parse.urlencode(query_string_parameters)
    return ''


def make_fcgi_params_and_input_from_event(event: dict):
    if event['path'] == '/ping' or event['path'] == '/status':
        script_filename = event['path']
    elif os.path.isfile(os.environ['LAMBDA_TASK_ROOT'] + '/php/public' + event['path']):
        script_filename = os.environ['LAMBDA_TASK_ROOT'] + '/php/public' + event['path']
    else:
        script_filename = os.environ['LAMBDA_TASK_ROOT'] + '/php/public/index.php' + event['path']

    params: Dict[str, str] = {transform_header_name_for_php(k): v for k, v in event['headers'].items()}
    params['SCRIPT_NAME'] = event['path']
    params['SCRIPT_FILENAME'] = script_filename
    params['REQUEST_METHOD'] = event['httpMethod']
    params['QUERY_STRING'] = query_string(event)

    input = b''
    if event['body'] is not None:
        input = bytes(event['body'], charset_from_event(event))
        params['CONTENT_LENGTH'] = str(len(input))

    return params, input
