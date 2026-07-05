import yagmail

import logging
logger = logging.getLogger(__name__)

SENDER_EMAIL = "nidhiiyadav2k@gmail.com"
SENDER_PASSWORD = "trjw dgwk iikq aoqt"

def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    try:
        yag = yagmail.SMTP(SENDER_EMAIL, SENDER_PASSWORD)
        yag.send(
            to=to_email,
            subject="Password Reset – Portfolio Tracker",
            contents=f"Click the link to reset your password: {reset_url}"
        )
        logger.info(f"[Email] Sent to {to_email}")
        return True
    except Exception as e:
        logger.info(f"[Email] Error: {e}")
        return False