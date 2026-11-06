# Loudness test files

Three 10 s test signals (48 kHz mono, 16-bit, like the clips). They are not scare sounds and
never play in a scare. Use them to find where the sound chain loses level: Pi jack -> MAX9744 ->
Pyle PLMR52. The target is 95 dB(A) at 1 m (TR4 Test 4, D-21).

| file | level | its reading tells you |
|---|---|---|
| `pink_clip_level_-18lufs.wav` | -18 LUFS, peak -6 dBFS: the clips' loudness (every clip is built to -18 LUFS) | about what the scare sounds reach now |
| `pink_full_scale.wav` | -12.8 LUFS, peak -1 dBFS: +5.2 dB, the most a noise-like sound gets without limiting | the headroom from making the clips louder without squashing them |
| `tone_1khz_full_scale.wav` | 1 kHz sine at -1 dBFS (-4.1 LUFS): +13.9 dB | the loudest the amp and speaker can go at all (A-weighting is 0 dB at 1 kHz) |

Play one, looping, through the configured device until Ctrl+C:

    while aplay -q -D "$(python3 -c 'import yaml;print(yaml.safe_load(open("config.yaml"))["sound"]["device"])')" \
        scare/sounds/test/pink_clip_level_-18lufs.wav; do :; done

The mixer is set by the controller at every start (sound.level, 80% mapped, about -1.8 dB). Set
the same level first, or run `python -m scare.sound` once.

## Reading the results

Measure each one at 1 m on axis: phone SPL app, A-weighted, slow. Use the same phone and the same
spot every time. Phone apps are uncalibrated (roughly +/- 2-3 dB), so trust the differences more
than the absolute numbers.

- **tone < 95 dB(A)**: no clip change can reach the target. The fix is hardware: the MAX9744's
  volume trim pot, its supply (12 V, not 5 V), wiring (L+/L- only, bridged output, neither lead
  grounded), the speaker's baffle or enclosure. Re-measure after each change.
- **tone >= 95 but pink at clip level < 95**: the chain can do it; the clips are too quiet. Options:
  rebuild the clips louder (the -18 LUFS target was set by a raptor clip that is now dead), at the
  cost of more limiting; or raise the mixer towards 0 dB (about +1.8 dB; above 0 dB the clips,
  which peak at -1.1 dBTP, clip).
- **The difference between the two pinks** should read about 5 dB. If it reads much less, the amp
  is clipping at full scale: back the trim pot off.

Keep the full-scale files short and use ear protection: the tone can be well over 100 dB at 1 m.

Made with numpy (pink noise 100 Hz-12 kHz, seed 449); loudness measured with pyloudnorm
(ITU-R BS.1770).
