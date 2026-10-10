# RE6 Character Replacement Workflow

This is the authoritative Stage 0-9 workflow for the Resident Evil 6
`pl0600` Ada replacement. Every stage writes a new `.blend` and JSON report.
Never overwrite a successful stage.

## Stage 0: Environment And Source Inspection

Run `scripts/check_environment.py` with
`profiles/re6-pl0600.json`. It validates Blender 5.2 and every required RE6
asset. The `mmd_tools`, `Modding-Toolkit`, and `re6_model_editor` add-ons are
loaded from `assets/RE6/Tools` inside the skill.

Run `scripts/inspect_source.py` in Blender to produce
`reports/source_inventory.json`. The report records source format, mesh count,
armature count, images, source bound box, Modding Toolkit preset detection,
and source A/T pose detection. Do not modify the source `.blend`.

## Stage 1: Import, Calibrate, Split, And Face Normals

Run `scripts/prepare_split_stage.py`.

1. Import the source through `source_import.py`.
2. Calibrate the uniform scale. For RE6 use shoulder-bone height alignment
   against `assets/RE6/pl0600.mod` unless the project provides a scale hint.
3. Detect the source preset. If no preset reaches 95% coverage, defer main
   bone normalization to Stage 1B.
4. Convert a detected A-pose to T-pose using the source preset path.
5. Separate by materials and assign every material to the single `body` part.
6. Delete materials ending in `+`.
7. Group the face materials, run the cylindrical face-normal pass with the
   same shared-seam and geodesic fade logic used by `process_face_object`,
   then merge the face group back into the single body mesh.
8. Save `stage_01_split.blend` and report scale, source preset, pose,
   material assignments, deleted materials, and face-normal results.

The cylindrical face-normal seam is restricted to the outer boundary loop.
Eyes, mouth, teeth, tongue, and other inner holes must not receive a
boundary/falloff transition. `outer_boundary_perimeter_ratio` selects boundary
components whose perimeter is close enough to the largest outer loop; the
default is `0.75`.

Stop if a material cannot be assigned to the single body part, if the
scale calibration is ambiguous, or if the face/body seam cannot be resolved.

## Stage 1B: Normalize An Unknown Source Rig

Run this stage only when Stage 1 reports
`requires_source_rig_normalization: true`.

Create `reports/ai_source_to_mmd_bone_mapping.json` with:

- `main_bone_mapping`: source main bone to MMD main bone
- `physics_bones`: every source physics bone that must remain unchanged

Run `scripts/normalize_source_rig_stage.py`. Main bones are renamed to MMD.
Physics bones keep their original names, hierarchy, side, numbering, and end
suffixes. If the source was A-pose and its preset was unknown, run MMD
A-to-T conversion after normalization.

## Stage 2: Align The RE6 Skeleton And Clean Fingers

Run `scripts/align_skeleton_stage.py`.

1. Import the target skeleton from `assets/RE6/pl0600.mod`.
2. Resolve bone semantics through `assets/RE6/RE6.json`.
3. Align model height by shoulder-bone height, not by the whole model box.
4. Transfer the source mesh to the aligned target armature.
5. Rotate every finger phalanx inward by 20 degrees. Each bone receives
   20 degrees, not 20 degrees per chain. Resolve the sign per hand from the
   palm-plane normal and require every fingertip to move toward the palm;
   never trust mirrored local-axis signs without this validation.
6. Do not rotate `thumb_01_L` or `thumb_01_R`, but still delete them.
7. Delete all finger bones and merge their weights into `hand_L` and
   `hand_R`.
8. Save `stage_02_aligned.blend` and report the alignment metrics, finger
   rotations, deleted bones, and merged bone names.

## Stage 3: Physics Bones, Rig Preparation, And Rig Validation

Run `scripts/prepare_rig_stage.py`.

1. Create the single `pl0600.mod` collection and its armature and mesh.
2. Identify every physics bone and classify its semantic category. Hair,
   skirt, and breast chains have the highest retention priority.
3. Rename retained physics bones to `RE6Bone_<id>`. Allocate ids from
   `213-249` first and then `135-189`. Keep the total physics-bone count at
   or below 92.
4. Merge physics bones that do not need their own chain into their parent
   bones before simplification.
5. Use Modding Toolkit `Simplify Selected Chains` on remaining long chains
   when necessary.
6. Record the complete original-to-`RE6Bone_<id>` mapping in
   `reports/re6_physics_bone_map.json`.
7. Validate that every weighted bone exists, every mesh has positive weights,
   and every bone name is ASCII.

## Stage 5C: Final Mesh Cleanup

Run `scripts/finalize_mesh_cleanup_stage.py` after Stage 3.

1. Apply the project material mode.
2. Keep Base Color consolidation enabled unless the project disables it.
3. Confirm every fragment resolves to one material.
4. Remove empty and orphan vertex groups.
5. Confirm no zero-weight vertices remain.
6. Save `stage_05c_mesh_cleanup.blend`.

## Stage 5D: Optional Outline

Run this before Stage 5C when `settings.make_outline` is true.

`scripts/prepare_outline_stage.py` duplicates the complete body mesh, offsets
it by `0.001` along positive normals, flips the normals, and assigns one shared
outline material. The outline shell follows the body skeleton normally.

## Stage 6: Build The MRL

Run `scripts/prepare_mrl_stage.py`.

Use the original `assets/RE6/pl0600_0.mrl` material `68b3d093` as the command
template. Preserve its 38 commands, constant buffers, shader flags, and MRL
header words. For every prepared material:

- assign a unique mod-specific ASCII name and `jamcrc32(name)` hash
- run `re6_mod.rename_meshes`
- bind exactly these slots:
  - `2266034a tNormalMap`: `<material>_NM`, or shared `Null_NM`
  - `75a5334b tDetailNormalMap`: empty
  - `cd06f347 tAlbedoMap`: `<material>_BM`
  - `ff5be348 tAlbedoBlendMap`: empty
  - `57c1c3d5 tAlbedoBlend2Map`: `data\chara\textures\pl_Blood_BM`
  - `0ed1b35a tSpecularMap`:
    `data\chara\pl\pl0600\model\pl0600_04shirt_MM`

Write `stages/pl0600.mrl`, then import it back into the `.blend` so the user
can inspect it. Save `stage_06_mrl.blend`.

## Stage 7: Base Color And Normal Textures

Run `scripts/prepare_re6_texture_stage.py`.

1. Reduce every base color image by 40 percent: multiply RGB by `0.6` and
   leave alpha unchanged. Apply the reduction only once per shared image.
2. Scale base color images to at most 2048 on either axis and encode BC1.
   Materials without a source Base Color image use a generated flat-color
   `256x256` fallback; the outline material uses a black `256x256` fallback.
3. Write `<material>_BM.tex` under the `.mrl` texture directory.
4. Convert a source normal image to DXT5nm BC3 as `<material>_NM.tex`.
5. If a material has no normal image, write one shared `Null_NM.tex` from
   `assets/RE6/Null_NM.dds`.
6. Do not emit an intermediate DDS for source images.
7. Save `stage_07_textures.blend` and report base, normal, fallback, size,
   format, and brightness-reduction data.

## Stage 8: Build The CTC Physics Chains

Run `scripts/prepare_re6_ctc_stage.py`.

1. Verify the input. A normal Stage 7 result has no CTC collection. If an
   existing CTC collection is present, stop unless the user explicitly asks
   to rebuild that modified project with `--rebuild-existing-ctc`.
2. Only with that explicit flag, remove every existing RE6 Model Editor CTC
   object and collection before rebuilding.
3. Create a fresh plugin CTC collection with
   `re6_ctc.create_ctc_collection`.
4. Rebuild non-branching chains from the Stage 3 semantic categories as
   plugin chain, node, and angle-limit-frame objects. Set chain damping to
   `0.02` for every physics chain.
5. Write bone function ids using the `RE6Bone_<id>` names.
6. Interpolate the angle limit linearly from 15 degrees at the first node to
   60 degrees at the last node.
7. Export the plugin collection with `re6_ctc.export_re6_ctc` to
   `stages/pl0600.ctc`, then save `stage_08_ctc.blend`. Do not call the core
   CTC serializer directly.
8. Do not generate a `.ccl`. The final stage reuses the reference `.ccl`.

## Stage 9: Export And Package

Run `scripts/auto_export_re6_stage.py`.

0. Immediately before export, select the non-face meshes in the RE6 mod
   collection and run `re6_mod.set_vertex_format` with `IASkinTB4wt`. Select
   the face meshes and run the same operator with `IASkinTB1wt`. Face meshes
   are identified by `settings.export.face_material_names`, falling back to
   `settings.cylindrical_face_normals.mask_material_names`. Verify that every
   mesh stores the expected `Mod_Mesh_VertexFormat` value. Stop if any mesh
   fails or if a mesh mixes face and non-face materials.
1. Copy the complete reference mod tree from
   `assets/RE6/参考标准mod/RE6 Shinano Official`.
2. Delete the reference's own textures while preserving the target texture
   directory.
3. Export our `pl0600.mod` with `exportAllLODs=true`,
   `useBlenderMaterialName=false`, and `shadowMode=KEEP`.
4. Replace `pl0600_0.mrl`, `pl0600_1.mrl`, and `pl0600_2.mrl` with our MRL.
5. Copy our `.ctc` and reuse the reference `.ccl`.
6. Write `modinfo.ini` with `author=GZYV` and a character-named screenshot.
7. Re-parse `.mod`, `.mrl`, and `.ctc`.
8. Require every `.mrl` material hash to exist in the `.mod`.
9. Require every mod-shipped texture path to resolve to a `.tex` file.
10. Allow only the explicitly preserved original texture paths to resolve
    from the game.
11. Reject missing files, extra files, unregistered external textures, parse
    failures, and material-hash mismatches.

## Manual Review Triggers

Stop and request review when:

- a material cannot be assigned to the single body
- source scale or shoulder alignment is ambiguous
- a weighted bone cannot be mapped or connected
- the palm-normal finger-curl validation fails
- a mesh has zero-weight vertices after rebinding
- the face seam or cylindrical normal field cannot be validated
- a physics chain candidate is branching or semantically ambiguous
- the texture count or format changes unexpectedly
- the final `.mod`, `.mrl`, or `.ctc` validation fails

