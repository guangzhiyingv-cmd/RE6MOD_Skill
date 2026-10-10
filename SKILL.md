---
name: re6-mod-auto
description: Build a Resident Evil 6 pl0600 Ada replacement from PMX, FBX, GLTF, VRM, BLEND, or OBJ source models. Use the RE6 Blender stages to prepare one body, MRL materials, textures, CTC physics chains, and the final SideLoader mod.
metadata:
  short-description: Turn character models into RE6 Ada replacements
---

# RE6 Mod Auto

This skill builds one Resident Evil 6 replacement for the Ada slot `pl0600`.
The target model is a single body mesh that contains face, hair, clothing, and
body materials. It does not split the model into body, face, and hair files.

The workflow is defined in [agents/WORKFLOW.md](agents/WORKFLOW.md). Low-token
execution is the default and is described in
[references/low-token-mode.md](references/low-token-mode.md) and
[references/low-token-stages.md](references/low-token-stages.md).

## Target

- Target profile: [profiles/re6-pl0600.json](profiles/re6-pl0600.json)
- Export slot: `pl0600`
- Character: Ada
- Output resource: `nativePC_mod/data/chara/pl/pl0600/model/pl0600.mod`
- Material library: `pl0600_0.mrl`, `pl0600_1.mrl`, `pl0600_2.mrl`
- Physics chains: `pl0600.ctc`
- Collision: reuse the reference `pl0600.ccl`

## Hard Rules

- Keep the project work root and final Mod root inside the source model's
  top-level folder, for example `E:\mod\re6\Lossi\work` and
  `E:\mod\re6\Lossi\Lossi_Ada`.
- Never overwrite a successful stage, the source model, or the source `.blend`.
- Import the source through `scripts/source_import.py`. Supported source
  adapters are MMD, FBX, glTF, VRM, Blender, and OBJ.
- Detect the source rig with Modding Toolkit `auto_detect_preset`. If no
  preset reaches 95% coverage, run Stage 1B and normalize only the main bones
  to MMD names. Physics bones keep their names and hierarchy until Stage 3.
- Stage 1 calibrates the source, separates materials, performs the cylindrical
  face-normal pass on the face group, then merges the face back into the single
  body part.
- Stage 2 aligns the RE6 `pl0600` target skeleton by shoulder-bone height.
  The RE6 skeleton uses numbered names; use
  [assets/RE6/RE6.json](assets/RE6/RE6.json) for bone semantics.
- Stage 2 also rotates every finger phalanx inward by 20 degrees, leaves
  `thumb_01_L` and `thumb_01_R` unrotated, then deletes the finger bones and
  merges their weights into the corresponding hand.
- Stage 3 processes physics bones. Rename them to `RE6Bone_<id>`, use ids
  `213-249` first and then `135-189`, and keep the total physics-bone count at
  or below 92. Merge unneeded physics bones into their parents before using
  Modding Toolkit chain simplification.
- Preserve the AI-reviewed chain choice. Prioritize hair, skirt, and breast
  chains in that order, but retain other useful chains when the 92-bone budget
  allows it.
- Stage 5D is optional and runs before Stage 5C. It creates one outline shell
  for the whole body, follows the skeleton normally, and does not lock the
  shell to a single bone.
- Stage 6 builds the `.mrl` from the original
  [assets/RE6/pl0600_0.mrl](assets/RE6/pl0600_0.mrl) material `68b3d093`.
  Keep the original command block, constant buffers, shader flags, and header.
  Bind:
  - `2266034a tNormalMap`: `<material>_NM`, or shared `Null_NM`
  - `75a5334b tDetailNormalMap`: empty
  - `cd06f347 tAlbedoMap`: `<material>_BM`
  - `ff5be348 tAlbedoBlendMap`: empty
  - `57c1c3d5 tAlbedoBlend2Map`: `data\chara\textures\pl_Blood_BM`
  - `0ed1b35a tSpecularMap`:
    `data\chara\pl\pl0600\model\pl0600_04shirt_MM`
- Stage 7 reduces every base color image by 40 percent before BC1 encoding.
  Base color textures are BC1 and at most 2048 on either axis. Normal maps are
  BC3 DXT5nm and at most 2048. Materials without a normal map share
  `Null_NM.tex` converted from
  [assets/RE6/Null_NM.dds](assets/RE6/Null_NM.dds).
- Stage 8 writes only the `.ctc`; reuse the reference `.ccl`. The angle limit
  interpolates from 15 degrees at the first node to 60 degrees at the last.
  Remove any existing CTC plugin objects, rebuild the chains as RE6 Model
  Editor CTC objects, and export them with `re6_ctc.export_re6_ctc`. Do not
  serialize the `.ctc` directly from the Stage 8 script.
- Stage 9 copies the reference mod tree, replaces the `.mod`, `.mrl`, and
  `.ctc`, deletes the reference textures, copies only our textures, reuses the
  reference `.ccl`, and writes `modinfo.ini` with `author=GZYV`.
- Treat `FINISHED` from a Blender operator as insufficient. Re-parse the
  final `.mod`, `.mrl`, and `.ctc` and validate material hashes, texture path
  resolution, and file placement.

## Execution

Create a project:

```powershell
python scripts/create_project.py `
  --profile profiles/re6-pl0600.json `
  --source "E:\mod\re6\Lossi\Lossi.pmx" `
  --work-root "E:\mod\re6\Lossi\work" `
  --mod-name "Lossi"
```

Check the environment and inspect the source:

```powershell
python scripts/check_environment.py `
  --profile profiles/re6-pl0600.json `
  --project "E:\mod\re6\Lossi\work\lossi\project.json"

& "D:\steam\steamapps\common\Blender\blender.exe" `
  --background --factory-startup `
  --python scripts/inspect_source.py `
  -- `
  --source "E:\mod\re6\Lossi\Lossi.pmx" `
  --project "E:\mod\re6\Lossi\work\lossi\project.json" `
  --report "E:\mod\re6\Lossi\work\lossi\reports\source_inventory.json"
```

Run stages in the order defined by [agents/WORKFLOW.md](agents/WORKFLOW.md).
Stop at any manual review trigger.

