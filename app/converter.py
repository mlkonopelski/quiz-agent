"""Temporal Pydantic data converter.

Single import point for all workers and the starter client.
Pass as `data_converter=pydantic_data_converter` when creating
a Temporal Client or Worker.
"""

from temporalio.contrib.pydantic import pydantic_data_converter

__all__ = ["pydantic_data_converter"]
