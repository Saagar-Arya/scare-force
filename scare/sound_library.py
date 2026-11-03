"""Which clips to play: resolves a classifier label to a clip set from sounds/library.yaml.

    lib = Library.load()                    # mode / active_profile from library.yaml
    lib.set_for("gray squirrel")            # -> "squirrel" in species/auto mode, "generic" in generic
    lib.clips_for("raccoon")                # -> [Path(.../build/<profile>/<clip_id>.wav), ...]

Modes: `generic` always uses the generic set. `species` uses the label's set and falls back to
generic when the label is missing or not in label_map. `auto` behaves like species; it exists so
the config reads naturally once the classifier names species.

clips_for() orders clips round-robin across sound types, so consecutive clips differ in type
where the set allows it. Flagged clips are left out unless include_flagged=True; clips with an
`excluded_reason` (e.g. build_sounds.py's "too_peaky: ...") are always left out. Clips are built
once per profile by build_sounds.py; a missing WAV raises BuildMissing naming the rebuild command.

Levels: every sound type plays at the profile's reference_spl_1m_db (team decision D-21), less
its level_trim_db (a lab correction, <= 0). level(sound_type) gives the intended SPL and the dB
the build puts the type under target_lufs; library.yaml's cited_levels (what the cited studies
used) ride along as information and are never applied. type_level() is the one rule, shared with
build_sounds.py.
"""

import re
from collections import Counter
from pathlib import Path

import yaml

LIBRARY = Path(__file__).resolve().parent / "sounds" / "library.yaml"
MODES = ("generic", "species", "auto")
GENERIC = "generic"


class BuildMissing(FileNotFoundError):
    pass


def type_level(sound_type, profile, cited_levels=None):
    """Intended playback level of one sound type under a speaker profile (dict):
    spl_1m_db   the profile's reference_spl_1m_db (D-21: the same for every type; None if unset)
    trim_db     the profile's level_trim_db for the type (default 0): a lab correction
    offset_db   dB under target_lufs the type is built at (= trim_db; cited levels not applied)
    cited_spl_1m_db / cited_refs / cited_conditions: cited_levels' entry, information only
    Attenuation only: a trim above 0 raises ValueError (a type that measures low means the amp
    pot goes up and the other types get trimmed down)."""
    trim = float((profile.get("level_trim_db") or {}).get(sound_type, 0) or 0)
    if trim > 0:
        raise ValueError(f"level_trim_db {sound_type}: {trim:g} dB > 0; trims only attenuate (raise "
                         "the amp pot instead, then trim the louder types down)")
    cited = (cited_levels or {}).get(sound_type) or {}
    return {"spl_1m_db": profile.get("reference_spl_1m_db"), "trim_db": trim, "offset_db": trim,
            "cited_spl_1m_db": cited.get("spl_1m_db"), "cited_refs": list(cited.get("refs") or []),
            "cited_conditions": cited.get("conditions")}


def norm_label(label):
    """'White-tailed_Deer ' -> 'white tailed deer'."""
    return re.sub(r"[\s_\-]+", " ", str(label)).strip().lower()


def round_robin(clips):
    """Order clips so no two neighbours share a sound type where possible: always take the type
    with the most clips left that isn't the previous one (ties: first-listed type). With the
    list played in a loop, the last clip is also kept off the first clip's type if possible."""
    remaining = {}
    for c in clips:
        remaining.setdefault(c["sound_type"], []).append(c)
    out, prev = [], None
    while remaining:
        counts = Counter({t: len(v) for t, v in remaining.items()})
        choices = [t for t in remaining if t != prev] or list(remaining)
        t = max(choices, key=lambda t: counts[t])  # max() keeps the first of equal counts
        out.append(remaining[t].pop(0))
        if not remaining[t]:
            del remaining[t]
        prev = t
    if len(out) > 2 and out[-1]["sound_type"] == out[0]["sound_type"]:
        last = out.pop()
        for i in range(1, len(out)):  # reinsert between two clips of other types
            if out[i - 1]["sound_type"] != last["sound_type"] != out[i]["sound_type"]:
                out.insert(i, last)
                break
        else:
            out.append(last)
    return out


class Library:
    def __init__(self, data, root, mode=None, profile=None, include_flagged=False):
        self.data = data
        self.root = Path(root)
        self.mode = mode or data.get("mode", GENERIC)
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {self.mode!r}")
        self.profile = profile or data["active_profile"]
        self.include_flagged = include_flagged
        self.sets = {name: list(types) for name, types in data["sets"].items()}
        if GENERIC not in self.sets:
            raise ValueError("library.yaml needs a 'generic' set")
        self.label_map = {norm_label(k): v for k, v in (data.get("label_map") or {}).items()}
        for label, name in self.label_map.items():
            if name not in self.sets:
                raise ValueError(f"label_map: {label!r} -> unknown set {name!r}")
        self.clips = data["clips"]
        self._profile_data = None

    @classmethod
    def load(cls, path=LIBRARY, mode=None, profile=None, include_flagged=False):
        """mode / profile override the YAML's `mode` / `active_profile`."""
        path = Path(path)
        return cls(yaml.safe_load(path.read_text()), path.resolve().parent, mode, profile,
                   include_flagged)

    @property
    def build_dir(self):
        return self.root / "build" / self.profile

    @property
    def profile_path(self):
        return self.root / "profiles" / f"{self.profile}.yaml"

    @property
    def profile_data(self):
        """The speaker profile's settings ({} if the profile file doesn't exist)."""
        if self._profile_data is None:
            path = self.profile_path
            self._profile_data = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
        return self._profile_data

    def level(self, sound_type):
        """Intended playback level of a sound type: see type_level()."""
        return type_level(sound_type, self.profile_data, self.data.get("cited_levels"))

    def playable(self, clip, include_flagged=None):
        flagged_ok = self.include_flagged if include_flagged is None else include_flagged
        return not clip.get("excluded_reason") and (flagged_ok or not clip.get("flagged", False))

    def first_clip(self, sound_type, include_flagged=None):
        """The first playable record of a sound type in library order, or None."""
        return next((c for c in self.clips if c["sound_type"] == sound_type
                     and self.playable(c, include_flagged)), None)

    def set_for(self, label):
        if self.mode == GENERIC or label is None:
            return GENERIC
        return self.label_map.get(norm_label(label), GENERIC)

    def clip_records(self, label=None, include_flagged=None):
        """Library records of the resolved set, in play order. A clip with a truthy
        excluded_reason is never included, flagged or not."""
        types = self.sets[self.set_for(label)]
        chosen = [c for t in types for c in self.clips
                  if c["sound_type"] == t and self.playable(c, include_flagged)]
        return round_robin(chosen)

    def clips_for(self, label=None, include_flagged=None):
        """Built WAV paths for the label's set, round-robin across sound types."""
        paths = [self.build_dir / f"{c['id']}.wav" for c in self.clip_records(label, include_flagged)]
        missing = [p.name for p in paths if not p.exists()]
        if missing:
            raise BuildMissing(
                f"{len(missing)} clip(s) not built for profile {self.profile} "
                f"(e.g. {missing[0]}). Rebuild: python3 deterrence/build_sounds.py --profile {self.profile}")
        return paths
