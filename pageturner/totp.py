
"""Time-based one-time passwords (requirement 71), standard library only.

Implements RFC 4226 HOTP and RFC 6238 TOTP with the parameters every
authenticator app defaults to: HMAC-SHA1, 6 digits, 30 second step.  Nothing
here touches the database or Flask, so the algorithm can be tested directly
against the RFC test vectors.

Two things matter for security and are easy to get wrong:

* comparison happens with :func:`hmac.compare_digest`, never ``==``;
* a code is only useful once, which is the caller's job - see
  :func:`claim_step`, which relies on a UNIQUE(user_id, time_step) constraint
  rather than on a read-then-write check that two concurrent requests could
  both pass.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


DIGITS = 6
STEP_SECONDS = 30
SECRET_BYTES = 20

# Accept the neighbouring steps so a slightly fast or slow phone clock does not
# lock a legitimate user out.  One step either side is the conventional choice.
DRIFT_STEPS = 1


def generate_secret():
    """Return a fresh base32 secret (160 bits, no padding)."""
    return (
        base64.b32encode(secrets.token_bytes(SECRET_BYTES))
        .decode("ascii")
        .rstrip("=")
    )


def normalise_secret(secret):
    return (secret or "").strip().replace(" ", "").upper()


def _key(secret):
    normalised = normalise_secret(secret)

    if not normalised:
        raise ValueError("An empty TOTP secret cannot be used.")

    # Restore the padding b32decode insists on.
    padding = "=" * (-len(normalised) % 8)

    return base64.b32decode(normalised + padding)


def counter_for(moment=None):
    """Return the time step that contains ``moment`` (epoch seconds)."""
    if moment is None:
        moment = time.time()

    return int(moment // STEP_SECONDS)


def hotp(secret, counter, digits=DIGITS):
    """RFC 4226 HOTP value for an explicit counter."""
    digest = hmac.new(
        _key(secret), struct.pack(">Q", int(counter)), hashlib.sha1
    ).digest()

    # Dynamic truncation: the low nibble of the last byte picks the offset.
    offset = digest[-1] & 0x0F
    chunk = digest[offset:offset + 4]
    value = struct.unpack(">I", chunk)[0] & 0x7FFFFFFF

    return str(value % (10 ** digits)).zfill(digits)


def code_for(secret, moment=None, digits=DIGITS):
    """The code a user's authenticator would show at ``moment``."""
    return hotp(secret, counter_for(moment), digits)


def matching_step(secret, code, moment=None, drift=DRIFT_STEPS, digits=DIGITS):
    """Return the accepted time step, or ``None`` when ``code`` is wrong.

    The shape check comes first (a non-numeric or wrong-length code can never
    match), then every candidate step inside the drift window is compared.  The
    loop deliberately does not return early so its duration does not reveal
    *which* step matched.
    """
    submitted = str(code or "").strip().replace(" ", "")

    if not submitted.isdigit() or len(submitted) != digits:
        return None

    current = counter_for(moment)
    found = None

    for step in range(current - drift, current + drift + 1):
        if hmac.compare_digest(hotp(secret, step, digits), submitted):
            found = step

    return found


def provisioning_uri(secret, account, issuer="PageTurner Books"):
    """otpauth:// URI for the QR code / manual entry in authenticator apps."""
    label = quote(f"{issuer}:{account or 'account'}")

    return (
        f"otpauth://totp/{label}"
        f"?secret={normalise_secret(secret)}"
        f"&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def claim_step(connection, user_id, step):
    """Redeem ``step`` for ``user_id`` exactly once.

    Returns ``True`` the first time and ``False`` on any replay.  The UNIQUE
    constraint does the work, so two simultaneous requests cannot both consume
    the same code.  The row is committed by the caller (or rolled back with the
    rest of the login transaction).
    """
    if step is None:
        return False

    try:
        connection.execute(
            "INSERT INTO totp_used_codes(user_id, time_step) VALUES (?, ?)",
            (user_id, step),
        )
    except Exception:
        # IntegrityError: this code was already used.  Any other failure is
        # surfaced by the caller's rollback, so re-raising is unnecessary here.
        return False

    return True
