# Low-Token Mode

Low-token mode is the default. It reduces context reads and command output
without reducing stage correctness.

## Context Rules

Read only:

- this file
- `references/low-token-stages.md`
- the project manifest
- `reports/run_state.json`
- the current stage report and the artifact needed for the next stage

Do not load the complete [WORKFLOW.md](../agents/WORKFLOW.md) up front. Read
only the matching section when a stage fails, a rule is ambiguous, or the
compact index does not cover the decision.

## Command Output

Run stage commands through `scripts/run_logged.py`. Full output goes to
`reports/logs/`; only the compact JSON summary is returned to the conversation.

```text
python scripts/run_logged.py \
  --project work/<project>/project.json \
  --stage stage_06 \
  --input work/<project>/stages/stage_05c_mesh_cleanup.blend \
  --output work/<project>/stages/stage_06_mrl.blend \
  --report work/<project>/reports/stage_06_mrl.json \
  --next-stage stage_07 \
  -- <stage command>
```

Inspect the full log only when the recorded error tail is insufficient.

## Re-Import Policy

Do not perform verification-only re-imports during working stages. Stage 9 may
write, hash, and parse the final resources, but it must not re-import them into
Blender unless the user asks for diagnosis.

Keep all in-memory validation for materials, weights, texture paths, mesh
limits, physics chains, and stage outputs.

## Reporting

After each stage report only:

- stage and status
- output `.blend`
- report path
- blocking error or decision, if any
- next stage

When the user requested a stop after every stage, stop and wait at each stage.

