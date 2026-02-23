from dataclasses import dataclass

from celine.nudging.config.settings import settings


@dataclass
class Vapid:
    private_key: str
    public_key: str
    subject: str


def get_vapid() -> Vapid:

    private_key = settings.VAPID_PRIVATE_KEY.strip()
    public_key = settings.VAPID_PUBLIC_KEY.strip()
    subject = settings.VAPID_SUBJECT.strip()

    if not private_key or not public_key or not subject:
        raise ValueError(
            "VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY and VAPID_SUBJECT are required"
        )

    # se la PEM è stata messa con \n letterali
    if "\\n" in private_key:
        private_key = private_key.replace("\\n", "\n")

    return Vapid(
        private_key=private_key,
        public_key=public_key,
        subject=subject,
    )
