"""Email delivery with a mailer that works without an external service.

Two modes, chosen by configuration:

1. SMTP configured (`MAIL_SERVER` set): real messages are handed to that SMTP
   server. This is genuine email delivery, but it only works when real
   credentials/tools are supplied by the operator.
2. No SMTP configured (the default): messages are appended to
   `instance/mail_outbox.log`. This is a file-based fallback so the flow can be
   tested locally. It is NOT email delivery and the UI/docs say so.

Failures never break a request: they are logged and reported as False.
"""

import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from flask import current_app


def smtp_configured(app=None):
    app = app or current_app
    return bool(app.config.get("MAIL_SERVER"))


def _outbox_path(app):
    configured = app.config.get("MAIL_OUTBOX")
    if configured:
        path = Path(configured)
    else:
        path = Path(app.instance_path) / "mail_outbox.log"

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def send_email(to_address, subject, body, app=None):
    """Deliver one plain-text email.

    Returns (delivered: bool, transport: str) where transport is "smtp" or
    "outbox". Never raises for configuration or network problems.
    """
    app = app or current_app

    if not to_address:
        return False, "none"

    sender = app.config.get("MAIL_DEFAULT_SENDER") or "no-reply@pageturnerbooks.local"

    if smtp_configured(app):
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)

        try:
            host = app.config["MAIL_SERVER"]
            port = int(app.config.get("MAIL_PORT") or 587)
            if app.config.get("MAIL_USE_TLS"):
                client = smtplib.SMTP(host, port, timeout=10)
                client.starttls()
            else:
                client = smtplib.SMTP(host, port, timeout=10)

            username = app.config.get("MAIL_USERNAME")
            password = app.config.get("MAIL_PASSWORD")
            if username and password:
                client.login(username, password)

            client.send_message(message)
            client.quit()
            return True, "smtp"
        except Exception as error:  # noqa: BLE001 - never break the request
            app.logger.warning(
                "SMTP delivery failed (%s); falling back to outbox", error
            )

    # Fallback: write to the local outbox file.
    try:
        entry = (
            f"--- {datetime.now().isoformat(timespec='seconds')} ---\n"
            f"To: {to_address}\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"{body}\n\n"
        )
        with _outbox_path(app).open("a", encoding="utf-8") as handle:
            handle.write(entry)
        return False, "outbox"
    except Exception as error:  # noqa: BLE001
        app.logger.warning("Could not write mail outbox: %s", error)
        return False, "none"


def send_order_confirmation(order, user_email, payment=None, app=None):
    lines = [
        f"Thank you for your order #{order['id']}.",
        "",
        "Your order is recorded as 'Placed'. Payment is simulated in this",
        "project: no gateway was contacted and no money was charged.",
        "",
        f"Total: INR {order['total_paise'] / 100:.2f}",
    ]

    if payment is not None:
        lines.extend(
            [
                f"Payment method: {payment['method']}",
                f"Payment status: {payment['status']}",
                f"Reference: {payment['reference']}",
            ]
        )

    lines.extend(["", "You can view the order in your account."])

    return send_email(
        user_email,
        f"PageTurner Books order #{order['id']} confirmation",
        "\n".join(lines),
        app=app,
    )
