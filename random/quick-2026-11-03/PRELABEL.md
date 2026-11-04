# Pre-label report: quick-2026-11-03

Models: models/toys_320.onnx (toys, conf >= 0.15) + models/yolo11n_320.onnx (person, conf >= 0.25). Labels in labels_prelabel/<camera>/.

## How the current toy model does on these Pi frames

- Single-toy images: 48; model found a toy in 45, right class in 20
- No toy found: 3 used the blank-diff box, 0 need a box added
- Blanks: 14; model fired a toy on 3 (dropped)
- Person boxes written: 82

## Per-image notes

- 003_raccoon_3ft_back cam0: model said groundhog
- 005_raccoon_8ft_front cam0: model said groundhog
- 007_raccoon_8ft_back cam0: model said groundhog
- 010_raccoon_5ft_handheld cam0: model said groundhog
- 014_groundhog_3ft_blank cam0: model saw raccoon 0.35 on blank (dropped)
- 016_groundhog_8ft_side cam0: model said raccoon
- 017_groundhog_8ft_back cam0: no toy found: diff fallback
- 021_squirrel_3ft_front cam0: model said raccoon
- 022_squirrel_3ft_side cam0: model said groundhog
- 023_squirrel_3ft_back cam0: model said raccoon
- 025_squirrel_8ft_front cam0: model said raccoon
- 026_squirrel_8ft_side cam0: model said raccoon
- 027_squirrel_8ft_back cam0: no toy found: diff fallback
- 028_squirrel_8ft_blank cam0: model saw raccoon 0.42 on blank (dropped)
- 029_squirrel_5ft_edge cam0: model said groundhog
- 030_squirrel_5ft_handheld cam0: model said groundhog
- 031_person_near cam0: dropped 1 false toy box(es)
- 033_person_walk cam0: dropped 1 false toy box(es)
- 034_all3_toys cam0: 1 toy boxes, classes from model: CHECK
- 035_distractor_bag cam0: 1 toy boxes, classes from model: CHECK
- 036_distractor_plush cam0: 1 toy boxes, classes from model: CHECK
- 002_raccoon_3ft_side cam1: model said groundhog
- 003_raccoon_3ft_back cam1: model said groundhog
- 005_raccoon_8ft_front cam1: model said groundhog
- 007_raccoon_8ft_back cam1: model said groundhog
- 010_raccoon_5ft_handheld cam1: model said groundhog; no person found
- 014_groundhog_3ft_blank cam1: model saw raccoon 0.21 on blank (dropped)
- 017_groundhog_8ft_back cam1: no toy found: diff fallback
- 021_squirrel_3ft_front cam1: model said groundhog
- 022_squirrel_3ft_side cam1: model said groundhog
- 023_squirrel_3ft_back cam1: model said raccoon
- 025_squirrel_8ft_front cam1: model said raccoon
- 026_squirrel_8ft_side cam1: model said raccoon
- 027_squirrel_8ft_back cam1: model said raccoon
- 029_squirrel_5ft_edge cam1: model said groundhog
- 030_squirrel_5ft_handheld cam1: model said groundhog
- 031_person_near cam1: dropped 1 false toy box(es)
- 033_person_walk cam1: dropped 1 false toy box(es)
- 034_all3_toys cam1: 1 toy boxes, classes from model: CHECK
- 035_distractor_bag cam1: 0 toy boxes, classes from model: CHECK
- 036_distractor_plush cam1: 0 toy boxes, classes from model: CHECK

Boxes are drafts: check every image in contact_prelabel_<camera>.jpg before training.
