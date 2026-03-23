from .profile import TrackProfile
from .openenv import OPENENV_PROFILE
from .security import SECURITY_PROFILE
from .tau2 import TAU2_PROFILE

_PROFILES = {
    "openenv": OPENENV_PROFILE,
    "security": SECURITY_PROFILE,
    "tau2": TAU2_PROFILE,
}


def get_track_profile(name: str) -> TrackProfile:
    return _PROFILES.get(name.lower(), OPENENV_PROFILE)


__all__ = [
    "TrackProfile",
    "OPENENV_PROFILE",
    "SECURITY_PROFILE",
    "TAU2_PROFILE",
    "get_track_profile",
]
