# Where these sounds come from

Everything in this folder is copied, unchanged, from the team's deterrence repo:
`nikhilmethi/scare-force-plan`, branch `deter/integration`, commit `5301bf3`, folder
`deterrence/sounds/` (plus `deterrence/sound_library.py` -> `scare/sound_library.py`).
Byte-identical to that commit when copied (2026-11-03). `scare/sound_library.py` has since been
cut down to the generic set this repo plays; the species sets and levels live in scare-force-plan.

| Here | What it is |
|---|---|
| `library.yaml` | The manifest. Every clip's id, sound type, source page, downloaded file, author, **license**, excerpt, evidence and notes. It's also the attribution the CC BY / BY-SA / BY-NC licenses require |
| `profiles/pyle_plmr52.yaml` | The speaker profile the clips were built for (Pyle PLMR52) |
| `build/pyle_plmr52/*.wav` | The playable clips (23): loudness-matched, trimmed, padded |
| `build/pyle_plmr52/report.csv` | The build's per-clip measurements (LUFS, true peak, onset, ...) and pass/fail |

**Don't edit the clips here.** The source audio (`originals/`) and the build script
(`build_sounds.py`) stay in scare-force-plan. To change a clip, a level or a profile, change it
there, rebuild (`python3 deterrence/build_sounds.py --profile pyle_plmr52`) and copy
`library.yaml`, `profiles/` and `build/` over again, updating the commit above.

Licenses (see each clip's `license` in `library.yaml`): CC0, public domain (US federal works), CC
BY 3.0/4.0, CC BY-SA 3.0/4.0, CC BY-NC 4.0, CC BY-NC-SA 4.0 and team-generated sirens. The NC
clips are fine for this course project but not for commercial use.

What plays: with `sound.source: library` the controller plays the **generic** set (16 clips:
human speech, shout, scream, dog bark, siren) in the library's round-robin order, from the first
clip, one per scare. The raptor and woodchuck-alarm clips are built but aren't in the generic set;
the woodchuck-alarm clips are also flagged off. `sound.source: legacy` plays `../../sounds/`
(Demo 1-3's placeholder clips) instead.
