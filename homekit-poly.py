#!/usr/bin/env python3
"""Polyglot PG3x HomeKit Hub Node Server entry point."""
import sys

from udi_interface import Interface, LOGGER

from nodes import VERSION, Controller


def _sync_profile(polyglot: Interface) -> None:
    """Use checkProfile when metadata is present; fallback to updateProfile."""
    try:
        serverdata = getattr(polyglot, "serverdata", None) or {}
        profile_version = (
            serverdata.get("profile_version")
            if isinstance(serverdata, dict)
            else None
        )
        if profile_version:
            LOGGER.debug(
                "profile_sync: path=checkProfile serverdata.profile_version=%s",
                profile_version,
            )
            polyglot.checkProfile()
        else:
            LOGGER.debug(
                "profile_sync: path=updateProfile_fallback reason=missing_profile_version"
            )
            polyglot.updateProfile()
    except Exception:
        LOGGER.debug(
            "profile_sync: path=updateProfile_fallback reason=exception",
            exc_info=True,
        )
        polyglot.updateProfile()


def main() -> None:
    if sys.version_info < (3, 9):
        LOGGER.error("Python 3.9+ is required, not %s.%s", sys.version_info[0], sys.version_info[1])
        sys.exit(1)
    # Zeroconf defaults: see CONFIG.md (Custom Params `zeroconf_*`; env overrides).
    try:
        polyglot = Interface([Controller])
        polyglot.start(VERSION)
        _sync_profile(polyglot)
        Controller(polyglot, "controller", "controller", "HomeKit Hub")
        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.warning("Interrupt or exit")
    except Exception as err:
        LOGGER.error("Fatal: %s", err, exc_info=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
