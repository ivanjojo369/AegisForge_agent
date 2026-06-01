from .profile import TrackProfile
from .openenv import OPENENV_PROFILE
from .security import SECURITY_PROFILE
from .tau2 import TAU2_PROFILE
from .skillsbench import SKILLSBENCH_PROFILE

_PROFILES = {
    "openenv": OPENENV_PROFILE,
    "security": SECURITY_PROFILE,
    "security_arena": SECURITY_PROFILE,
    "tau2": TAU2_PROFILE,
    "tau2_agentbeats": TAU2_PROFILE,

    # SkillsBench / General-Purpose Agent.
    "skillsbench": SKILLSBENCH_PROFILE,
    "skillsbench_leaderboard": SKILLSBENCH_PROFILE,
    "skillsbench-leaderboard": SKILLSBENCH_PROFILE,
    "benchflow": SKILLSBENCH_PROFILE,
    "benchflow_ai": SKILLSBENCH_PROFILE,
    "benchflow-ai": SKILLSBENCH_PROFILE,
    "standard-v1": SKILLSBENCH_PROFILE,
    "standard_v1": SKILLSBENCH_PROFILE,
    "with_skills": SKILLSBENCH_PROFILE,
    "with-skills": SKILLSBENCH_PROFILE,
    "general_purpose": SKILLSBENCH_PROFILE,
    "general-purpose": SKILLSBENCH_PROFILE,
    "general purpose": SKILLSBENCH_PROFILE,
    "general_purpose_agent": SKILLSBENCH_PROFILE,
    "general-purpose-agent": SKILLSBENCH_PROFILE,
    "multi_utility": SKILLSBENCH_PROFILE,
    "multi-utility": SKILLSBENCH_PROFILE,
}


def _normalize_profile_name(name: str) -> str:
    return str(name or "").strip().lower()


def get_track_profile(name: str) -> TrackProfile:
    return _PROFILES.get(_normalize_profile_name(name), OPENENV_PROFILE)


__all__ = [
    "TrackProfile",
    "OPENENV_PROFILE",
    "SECURITY_PROFILE",
    "TAU2_PROFILE",
    "SKILLSBENCH_PROFILE",
    "get_track_profile",
]
