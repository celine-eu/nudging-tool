from __future__ import annotations

from typing import Dict

from orchestrator.models import Channel
from publishers.base import Publisher
from publishers.web.worker import WebPublisher


_PUBLISHERS: Dict[Channel, Publisher] = {
    Channel.web: WebPublisher(),
}


def get_publisher(channel: Channel) -> Publisher:
    try:
        return _PUBLISHERS[channel]
    except KeyError as e:
        raise ValueError(f"No publisher registered for channel={channel}") from e
