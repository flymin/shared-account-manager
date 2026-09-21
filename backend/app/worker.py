import logging
import time
from .db import Session, write_lock
from .domain import reconcile
from .config import cipher
from .email_worker import EmailWorker
from .plugins.tools import tool_catalog

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("account-manager.worker")


def tick():
    with Session.begin() as db:
        write_lock(db)
        reconcile(db)


if __name__ == "__main__":
    tool_catalog()
    cipher()
    email_worker = EmailWorker()
    next_reconcile = 0
    while True:
        try:
            if time.monotonic() >= next_reconcile:
                tick()
                next_reconcile = time.monotonic() + 30
            email_worker.tick()
        except Exception as exc:
            # SQL parameters and credentials must not appear in worker logs.
            log.error("Reconciliation failed (%s); will retry", type(exc).__name__)
        time.sleep(1)
