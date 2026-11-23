"""Sends a reader their list by email, through Resend. Only ever to the signed-in reader's own address."""
import httpx

SENDER = "whatisupnyc <list@whatisupnyc.com>"


def send_list(http: httpx.Client, api_key: str, to: str, subject: str, html: str) -> bool:
    """True when Resend accepted the email."""
    page = f'<!doctype html><html><body style="margin: 0; padding: 16px 0; background: #ffffff;">{html}</body></html>'
    try:
        response = http.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"from": SENDER, "to": [to], "subject": subject, "html": page},
            timeout=20,
        )
    except httpx.HTTPError:
        return False
    return response.is_success
