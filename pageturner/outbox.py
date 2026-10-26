"""Requirement 69 — a transactional notification outbox with retry.

Why an outbox at all
--------------------
A notification is a *side effect* of a business event (an order is placed, a
backordered copy ships). Sending it inline is wrong in both directions: if the
send happens first and the transaction then rolls back, the customer is told
about an order that does not exist; if the send happens after the commit and
the process dies in between, the message is lost forever with no record that it
was owed.

So the row is written *in the same transaction as the event* — the caller is
already holding the request's connection and does not commit here. Nothing is
sent during checkout. Delivery is a separate, retryable step:

    enqueue()        write the obligation (same transaction as the event)
    due()            what is owed and payable now
    deliver()        one attempt, counting it, with backoff or give-up
    process_due()    drain the due rows, returning a summary

Guarantees
----------
* At-most-once *per attempt*, at-least-once overall: a crash after the send but
  before the status update replays the message. That is the standard outbox
  trade-off and is why the body is a plain, idempotent message.
* ``attempts`` counts real tries. A failure schedules ``next_attempt_at`` with
  exponential backoff (see :data:`BACKOFF_SECONDS`) so a broken transport is
  not hammered.
* After ``max_attempts`` the row is parked as ``failed`` and never retried
  silently — it is visible on the admin outbox page for a human to fix.
* ``dedupe_key`` is UNIQUE, so the same event enqueued twice (double submit,
  retried request) still produces exactly one message.
"""

from datetime import datetime, timedelta

#: Delay before each retry, indexed by the number of failures so far. Attempt 1
#: failing waits a minute, attempt 2 failing five, then half an hour, two hours
#: and finally six. Anything beyond the end of the table keeps the last value.
BACKOFF_SECONDS = (60, 300, 1800, 7200, 21600)

#: How many times a message is tried before it is given up on.
DEFAULT_MAX_ATTEMPTS = 3

#: Longest error string kept on the row; stack traces are for the log, not the
#: database.
MAX_ERROR_CHARS = 500

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _stamp(moment):
    """Format a datetime the way every other table in this schema stores it."""
    return moment.strftime(_TIMESTAMP_FORMAT)


def _moment(now=None):
    """Normalise ``now`` so tests can move the clock without touching globals."""
    return now or datetime.now()


def backoff_seconds(attempts):
    """Seconds to wait after ``attempts`` consecutive failures.

    ``attempts`` is the number of failures *already recorded*, so after the
    first failure (``1``) the first entry of :data:`BACKOFF_SECONDS` applies.
    """
    attempts = int(attempts)

    if attempts <= 0:
        return 0

    index = min(attempts - 1, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[index]


def enqueue(
    connection,
    user_id,
    subject,
    body,
    recipient=None,
    channel="email",
    dedupe_key=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    now=None,
):
    """Record a message to deliver. Does **not** commit.

    The caller is expected to be inside the transaction of the event that
    caused this message, which is the whole point of the pattern. Returns the
    new row id, or ``None`` when ``dedupe_key`` says it is already queued.
    """
    moment = _moment(now)

    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO notification_outbox(
            user_id, channel, recipient, subject, body, dedupe_key,
            max_attempts, next_attempt_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            channel,
            recipient,
            subject,
            body,
            dedupe_key,
            int(max_attempts),
            _stamp(moment),
            _stamp(moment),
        ),
    )

    if not cursor.rowcount:
        return None

    return cursor.lastrowid


def due(connection, now=None, limit=50):
    """Pending messages whose backoff has elapsed, oldest obligation first."""
    return connection.execute(
        """
        SELECT *
        FROM notification_outbox
        WHERE status = 'pending' AND next_attempt_at <= ?
        ORDER BY next_attempt_at, id
        LIMIT ?
        """,
        (_stamp(_moment(now)), int(limit)),
    ).fetchall()


def default_transport(message):
    """Ship one message.

    There is no SMTP server wired into this deployment, so the built-in
    transport is a log sink: it renders the message into the application log,
    which is a real, inspectable delivery target rather than a silent no-op.
    A deployment with a mail provider replaces it via the ``OUTBOX_TRANSPORT``
    config value (see ``DEPLOYMENT.md``); the transport raises on failure and
    the retry policy then applies.
    """
    import logging

    logging.getLogger("pageturner.outbox").info(
        "outbox delivery to=%s subject=%r body=%r",
        message["recipient"] or f"user {message['user_id']}",
        message["subject"],
        message["body"],
    )


def deliver(connection, message, transport=None, now=None):
    """Make one delivery attempt and record the outcome.

    Returns ``"sent"``, ``"pending"`` (failed, will be retried) or
    ``"failed"`` (given up: ``max_attempts`` exhausted). The caller commits.
    """
    moment = _moment(now)
    transport = transport or default_transport
    attempts = int(message["attempts"] or 0)
    max_attempts = int(message["max_attempts"] or DEFAULT_MAX_ATTEMPTS)

    try:
        transport(message)
    except Exception as error:  # noqa: BLE001 - a transport may raise anything
        attempts += 1
        exhausted = attempts >= max_attempts
        status = "failed" if exhausted else "pending"
        retry_at = moment + timedelta(
            seconds=0 if exhausted else backoff_seconds(attempts)
        )

        connection.execute(
            """
            UPDATE notification_outbox
            SET attempts = ?, status = ?, last_error = ?, next_attempt_at = ?
            WHERE id = ?
            """,
            (
                attempts,
                status,
                f"{type(error).__name__}: {error}"[:MAX_ERROR_CHARS],
                _stamp(retry_at),
                message["id"],
            ),
        )

        return status

    connection.execute(
        """
        UPDATE notification_outbox
        SET attempts = attempts + 1, status = 'sent', sent_at = ?,
            last_error = NULL
        WHERE id = ?
        """,
        (_stamp(moment), message["id"]),
    )

    return "sent"


def process_due(connection, transport=None, now=None, limit=50):
    """Attempt every message that is due now; summarise what happened."""
    summary = {"sent": 0, "retried": 0, "failed": 0}

    for message in due(connection, now=now, limit=limit):
        outcome = deliver(connection, message, transport=transport, now=now)

        if outcome == "sent":
            summary["sent"] += 1
        elif outcome == "failed":
            summary["failed"] += 1
        else:
            summary["retried"] += 1

    return summary


def retry_now(connection, message_id, now=None):
    """Put a failed message back in the queue, keeping its error for the record."""
    changed = connection.execute(
        """
        UPDATE notification_outbox
        SET status = 'pending', next_attempt_at = ?
        WHERE id = ? AND status = 'failed'
        """,
        (_stamp(_moment(now)), message_id),
    ).rowcount

    return changed == 1


def status_counts(connection):
    """Row counts per status, for the admin page."""
    counts = {"pending": 0, "sent": 0, "failed": 0}

    for row in connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM notification_outbox
        GROUP BY status
        """
    ):
        counts[row["status"]] = row["count"]

    return counts
