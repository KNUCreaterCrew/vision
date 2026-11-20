"""Send synthetic seat metadata to the loopback receiver; no camera or model."""

import argparse
import os
from time import monotonic

from mock_backend import DEVELOPMENT_KEY
from zeroframe.state import Reason, SeatState, Status
from zeroframe.transport import StatusPublisher, TransportConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    config = TransportConfig(f"http://127.0.0.1:{args.port}", os.environ.get("EDGE_NODE_SECRET_KEY", DEVELOPMENT_KEY))
    started_at = monotonic()
    initial = {"A-01": SeatState(Status.UNKNOWN, started_at, reason=Reason.STALE_OBSERVATION)}
    publisher = StatusPublisher(config, "IT4-101", initial, started_at=started_at)
    print("Synthetic metadata demo: no camera/model; sends only to the local receiver.")
    last_observed_at = None
    try:
        for status in (Status.UNKNOWN, Status.OCCUPIED, Status.AWAY, Status.UNKNOWN):
            now = monotonic()
            if status != Status.UNKNOWN:
                last_observed_at = now
            reason = (Reason.STALE_OBSERVATION if last_observed_at is None else Reason.OCCLUDED) if status == Status.UNKNOWN else None
            state = SeatState(status, now, last_observed_at, reason)
            publisher.update({"A-01": state}, inference_count=0)
            if not publisher.send_due(now):
                print("Start the local receiver first: python mock_backend.py")
                return 1
            print(f"Sent synthetic A-01={status.value}")
    finally:
        publisher.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
