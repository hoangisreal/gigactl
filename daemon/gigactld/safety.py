"""Conservative software policy, not a statement of firmware thermal limits.

Firmware mode yields control completely. Every software-controlled mode must
use this policy, including manual overrides and a GPU with no temperature.
"""
from __future__ import annotations

MIN_RUNNING_PCT = 30
WARM_C = 70
CPU_EMERGENCY_C = 95
GPU_EMERGENCY_C = 87


def safe_duty(requested: int, cpu_temp: int, gpu_temp: int = 0) -> int:
    """Clamp a software request; GPU zero means unavailable, NOT zero degrees."""
    if not 1 <= cpu_temp <= 125 or not 0 <= gpu_temp <= 125:
        raise ValueError("invalid thermal sample; software fan control refused")
    if cpu_temp >= CPU_EMERGENCY_C or gpu_temp >= GPU_EMERGENCY_C:
        return 100
    if max(cpu_temp, gpu_temp) >= WARM_C:
        return max(requested, MIN_RUNNING_PCT)
    return requested
