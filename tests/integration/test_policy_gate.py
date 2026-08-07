import pytest

from dkg.agents.base import Task
from dkg.agents.coordinator import Coordinator
from dkg.core.errors import PolicyError


def test_web_ingest_denied_when_network_disabled(db, cfg):
    # network is disabled by default in cfg
    coord = Coordinator(db, cfg=cfg)
    with pytest.raises(PolicyError):
        coord.submit(Task(kind="ingest.web", input={"url": "https://example.com/"})).result()
