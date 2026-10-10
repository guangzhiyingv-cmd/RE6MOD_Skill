# RE6MOD Skill

Automated Blender preparation and export workflow for a Resident Evil 6
`pl0600` Ada replacement mod.

The skill accepts PMX, FBX, GLTF, VRM, BLEND, and OBJ character models and
produces:

- one RE6 body model containing the face, hair, and clothing materials
- the `.mod` model
- the `.mrl` material libraries
- BC1 base color and BC3 normal textures
- the `.ctc` physics chains
- the final SideLoader mod folder

## Requirements

- Blender 5.2
- `mmd_tools`
- `re6_model_editor`
- the latest Modding Toolkit add-on used by the source import and mesh stages

## Quick Start

```powershell
python scripts/create_project.py `
  --profile profiles/re6-pl0600.json `
  --source "E:\mod\re6\Lossi\Lossi.pmx" `
  --work-root "E:\mod\re6\Lossi\work" `
  --mod-name "Lossi"
```

```powershell
python scripts/check_environment.py `
  --profile profiles/re6-pl0600.json `
  --project "E:\mod\re6\Lossi\work\lossi\project.json"
```

The complete stage order and validation rules are in
[agents/WORKFLOW.md](agents/WORKFLOW.md).

## Layout

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Agent entry point and hard rules |
| `agents/WORKFLOW.md` | RE6 Stage 0-9 workflow |
| `profiles/re6-pl0600.json` | Target profile |
| `scripts/` | Project, Blender stage, validation, and export scripts |
| `assets/RE6/` | RE6 templates, tools, source game files, and reference mod |
| `references/` | Low-token mode and project manifest documentation |

