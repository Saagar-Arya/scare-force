"""Which clips to play: a set from sounds/library.yaml, in play order.

    lib = Library.load()          # active_profile from library.yaml
    lib.clips_for()               # the generic set -> [Path(.../build/<profile>/<clip_id>.wav), ...]
    lib.clips_for("squirrel")     # a species set (raccoon, groundhog, squirrel)
    Library.load(skip_types=["human_speech"])   # leave sound types out of every set

library.yaml is copied unchanged from scare-force-plan (see sounds/SOURCE.md). Only the generic
set is played (scare/sound.py). The species sets and their WAVs are kept here, built and
checked, but not connected: whether to play a set by detected species (extra credit, Challenge 1)
is still open. label_map, mode and cited_levels in library.yaml are not used by this repo.

Self-check: python -m scare.sound_library   (every set: clip count, types, missing WAVs)

Clips are ordered round-robin across sound types, so consecutive clips differ in type where the
set allows it. Flagged clips and clips with an `excluded_reason` (e.g. "too_peaky: ...") are left
out. The WAVs are built in scare-force-plan; a missing one raises BuildMissing.
"""

from collections import Counter
from pathlib import Path

import yaml

LIBRARY = Path(__file__).resolve().parent / "sounds" / "library.yaml"
GENERIC = "generic"


class BuildMissing(FileNotFoundError):
    pass


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
    def __init__(self, data, root, skip_types=()):
        self.root = Path(root)
        self.profile = data["active_profile"]
        if GENERIC not in data["sets"]:
            raise ValueError("library.yaml needs a 'generic' set")
        known = {t for types in data["sets"].values() for t in types}
        unknown = set(skip_types) - known
        if unknown:
            raise ValueError(f"sound.skip_types: no such sound type {sorted(unknown)} "
                             f"(types: {sorted(known)})")
        self.skip_types = list(skip_types)
        self.sets = {name: [t for t in types if t not in self.skip_types]
                     for name, types in data["sets"].items()}
        if not self.sets[GENERIC]:
            raise ValueError("sound.skip_types leaves the generic set with nothing to play")
        self.clips = data["clips"]

    @classmethod
    def load(cls, path=LIBRARY, skip_types=()):
        path = Path(path)
        return cls(yaml.safe_load(path.read_text()), path.resolve().parent, skip_types)

    @property
    def build_dir(self):
        return self.root / "build" / self.profile

    @staticmethod
    def playable(clip):
        return not clip.get("excluded_reason") and not clip.get("flagged", False)

    def clip_records(self, set_name: str = GENERIC):
        """Library records of a set (default generic), in play order."""
        if set_name not in self.sets:
            raise KeyError(f"no set {set_name!r} in library.yaml (sets: {', '.join(self.sets)})")
        chosen = [c for t in self.sets[set_name] for c in self.clips
                  if c["sound_type"] == t and self.playable(c)]
        return round_robin(chosen)

    def clips_for(self, set_name: str = GENERIC):
        """Built WAV paths of a set (default generic), in play order."""
        paths = [self.build_dir / f"{c['id']}.wav" for c in self.clip_records(set_name)]
        missing = [p.name for p in paths if not p.exists()]
        if missing:
            raise BuildMissing(
                f"{len(missing)} clip(s) not built for profile {self.profile} "
                f"(e.g. {missing[0]}). Build them in scare-force-plan and copy them in: "
                "see scare/sounds/SOURCE.md")
        return paths


if __name__ == "__main__":
    lib = Library.load()
    print(f"profile {lib.profile}; generic is played, species sets are kept but not connected")
    bad = 0
    for name in lib.sets:
        records = lib.clip_records(name)
        missing = [c["id"] for c in records if not (lib.build_dir / f"{c['id']}.wav").exists()]
        bad += len(missing)
        types = Counter(c["sound_type"] for c in records)
        print(f"  {name:10} {len(records):2} clips  " + ", ".join(f"{t} {n}" for t, n in types.items())
              + (f"  MISSING {', '.join(missing)}" if missing else ""))
    raise SystemExit(1 if bad else 0)
