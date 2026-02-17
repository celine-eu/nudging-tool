from __future__ import annotations

from typing import Dict

from celine.nudging.orchestrator.models import Channel
from celine.nudging.publishers.base import Publisher
from celine.nudging.publishers.web.worker import WebPublisher

_PUBLISHERS: Dict[Channel, Publisher] = {
    Channel.web: WebPublisher(),
}


def get_publisher(channel: Channel) -> Publisher:
    try:
        return _PUBLISHERS[channel]
    except KeyError as e:
        raise ValueError(f"No publisher registered for channel={channel}") from e
