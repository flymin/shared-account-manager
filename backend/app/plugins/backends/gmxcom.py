"""GMX webmail adapter using the shared 1&1 mailbox protocol."""

from .mailcom import MailComClient


class GMXComClient(MailComClient):
    home_url = "https://www.gmx.com/"
    provider_domains = ("gmx.com",)
