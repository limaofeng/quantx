"""
Strategy configuration schemas.

This module contains Pydantic-based configuration models for strategy parameters,
providing type-safe validation and documentation.
"""

from quantx_domain.strategies.config.ashare_supermarket_schema import (
    AshareSupermarketConfig,
    CandidatePoolConfig,
)

__all__ = ["AshareSupermarketConfig", "CandidatePoolConfig"]
