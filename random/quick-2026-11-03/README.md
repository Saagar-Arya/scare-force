# Pi camera captures, 2026-11-03

74 frames (37 shots x cam0/cam1), 640x480, from the deployed Camera Module 3s via scare/camera.py.

- `images/<cam>/<shot_id>.jpg`, with a matching `labels/<cam>/<shot_id>.txt` (YOLO: class cx cy w h, normalised)
- `classes.txt`: 0 raccoon, 1 groundhog, 2 squirrel, 3 person
- Labels are **drafts** from toys_320.onnx + yolo11n_320.onnx, with toy classes corrected from the shot list.
  Fix them before training. Problem images are listed in `PRELABEL.md`; `contact_prelabel_*.jpg` shows them all.
- `manifest.csv`: shot metadata. Distances are nominal (toys were held close, cameras aimed at the ceiling).
- cam0/cam1 are near-duplicates: keep both copies of a shot in the same train/val split.
