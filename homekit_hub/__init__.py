"""HomeKit Hub async bridge (aiohomekit + WebSocket)."""

from .bridge import (
    DATA_KEY_LAST_HAP_DISCOVER,
    DATA_KEY_PAIRINGS,
    ERR_PAIRING_FAILED,
    ERR_PAIRING_NO_TARGET,
    TYPED_PAIRING_SLOTS_KEY,
    assign_pairing_slot_rows,
    HomeKitHubBridge,
    normalize_hap_pin,
    slot_alias,
)

__all__ = [
    "DATA_KEY_LAST_HAP_DISCOVER",
    "DATA_KEY_PAIRINGS",
    "ERR_PAIRING_FAILED",
    "ERR_PAIRING_NO_TARGET",
    "TYPED_PAIRING_SLOTS_KEY",
    "assign_pairing_slot_rows",
    "HomeKitHubBridge",
    "normalize_hap_pin",
    "slot_alias",
]
