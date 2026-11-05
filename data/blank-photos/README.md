# Blank photos

Photos from the Pi's cameras with **no animal (toy) in view**: what each scene looks like empty.
Use them as negatives (frames that must not trigger a scare), to check false fires, or as
backgrounds to difference against.

## 2026-11-03/

14 photos, 640x480, taken through `scare/camera.py` on 2026-11-03 during the quick capture of
the toys (the full set, with the animal shots and draft labels, is on the `captures-2026-11-03`
branch, `random/quick-2026-11-03/`).

- `cam0/`, `cam1/`: the two Camera Module 3s, with the camera numbering of that day (before the
  third camera was added on 2026-11-04 and the ids changed). The two cameras see nearly the same
  view.
- 7 shots per camera: `000_blank_start` at the start, then one blank after each species/distance
  group (`004_raccoon_3ft_blank`, `008_raccoon_8ft_blank`, `014_groundhog_3ft_blank`,
  `018_groundhog_8ft_blank`, `024_squirrel_3ft_blank`, `028_squirrel_8ft_blank`).
- `manifest.csv`: per photo its camera, shot id, nominal distance, which animal shots it is the
  empty version of (`blank_for`), and the capture timestamp as recorded by the capture script.

Caveats:
- Indoors, cameras aimed up at the lab ceiling: not the farm. Distances are nominal (the toys
  were held close).
- **Not empty of people**: a team member is partly in frame at the edge in most of them. They
  are blank of animals, not of people.
- On that day the model then in use saw a raccoon on 3 of them (confidence 0.21-0.42); the model
  on main has since been retrained, so check again before relying on that.
