# RE6MOD Skill

Automated Blender workflow for creating a Resident Evil 6 `pl0600` Ada
character replacement from an existing character model.

The skill turns PMX, PMD, FBX, GLB, GLTF, VRM, BLEND, or OBJ input into a
single-body RE6 mod containing the face, hair, clothing, materials, textures,
and CTC physics chains.

[English](README.md) | [简体中文](README.zh-CN.md)

## What It Produces

- one `pl0600.mod` body model containing all visible parts
- `pl0600_0.mrl`, `pl0600_1.mrl`, and `pl0600_2.mrl`
- BC1 base color textures and BC3 normal textures
- `pl0600.ctc` physics chains built through RE6 Model Editor
- a ready-to-install SideLoader mod folder
- JSON reports for every stage and validation pass

The final mod is written as:

```text
<ModName>_Ada/
|-- modinfo.ini
`-- nativePC_mod/
    `-- data/
        |-- chara/pl/pl0600/model/
        |   |-- pl0600.mod
        |   |-- pl0600_0.mrl
        |   |-- pl0600_1.mrl
        |   |-- pl0600_2.mrl
        |   |-- pl0600.ctc
        |   `-- pl0600.ccl
        `-- chara/pl/pl0600/gzyv/
            `-- *.tex
```

## Requirements

- Blender `5.2.x`
- Python `3.10+` for the launcher scripts
- Windows 10 or Windows 11 is recommended
- Enough disk space for the source model, stage `.blend` files, and exports

No `pip install` step is required. The Python launcher scripts use only the
standard library. Blender stage scripts run inside Blender.

The following Blender add-ons are included in this repository:

- `mmd_tools`
- `Modding-Toolkit`
- `re6_model_editor`

They are loaded directly from `assets/RE6/Tools`. Do not install or enable
separate copies for this workflow.

## Download

Download the repository ZIP from GitHub:

<https://github.com/guangzhiyingv-cmd/RE6MOD_Skill>

Or clone it:

```powershell
git clone https://github.com/guangzhiyingv-cmd/RE6MOD_Skill.git
```

The repository contains templates, reference resources, and third-party
tools, so a normal clone is larger than a typical script-only skill.

## Install As A Codex Skill

Place this repository under your Codex skills directory. The recommended
folder name is `re6-mod-auto`, matching the `name` field in `SKILL.md`.

```powershell
$skillsRoot = Join-Path $env:USERPROFILE ".codex\skills"
New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null

git clone https://github.com/guangzhiyingv-cmd/RE6MOD_Skill.git `
  (Join-Path $skillsRoot "re6-mod-auto")
```

Verify the installation:

```powershell
Test-Path (Join-Path $skillsRoot "re6-mod-auto\SKILL.md")
```

Reload Codex or start a new task so the skill is discovered. Then mention
`$re6-mod-auto` in your request, or ask Codex to use the RE6MOD skill.

If you already downloaded a ZIP, extract it and copy the extracted folder to:

```text
%USERPROFILE%\.codex\skills\re6-mod-auto
```

## Manual Quick Start

You can use the scripts without installing the repository as a Codex skill.
Run the commands from the repository root.

### 1. Set Paths

```powershell
$SkillRoot = (Resolve-Path ".").Path
$env:BLENDER_EXECUTABLE = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"

$Source = "D:\Models\MyCharacter\MyCharacter.pmx"
$WorkRoot = "D:\Models\MyCharacter\work"
$ModName = "MyCharacter"
```

Blender can also be found through `PATH`, the profile's
`blender.executable`, or the `--blender` argument.

### 2. Create The Project

```powershell
python "$SkillRoot\scripts\create_project.py" `
  --source $Source `
  --work-root $WorkRoot `
  --mod-name $ModName
```

The default project ID is the lower-case slug of the mod name. For example,
`MyCharacter` creates:

```text
D:\Models\MyCharacter\work\mycharacter\project.json
```

Useful project options:

```text
--outline                  Enable Stage 5D outline generation
--merge-textures           Enable texture merging where supported
--no-merge-by-base-image   Disable Stage 5C base-image material merging
--execution-mode normal    Disable low-token execution
```

Low-token mode is enabled by default. It keeps command output compact while
preserving stage results and reports.

### 3. Check The Environment

```powershell
$Project = Join-Path $WorkRoot "mycharacter\project.json"

python "$SkillRoot\scripts\check_environment.py" `
  --project $Project
```

The check must report `"ok": true` before continuing.

### 4. Inspect The Source Model

```powershell
& $env:BLENDER_EXECUTABLE `
  --background --factory-startup `
  --python "$SkillRoot\scripts\inspect_source.py" `
  -- `
  --source $Source `
  --project $Project `
  --report (Join-Path $WorkRoot "mycharacter\reports\source_inventory.json")
```

Review the generated source report before starting Stage 1. It records mesh
and armature counts, image paths, source bounds, detected rig preset, and
pose detection.

### 5. Run The Remaining Stages

The authoritative stage order is in
[agents/WORKFLOW.md](agents/WORKFLOW.md). Run each stage with the output of
the previous stage as its input. Never overwrite a successful stage.

When using Codex, ask the agent to use `$re6-mod-auto` and it will follow the
low-token stage instructions.

## Workflow

| Stage | Script | Purpose |
| --- | --- | --- |
| 0 | `check_environment.py`, `inspect_source.py` | Validate tools and inspect the source |
| 1 | `prepare_split_stage.py` | Import, calibrate, split materials, process face normals |
| 1B | `normalize_source_rig_stage.py` | Normalize an unknown source rig when required |
| 2 | `align_skeleton_stage.py` | Align to the `pl0600` skeleton and clean finger bones |
| 3 | `prepare_rig_stage.py` | Build the RE6 rig and physics bone mapping |
| 5D | `prepare_outline_stage.py` | Optional body outline shell |
| 5C | `finalize_mesh_cleanup_stage.py` | Finalize materials, weights, and mesh groups |
| 6 | `prepare_mrl_stage.py` | Build and inspect the MRL |
| 7 | `prepare_re6_texture_stage.py` | Encode BC1 base color and BC3 normal textures |
| 8 | `prepare_re6_ctc_stage.py` | Build and export plugin-authored CTC physics chains |
| 9 | `auto_export_re6_stage.py` | Export the MOD and assemble the final mod folder |

Some internal stage numbers are historical. Follow the order in
`agents/WORKFLOW.md`, not the numeric gap.

## Source Model Support

| Format | Adapter |
| --- | --- |
| PMX | MMD Tools |
| PMD | MMD Tools |
| FBX | Blender FBX importer |
| GLB / GLTF | Blender glTF importer |
| VRM | Blender VRM importer, when available |
| BLEND | Blender library import |
| OBJ | Blender OBJ importer |

PMX is the best-tested path. Other formats are supported by the shared import
adapter, but an unusual rig may require the Stage 1B normalization mapping.

## Project Layout

A generated project uses this structure:

```text
<source folder>/
|-- <source model>
|-- work/
|   `-- <project-id>/
|       |-- project.json
|       |-- manual/
|       |-- reports/
|       |   |-- logs/
|       |   `-- run_state.json
|       `-- stages/
|           |-- stage_01_split.blend
|           |-- stage_02_aligned.blend
|           |-- stage_03_rig.blend
|           |-- stage_05c_mesh_cleanup.blend
|           |-- stage_06_mrl.blend
|           |-- stage_07_textures.blend
|           |-- stage_08_ctc.blend
|           |-- pl0600.mrl
|           `-- pl0600.ctc
`-- <ModName>_Ada/
```

Keep `work` and the final mod root inside the source model's own top-level
folder, as shown by the project manifest.

## Important Behavior

- The target is `pl0600` Ada and always exports one body model.
- Face, hair, clothing, and body materials stay in the same MOD.
- The RE6 skeleton uses numbered bone names. Semantics come from
  `assets/RE6/RE6.json`.
- Height alignment uses the shoulder bones, not the overall model bounds.
- Every finger phalanx rotates inward by 20 degrees. `thumb_01` is not
  rotated, but all finger bones are still deleted and merged into `hand`.
- Physics bones must fit the RE6 ranges `213-249` and `135-189`.
- Stage 8 writes only the CTC. The final stage reuses the reference CCL.
- Stage 9 validates the exported MOD, MRL, CTC, textures, material hashes,
  and file placement.

## Troubleshooting

### Blender executable not found

Set `BLENDER_EXECUTABLE`, pass `--blender`, or add Blender to `PATH`.

```powershell
$env:BLENDER_EXECUTABLE = "C:\Path\To\Blender\blender.exe"
python "$SkillRoot\scripts\check_environment.py" --project $Project
```

### Wrong Blender version

This workflow requires Blender `5.2.x`. Other versions may load the add-ons
but are not validated by this repository.

### Internal add-on missing

Confirm these directories exist:

```text
assets/RE6/Tools/mmd_tools
assets/RE6/Tools/Modding-Toolkit
assets/RE6/Tools/re6_model_editor-0.3.0
```

If they are missing, download the repository again. Do not replace them with
unrelated installed add-ons unless you are intentionally modifying the
pipeline.

### Stage refuses to overwrite an existing file

This is intentional. Use a new project ID, a new work root, or a new stage
name. Do not delete successful stages merely to rerun a later stage.

### Existing CTC collection stop

Stage 8 refuses to silently replace a user-modified CTC collection. Use a
normal Stage 7 input, or pass `--rebuild-existing-ctc` only when you explicitly
want to discard and rebuild the existing collection.

### Texture validation fails

Stage 9 requires every shipped texture path to resolve to a `.tex` file. Check
the Stage 7 report and the generated texture directory. Original game texture
paths are allowed only when explicitly preserved by the profile.

### The game does not load the mod

Install RE6 SideLoader and place the generated `nativePC_mod` tree through the
normal SideLoader mod layout. Keep `modinfo.ini` with the mod folder.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Codex skill entry point and hard rules |
| `README.md` | Download, installation, and usage guide |
| `README.zh-CN.md` | Simplified Chinese guide |
| `agents/WORKFLOW.md` | Authoritative stage order and validation rules |
| `profiles/re6-pl0600.json` | Target profile and paths |
| `scripts/` | Project setup, Blender stages, validation, and export |
| `assets/RE6/` | Templates, add-ons, helper tools, and reference resources |
| `references/` | Low-token mode and project manifest documentation |
| `THIRD_PARTY_NOTICES.md` | Bundled third-party and game-data notice |

## Documentation

- [Workflow](agents/WORKFLOW.md)
- [Low-token mode](references/low-token-mode.md)
- [Project manifest](references/project-manifest.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Bundled Components And Licensing

This repository bundles Blender add-ons, helper binaries, game-derived
templates, reference mod resources, and game textures. They do not all use the
same license. Review [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the
`LICENSE` or `NOTICE` file inside each bundled component before redistributing
the repository or its output.

No ownership is claimed over Capcom game assets. Use the workflow only with
game files and models you are legally entitled to use.
