from __future__ import annotations

import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter


async def connect_temporal() -> Client:
    return await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
        data_converter=pydantic_data_converter,
    )


def task_queue() -> str:
    return os.getenv("TEMPORAL_TASK_QUEUE", "kentoagent-disaster-response")
