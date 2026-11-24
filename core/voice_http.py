"""Direct, bounded HTTP requests to the owner's configured speech endpoint.

No ambient proxy, global urllib opener or redirect can select another recipient.
This is an explicit integration, not the public-URL workspace fetch surface.
"""
import http.client
import math
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class VoiceHTTPError(Exception):
    def __init__(self, message='The configured voice service returned an invalid response.', *, status=None):
        super().__init__(message)
        self.status = status


class VoiceHTTPUnavailable(VoiceHTTPError):
    def __init__(self, *, timed_out=False):
        super().__init__('The configured voice service could not be reached.')
        self.timed_out = timed_out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


JSON_ENDPOINTS = {'/api/info', '/api/health', '/api/voices'}
AUDIO_ENDPOINTS = {'/api/speak', '/v1/audio/speech'}


def is_loopback(url):
    try:
        return urlsplit(url).hostname in {'127.0.0.1', 'localhost', '::1'}
    except ValueError:
        return False


def read(base_url, endpoint, *, timeout, limit, data=None):
    """Read at most limit bytes; refuse redirects, errors and oversized output.

HTTP(S) base paths are supported. Credentials, query and fragment components
are refused. Explicit remote endpoints remain possible; this helper does not
confine what that provider keeps.
"""
    if (type(timeout) not in {float, int} or not math.isfinite(timeout) or timeout <= 0
            or type(limit) is not int or limit < 1):
        raise VoiceHTTPError('Invalid voice-service request limits.')
    if (not isinstance(base_url, str) or not base_url
            or any(ord(char) <= 32 or ord(char) == 127 for char in base_url)
            or any(char in base_url for char in ('\\', '?', '#'))):
        raise VoiceHTTPError('Invalid configured voice-service URL.')
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or port == 0):
            raise ValueError()
    except ValueError:
        raise VoiceHTTPError('Invalid configured voice-service URL.') from None
    if endpoint not in JSON_ENDPOINTS | AUDIO_ENDPOINTS:
        raise VoiceHTTPError('Invalid voice-service endpoint.')
    request = urllib.request.Request(base_url.rstrip('/') + endpoint, data=data,
        headers={'Accept': 'application/json' if endpoint in JSON_ENDPOINTS else 'audio/wav',
                 **({'Content-Type': 'application/json'} if data is not None else {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise VoiceHTTPError(status=response.status)
            length = response.headers.get('Content-Length')
            if length is not None:
                try:
                    size = int(length)
                except ValueError:
                    raise VoiceHTTPError() from None
                if size < 0 or size > limit:
                    raise VoiceHTTPError('The configured voice-service response exceeds its size limit.')
            result = response.read(limit + 1)
            if len(result) > limit:
                raise VoiceHTTPError('The configured voice-service response exceeds its size limit.')
            if length is not None and len(result) != size:
                raise VoiceHTTPError()
            return result
    except urllib.error.HTTPError as error:
        # Do not read or expose provider error bodies, redirect locations or URLs.
        status = error.code
        error.close()
        raise VoiceHTTPError(f'The configured voice service returned HTTP {status}.', status=status) from None
    except (urllib.error.URLError, OSError) as error:
        raise VoiceHTTPUnavailable(timed_out=isinstance(getattr(error, 'reason', error), TimeoutError)) from None
    except (http.client.HTTPException, ValueError, UnicodeError):
        raise VoiceHTTPError() from None
