from dataclasses import dataclass

import py_vapid

from celine.nudging.config.settings import settings


@dataclass
class Vapid:
    private_key: str
    public_key: str
    subject: str

    @property
    def signing_key(self) -> py_vapid.Vapid | str:
        """What to hand `pywebpush.webpush(vapid_private_key=...)`.

        `pywebpush` reads a *string* as base64url of a raw or DER key — never as PEM
        (`py_vapid.Vapid.from_string` strips newlines and base64-decodes). A PEM has to
        arrive as a `py_vapid.Vapid` instance, or every send fails with
        "Could not deserialize key data" — which is what staging did, 12 times out of 12.
        """
        if self.private_key.lstrip().startswith("-----BEGIN"):
            return py_vapid.Vapid.from_pem(self.private_key.encode("utf-8"))
        return self.private_key


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
