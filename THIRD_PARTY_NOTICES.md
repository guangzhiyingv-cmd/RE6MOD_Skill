# Third-Party Notices

This repository is a workflow wrapper around several third-party Blender
add-ons, helper programs, reference resources, and game-derived assets. These
components do not all use the same license.

The files under `assets/RE6/Tools` are bundled so the workflow can run without
separate add-on installation. Their original licenses and notices remain in
their component directories.

## Bundled Blender Add-Ons

### MMD Tools

- Path: `assets/RE6/Tools/mmd_tools`
- License: GPL-3.0-or-later
- License file: `assets/RE6/Tools/mmd_tools/LICENSE`
- Additional notices: `assets/RE6/Tools/mmd_tools/externals/opencc/NOTICE.txt`

### Modding Toolkit

- Path: `assets/RE6/Tools/Modding-Toolkit`
- License: GPL-3.0-or-later
- License file: `assets/RE6/Tools/Modding-Toolkit/LICENSE`
- Additional notice: `assets/RE6/Tools/Modding-Toolkit/NOTICE.md`

The Modding Toolkit notice contains additional details for bundled helper
binaries and third-party components, including `texconv.dll` and
`GDeflateWrapper.dll`.

### RE6 Model Editor

- Path: `assets/RE6/Tools/re6_model_editor-0.3.0`
- License: GPL-3.0-or-later
- License file: `assets/RE6/Tools/re6_model_editor-0.3.0/LICENSE`
- Additional notice: `assets/RE6/Tools/re6_model_editor-0.3.0/NOTICE.md`

The RE6 Model Editor notice identifies the MHW Model Editor project and other
components from which it was adapted.

## Bundled Helper Tools

The following helper tools are included for workflow compatibility:

| Path | Purpose | Local license or notice |
| --- | --- | --- |
| `assets/RE6/Tools/RE6 SideLoader` | RE6 SideLoader files | See the component directory |
| `assets/RE6/Tools/RE6_SideloadConverter` | Converts ARC mods to loose files | `LICENSE.txt`, `README.txt` |
| `assets/RE6/Tools/RE6-ARC-Studio` | ARC inspection and extraction tool | See the upstream distribution |

Where a component has no local license file, this repository does not grant a
new license for that component. Consult the upstream project and comply with
its terms.

## Game-Derived Assets

The repository includes RE6 templates, reference resources, extracted game
files, and reference mod data used by the automated stages. These assets are
derived from Capcom game content.

This repository does not claim ownership of those assets and does not grant a
license to them. They are included only to support non-commercial modding of a
game the user already owns.

Do not redistribute game-derived assets commercially. Rights holders can open
an issue if they want a bundled asset reviewed or removed.

## Generated Mods

The workflow may export a Mod containing geometry, textures, or other content
derived from the source model and the game. You are responsible for ensuring
that you have permission to use and distribute the source model, textures,
character likeness, and any other content included in the generated Mod.
