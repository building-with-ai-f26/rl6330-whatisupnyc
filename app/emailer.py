"""Sends a reader their list by email, through Resend. Only ever to the signed-in reader's own address."""
import httpx

SENDER = "whatisupnyc <list@whatisupnyc.com>"


def send_list(http: httpx.Client, api_key: str, to: str, subject: str, html: str, once_key: str = "") -> bool:
    """True when Resend accepted the email. once_key, when given, stops a repeat of the same email."""
    page = f'<!doctype html><html><body style="margin: 0; padding: 16px 0; background: #ffffff;">{html}</body></html>'
    try:
        response = http.post(
            "https://api.resend.com/emails",
            # Resend drops a second email with the same key, so a retried send never arrives twice.
            headers={"Authorization": f"Bearer {api_key}", **({"Idempotency-Key": once_key} if once_key else {})},
            json={"from": SENDER, "to": [to], "subject": subject, "html": page},
            timeout=20,
        )
    except httpx.HTTPError:
        return False
    return response.is_success
