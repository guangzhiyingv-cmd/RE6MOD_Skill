# Low-Token Stage Index

Use this index with [low-token-mode.md](low-token-mode.md). Read the matching
section in [WORKFLOW.md](../agents/WORKFLOW.md) only when a stage fails or a
decision is not covered here.

| Stage | Script | Required result | Stop trigger |
| --- | --- | --- | --- |
| 0 | `check_environment.py`, `inspect_source.py` | RE6 environment and `source_inventory.json` | missing tool/asset, unreadable source, or unsupported adapter |
| 1 | `prepare_split_stage.py` | calibrated single body, face normals applied and merged back, `stage_01_split.blend` | ambiguous material assignment, scale, pose, or face seam |
| 1B | `normalize_source_rig_stage.py` | main bones normalized to MMD, physics bones untouched | missing or invalid AI main-bone mapping |
| 2 | `align_skeleton_stage.py` | shoulder-aligned RE6 skeleton and finger cleanup | unresolved shoulder bone, thumb handling, or finger cleanup |
| 3 | `prepare_rig_stage.py` | single `pl0600.mod` collection, renamed physics bones, physics map, weighted rig | unknown bone role, over 92 physics bones, invalid weights |
| 5D | `prepare_outline_stage.py` | optional whole-body outline shell, run before 5C | outline creation or material failure |
| 5C | `finalize_mesh_cleanup_stage.py` | material mode applied, empty/orphan groups removed, no zero-weight vertices | unresolved material grouping or lost material assignment |
| 6 | `prepare_mrl_stage.py` | 38-command mod materials, exact RE6 slot rules, `stages/pl0600.mrl` | non-English material without mapping, missing template slot |
| 7 | `prepare_re6_texture_stage.py` | base color reduced 40 percent, BC1 `_BM`, BC3 `_NM` or shared `Null_NM` | non-BC1 base, oversized texture, missing base color |
| 8 | `prepare_re6_ctc_stage.py` | plugin CTC objects created from Stage 7 input and plugin-exported `.ctc`; rebuilding an existing collection requires `--rebuild-existing-ctc` | existing CTC without the explicit rebuild flag, plugin creation/export failure, branching path, or non-`RE6Bone_<id>` name |
| 9 | `auto_export_re6_stage.py` | normal meshes set to plugin vertex format `IASkinTB4wt`, face meshes set to `IASkinTB1wt`, final `pl0600.mod`, `.mrl`, `.ctc`, reused `.ccl`, `modinfo.ini`, validation report | vertex-format application or verification failure, missing/extra texture, hash mismatch, parse failure |

Keep the stage `.blend`, stage report, and `reports/run_state.json` after every
successful stage.

