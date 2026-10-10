# RE6 Project Manifest

Create one project manifest for each source model:

```text
python scripts/create_project.py \
  --profile profiles/re6-pl0600.json \
  --source E:\mod\re6\Lossi\Lossi.pmx \
  --work-root E:\mod\re6\Lossi\work \
  --mod-name "Lossi"
```

The work root and final Mod root must both live inside the source model's
top-level folder, for example:

```text
E:\mod\re6\Lossi\
├─ Lossi.pmx
├─ textures\
├─ work\
│  └─ lossi\
│     ├─ project.json
│     ├─ reports\
│     └─ stages\
└─ Lossi_Ada\
```

## Core Fields

- `source`: path, SHA256, format, adapter, and texture roots
- `source_rig`: source preset detection, selected preset, optional AI mapping
- `target_profile`: `re6-pl0600`, Ada, and the single `body` part
- `mod`: display name and export status
- `settings.scale_hint`: optional override; otherwise shoulder alignment is
  calibrated in Stage 1
- `settings.make_cylindrical_face_normals`: enabled by default and executed
  inside Stage 1 for RE6
- `settings.make_outline`: optional Stage 5D whole-body outline
- `settings.stage_5c_merge_textures`: optional Stage 5C atlas mode
- `settings.stage_5c_merge_by_base_image`: enabled by default
- `settings.mrl`: texture directory, material rename map, original MRL
  template, slot configuration, and `Null_NM` path
- `settings.texture`: BC1 base color settings, BC3 normal settings, 40 percent
  base brightness reduction, and the `Null_NM.dds` source
- `settings.ctc`: chain damping, force, collision radius, and 15-to-60 degree
  interpolation
- `settings.export`: reference mod root, reused `.ccl`, MRL names, and
  `modinfo.ini`
- `stage_history`: one entry for every successful `.blend`

## Unknown Source Rig

When no Modding Toolkit source preset reaches 95 percent coverage, create
`reports/ai_source_to_mmd_bone_mapping.json`:

```json
{
  "main_bone_mapping": {
    "source_main_bone": "MMD_MainBone"
  },
  "physics_bones": [
    "source_hair_01",
    "source_skirt_01"
  ]
}
```

Only main bones are renamed in Stage 1B. Physics bones keep their original
names and hierarchy until Stage 3.

