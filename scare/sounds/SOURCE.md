# Where these sounds come from

**Nov 11, 2026: scare-force-plan is retired; this folder is now the sound library of record.**
`library.yaml` here is the manifest (sources, authors, licenses, excerpts, exclusions). The three
clips added Nov 11 (D-29: `bird_call_screaming_piha_01`, `raptor_red_tailed_hawk_05`,
`dog_bark_pack_05`) have their cut-only originals in `originals/` here; they were built with
scare-force-plan's `deterrence/build_sounds.py` (main `be8f5c7`), profile pyle_plmr52, level_mode
max, 12 dB limiter cap. Older clips' originals and the build script stay in scare-force-plan.
Generic set now: 18 clips (goal shout and the last speech clip dropped after a listening pass). The history below is from Nov 3.

Everything in this folder is copied, unchanged, from the team's deterrence repo:
`nikhilmethi/scare-force-plan`, branch `deter/integration`, commit `5301bf3`, folder
`deterrence/sounds/` (plus `deterrence/sound_library.py` -> `scare/sound_library.py`).
Byte-identical to that commit when copied (2026-11-03). `scare/sound_library.py` has since been
cut down to the generic set this repo plays; the species sets and levels live in scare-force-plan.

| Here | What it is |
|---|---|
| `library.yaml` | The manifest. Every clip's id, sound type, source page, downloaded file, author, **license**, excerpt, evidence and notes. It's also the attribution the CC BY / BY-SA / BY-NC licenses require |
| `profiles/pyle_plmr52.yaml` | The speaker profile the clips were built for (Pyle PLMR52) |
| `build/pyle_plmr52/*.wav` | The playable clips (16, the generic set): loudness-matched, trimmed, padded |
| `build/pyle_plmr52/report.csv` | The build's per-clip measurements (LUFS, true peak, onset, ...) and pass/fail |

**Don't edit the clips here.** The source audio (`originals/`) and the build script
(`build_sounds.py`) stay in scare-force-plan. To change a clip, a level or a profile, change it
there, rebuild (`python3 deterrence/build_sounds.py --profile pyle_plmr52`) and copy
`library.yaml`, `profiles/` and `build/` over again, updating the commit above.

Licenses (see each clip's `license` in `library.yaml`): CC0, public domain (US federal works), CC
BY 3.0/4.0, CC BY-SA 3.0/4.0, CC BY-NC 4.0, CC BY-NC-SA 4.0 and team-generated sirens. The NC
clips are fine for this course project but not for commercial use.

What plays: the controller plays the **generic** set (16 clips:
human speech, shout, scream, dog bark, siren; minus any types in `sound.skip_types` in
config.yaml), a random clip per scare (never the previous scare's), each rendered to the scare's length at startup (`scare/sound_fill.py`). The species
sets are kept but not connected (species-specific sound is extra credit, undecided): raccoon 12
clips, groundhog 4, squirrel 4, all already in the generic set.

Changes from the copied library.yaml (scare-force-plan is retired, D-23, so these are made here):
- the 4 raptor clips: WAVs removed 2026-11-04 (dead when Saagar tested them on the Pi); their
  records stay, with `excluded_reason`, so the groundhog and squirrel sets are dog barks only;
- the 3 woodchuck-alarm whistles: removed entirely 2026-11-05 (records, the `woodchuck_alarm`
  type in the groundhog set, report.csv rows).
