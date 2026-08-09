"""CloudWatch metrics via Embedded Metric Format.

An EMF-shaped JSON line written to stdout is parsed by the CloudWatch agent and
turned into a metric — no PutMetricData call, no extra IAM, no latency in the
request path. Dimensions stay low-cardinality on purpose: never put a user id,
document id, or question text in a dimension.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import settings

NAMESPACE = "AgenticRAG"


def emit(
    metrics: dict[str, float],
    *,
    unit: str = "None",
    dimensions: dict[str, str] | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    dims = {"Service": settings.service_name, "Environment": settings.app_env}
    if dimensions:
        dims.update(dimensions)

    doc: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dims.keys())],
                    "Metrics": [{"Name": k, "Unit": unit} for k in metrics],
                }
            ],
        },
        **dims,
        **metrics,
    }
    if properties:
        doc.update(properties)
    sys.stdout.write(json.dumps(doc, default=str) + "\n")
    sys.stdout.flush()


@contextmanager
def timed(
    metric_name: str,
    *,
    dimensions: dict[str, str] | None = None,
    properties: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Time a block and emit its duration in milliseconds, success or failure.

    The yielded dict is merged into the emitted properties, so a caller can
    annotate the measurement from inside the block.
    """
    extra: dict[str, Any] = {}
    started = time.perf_counter()
    outcome = "success"
    try:
        yield extra
    except Exception:
        outcome = "error"
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        props = {**(properties or {}), **extra, "outcome": outcome}
        emit(
            {metric_name: elapsed_ms},
            unit="Milliseconds",
            dimensions={**(dimensions or {}), "Outcome": outcome},
            properties=props,
        )
