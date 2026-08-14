# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""GaussDB/openGauss authentication for the pure-Python ``pg8000`` driver.

GaussDB replaces PostgreSQL's SASL authentication with a private SHA256
handshake whose auth-code numbers collide with PG's SASL codes, so stock
PostgreSQL drivers fail before the password is even checked. pg8000 is pure
Python — no libpq, no C extension — which makes the handshake patchable and
the driver usable on every platform, including macOS where no GaussDB libpq
build exists.

Two deltas against stock pg8000, both verified against a live openGauss
7.0.0-RC2 and a Huawei-cloud managed GaussDB Kernel 505.2.1:

1. **Startup protocol 3.51** (196659) instead of 3.0 (196608). Under 3.0 the
   server's SHA256 payload omits the PBKDF2 iteration count, so no client can
   derive the key; under 3.51 (the modern openGauss JDBC default) the
   iteration count is explicit.
2. **Auth code 10 means AUTH_REQ_SHA256** on GaussDB, not PG's
   AuthenticationSASL. The client proof is the RFC 5802 scheme below,
   verified byte-for-byte against openGauss JDBC's
   ``MD5Digest.RFC5802Algorithm``.

This module is a self-contained DB-API 2.0 module (SQLAlchemy's
``import_dbapi`` contract): it re-exports pg8000's DB-API surface and swaps
in a GaussDB-aware ``Connection``/``connect``.
"""

import hashlib
import hmac
import struct
import types

import pg8000
import pg8000.core as _core
import pg8000.dbapi as _dbapi
from pg8000.core import NULL_BYTE, PASSWORD, CoreConnection, _flush
from pg8000.dbapi import InterfaceError

# Re-export the full DB-API surface (exceptions, type constructors, module
# constants) so this module is a drop-in ``import_dbapi`` target. pg8000's
# __all__ omits the PEP 249 module globals, so they come from the dbapi
# module explicitly.
globals().update({name: getattr(pg8000, name) for name in pg8000.__all__})
apilevel = _dbapi.apilevel
threadsafety = _dbapi.threadsafety
paramstyle = _dbapi.paramstyle

PROTOCOL_3_0 = 196608
PROTOCOL_3_51 = 196659

# GaussDB password-stored methods (first int32 of the auth code 10 payload).
_METHOD_PLAIN = 0
_METHOD_MD5 = 1
_METHOD_SHA256 = 2
_METHOD_SM3 = 3

_AUTH_HINT = (
    "GaussDB accounts must use an MD5- or SHA256-stored password "
    "(password_encryption_type 0, 1 or 2). If the server's pg_hba method is "
    "'md5' while the account's password is SHA256-only, or the password uses "
    "SM3, re-set the account password under password_encryption_type = 1 or "
    "switch the pg_hba method to 'sha256'."
)


def rfc5802_response(password: bytes, random64_hex: bytes, token_hex: bytes, iteration: int) -> bytes:
    """GaussDB's RFC 5802 client proof, as lowercase-hex ASCII bytes.

    Matches openGauss JDBC's ``MD5Digest.RFC5802Algorithm``: the PBKDF2 salt
    and the token are the *decoded* bytes of their hex strings, and the HMAC
    key direction is K-as-key with the literal strings as data.
    """
    k = hashlib.pbkdf2_hmac("sha1", password, bytes.fromhex(random64_hex.decode("ascii")), iteration, 32)
    client_key = hmac.new(k, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    h = hmac.new(stored_key, bytes.fromhex(token_hex.decode("ascii")), hashlib.sha256).digest()
    return bytes(a ^ b for a, b in zip(h, client_key)).hex().encode("ascii")


def _md5_response(user: bytes, password: bytes, salt: bytes) -> bytes:
    inner = hashlib.md5(password + user).hexdigest().encode("ascii")
    return b"md5" + hashlib.md5(inner + salt).hexdigest().encode("ascii")


def _i_pack_351(value):
    """``i_pack`` that rewrites the startup protocol constant to 3.51.

    Scoped to the cloned ``__init__`` below; 196608 appears in that function
    only as the protocol constant (a startup packet can never be 196608 bytes
    long), so the rewrite is unambiguous.
    """
    return _core.i_pack(PROTOCOL_3_51 if value == PROTOCOL_3_0 else value)


# ``protocol = 196608`` is a hardcoded local inside CoreConnection.__init__ —
# there is no subclass hook. Rather than copying the ~140-line method (which
# would silently drift from upstream), clone the function object with a
# patched ``i_pack`` in its globals. The clone is thread-safe (no module
# state is touched) and survives upstream patch releases as long as the
# ``i_pack(protocol)`` idiom stays; test_pg8000_gauss.py carries a drift
# guard pinning the upstream source. Tracked for removal once pg8000 grows a
# ``protocol_version`` parameter (upstream PR planned).
_init_3_51 = types.FunctionType(
    CoreConnection.__init__.__code__,
    {**vars(_core), "i_pack": _i_pack_351},
    "__init__",
    CoreConnection.__init__.__defaults__,
    CoreConnection.__init__.__closure__,
)


class _GaussCore(CoreConnection):
    """CoreConnection speaking startup 3.51 and the GaussDB SHA256 handshake."""

    __init__ = _init_3_51

    def handle_AUTHENTICATION_REQUEST(self, data, context):
        auth_code = _core.i_unpack(data)[0]
        # PG's AuthenticationSASL is also code 10; its payload is a
        # null-terminated mechanism list ("SCRAM-SHA-256..."), while
        # GaussDB's starts with a binary method int. Delegate the SASL shape
        # so this class stays correct against a real PostgreSQL server.
        if auth_code != 10 or b"SCRAM" in data[4:]:
            if auth_code == 11:
                raise InterfaceError(
                    "GaussDB MD5_SHA256 authentication (code 11) omits the "
                    "PBKDF2 iteration count, so no client can derive the "
                    f"key. {_AUTH_HINT}"
                )
            return super().handle_AUTHENTICATION_REQUEST(data, context)

        if self.password is None:
            raise InterfaceError("server requesting GaussDB SHA256 authentication, but no password was provided")
        payload = data[4:]
        method = struct.unpack("!I", payload[:4])[0]
        body = payload[4:]
        if method in (_METHOD_PLAIN, _METHOD_SHA256):
            random64, token = body[:64], body[64:72]
            iteration = struct.unpack("!I", body[72:76])[0]
            response = rfc5802_response(self.password, random64, token, iteration)
        elif method == _METHOD_MD5:
            response = _md5_response(self.user, self.password, body[:4])
        else:
            raise InterfaceError(f"GaussDB password-stored method {method} is not supported. {_AUTH_HINT}")
        self._send_message(PASSWORD, response + NULL_BYTE)
        _flush(self._sock)


class Connection(_dbapi.Connection, _GaussCore):
    """DB-API 2.0 connection; MRO inserts the GaussDB core under pg8000's.

    ``pg8000.dbapi.Connection.__init__`` delegates straight to
    ``super().__init__``, which resolves to :class:`_GaussCore` here.
    """


def connect(*args, **kwargs):
    return Connection(*args, **kwargs)
