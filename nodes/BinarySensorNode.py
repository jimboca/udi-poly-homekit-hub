#!/usr/bin/env python3
"""Backward-compatible import path for sensor IoX nodes."""

from .SensorNode import BinarySensorNode, SensorNode  # noqa: F401

__all__ = ['BinarySensorNode', 'SensorNode']
