# RE6MOD Skill 简体中文说明

这是一个用于制作《生化危机 6》`pl0600` Ada 角色替换 Mod 的 Blender
自动化工作流。

它可以把 PMX、PMD、FBX、GLB、GLTF、VRM、BLEND 或 OBJ 模型转换为一个
单 Body 的 RE6 Mod，包含脸、头发、服装、材质、贴图和 CTC 物理链。

[English](README.md) | [简体中文](README.zh-CN.md)

## 输出内容

- 一个包含所有可见部件的 `pl0600.mod`
- `pl0600_0.mrl`、`pl0600_1.mrl`、`pl0600_2.mrl`
- BC1 基础色贴图和 BC3 法线贴图
- 使用 RE6 Model Editor 创建的 `pl0600.ctc` 物理链
- 可直接放到 SideLoader Mod 目录中的完整工程
- 每个阶段的 JSON 报告和验证结果

最终目录结构：

```text
<Mod 名称>_Ada/
├─ modinfo.ini
└─ nativePC_mod/
   └─ data/
      ├─ chara/pl/pl0600/model/
      │  ├─ pl0600.mod
      │  ├─ pl0600_0.mrl
      │  ├─ pl0600_1.mrl
      │  ├─ pl0600_2.mrl
      │  ├─ pl0600.ctc
      │  └─ pl0600.ccl
      └─ chara/pl/pl0600/gzyv/
         └─ *.tex
```

## 运行要求

- Blender `5.2.x`
- Python `3.10+`，仅用于最外层启动脚本
- 推荐 Windows 10 或 Windows 11
- 预留足够的空间保存源模型、阶段 `.blend` 和最终输出

不需要执行 `pip install`。启动脚本只使用 Python 标准库，Blender Stage
脚本在 Blender 自带 Python 中运行。

以下 Blender 插件已经包含在仓库中，不需要另外安装：

- `mmd_tools`
- `Modding-Toolkit`
- `re6_model_editor`

插件位于 `assets/RE6/Tools`，由脚本直接从内部路径加载。

## 下载

在 GitHub 页面选择 `Code > Download ZIP`：

<https://github.com/guangzhiyingv-cmd/RE6MOD_Skill>

也可以直接克隆：

```powershell
git clone https://github.com/guangzhiyingv-cmd/RE6MOD_Skill.git
```

## 安装为 Codex Skill

推荐把仓库放入 Codex skills 目录，并将文件夹命名为 `re6-mod-auto`：

```powershell
$skillsRoot = Join-Path $env:USERPROFILE ".codex\skills"
New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null

git clone https://github.com/guangzhiyingv-cmd/RE6MOD_Skill.git `
  (Join-Path $skillsRoot "re6-mod-auto")
```

检查是否安装成功：

```powershell
Test-Path (Join-Path $skillsRoot "re6-mod-auto\SKILL.md")
```

重新加载 Codex，或者新建一个任务，让 Codex 重新扫描 skill。使用时可以在
请求中写 `$re6-mod-auto`，也可以直接要求 Codex 使用 RE6MOD skill。

如果下载的是 ZIP，解压后把整个文件夹复制到：

```text
%USERPROFILE%\.codex\skills\re6-mod-auto
```

## 手动使用

即使不安装为 Codex Skill，也可以直接在 PowerShell 中运行脚本。以下命令
均在仓库根目录执行。

### 1. 设置路径

```powershell
$SkillRoot = (Resolve-Path ".").Path
$env:BLENDER_EXECUTABLE = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"

$Source = "D:\Models\MyCharacter\MyCharacter.pmx"
$WorkRoot = "D:\Models\MyCharacter\work"
$ModName = "MyCharacter"
```

Blender 也可以通过 `PATH`、profile 的 `blender.executable` 或
`--blender` 参数指定。

### 2. 创建工程

```powershell
python "$SkillRoot\scripts\create_project.py" `
  --source $Source `
  --work-root $WorkRoot `
  --mod-name $ModName
```

Project ID 默认使用 Mod 名称的小写 slug。例如 `MyCharacter` 会生成：

```text
D:\Models\MyCharacter\work\mycharacter\project.json
```

常用参数：

```text
--outline                  启用 Stage 5D 描边
--merge-textures           启用支持的贴图合并
--no-merge-by-base-image   关闭 Stage 5C 按基础色贴图合并材质
--execution-mode normal    关闭低 token 模式
```

### 3. 检查环境

```powershell
$Project = Join-Path $WorkRoot "mycharacter\project.json"

python "$SkillRoot\scripts\check_environment.py" `
  --project $Project
```

必须得到 `"ok": true` 后才能继续。

### 4. 检查源模型

```powershell
& $env:BLENDER_EXECUTABLE `
  --background --factory-startup `
  --python "$SkillRoot\scripts\inspect_source.py" `
  -- `
  --source $Source `
  --project $Project `
  --report (Join-Path $WorkRoot "mycharacter\reports\source_inventory.json")
```

报告会记录模型格式、网格和骨架数量、图片、包围盒、骨架预设和姿势检测结果。

### 5. 执行后续 Stage

完整阶段顺序和硬性规则见 [agents/WORKFLOW.md](agents/WORKFLOW.md)。每个
Stage 都使用上一个 Stage 的输出作为输入，并且不会覆盖已经成功的结果。

在 Codex 中使用时，直接要求 `$re6-mod-auto` 执行即可，默认会按照低 token
模式运行。

## Stage 概览

| Stage | 脚本 | 作用 |
| --- | --- | --- |
| 0 | `check_environment.py`, `inspect_source.py` | 检查环境并分析源模型 |
| 1 | `prepare_split_stage.py` | 导入、缩放、材质拆分、面部法线 |
| 1B | `normalize_source_rig_stage.py` | 未知骨架归一化 |
| 2 | `align_skeleton_stage.py` | 对齐 RE6 `pl0600` 骨架并清理手指骨 |
| 3 | `prepare_rig_stage.py` | 创建 RE6 骨架和物理骨映射 |
| 5D | `prepare_outline_stage.py` | 可选描边壳 |
| 5C | `finalize_mesh_cleanup_stage.py` | 材质、权重和网格收尾 |
| 6 | `prepare_mrl_stage.py` | 创建 MRL 并回导检查 |
| 7 | `prepare_re6_texture_stage.py` | 导出 BC1 基础色和 BC3 法线贴图 |
| 8 | `prepare_re6_ctc_stage.py` | 使用插件创建并导出 CTC 物理链 |
| 9 | `auto_export_re6_stage.py` | 导出 MOD 并组装最终 Mod 目录 |

部分 Stage 编号是历史编号，实际顺序以 `agents/WORKFLOW.md` 为准。

## 重要规则

- 目标固定为 `pl0600` Ada，始终只导出一个 Body 模型。
- 脸、头发、服装和身体材质都合并在同一个 MOD 中。
- RE6 使用序号骨骼名，骨骼语义来自 `assets/RE6/RE6.json`。
- 高度以肩膀骨骼为准，不使用整个模型包围盒。
- 每根手指骨向内侧旋转 20 度；`thumb_01` 不旋转，但所有手指骨都会删除并
  把权重合并到 `hand`。
- 物理骨必须使用 `213-249` 和 `135-189` 范围内的编号。
- Stage 8 只生成 CTC，最终复用参考 Mod 的 CCL。
- Stage 9 会重新解析并验证 MOD、MRL、CTC、贴图、材质哈希和文件位置。

## 常见问题

### 找不到 Blender

设置环境变量、传入 `--blender`，或者把 Blender 加入 `PATH`：

```powershell
$env:BLENDER_EXECUTABLE = "C:\Path\To\Blender\blender.exe"
python "$SkillRoot\scripts\check_environment.py" --project $Project
```

### Blender 版本不正确

当前工作流只验证 Blender `5.2.x`。其他版本可能可以加载插件，但没有经过
本仓库的完整验证。

### 内置插件缺失

确认以下目录存在：

```text
assets/RE6/Tools/mmd_tools
assets/RE6/Tools/Modding-Toolkit
assets/RE6/Tools/re6_model_editor-0.3.0
```

如果缺失，请重新下载完整仓库。

### Stage 拒绝覆盖文件

这是有意设计。请创建新的 Project ID、新的工作目录或新的 Stage 输出，不要
直接删除已经成功的 Stage。

### Stage 8 提示已有 CTC Collection

正常流程中 Stage 7 的输出不会包含 CTC。只有明确要重建用户修改过的 CTC
工程时才使用 `--rebuild-existing-ctc`。

### 贴图验证失败

Stage 9 要求每个需要随 Mod 发出的贴图路径都能解析到 `.tex` 文件。检查
Stage 7 报告和生成目录。只有 profile 明确保留的原版贴图才允许从游戏读取。

### 游戏内没有生效

安装 RE6 SideLoader，并把生成的 `nativePC_mod` 目录按 SideLoader Mod
结构放置，同时保留 `modinfo.ini`。

## 目录说明

| 路径 | 内容 |
| --- | --- |
| `SKILL.md` | Codex Skill 入口和硬性规则 |
| `README.md` | 英文下载、安装和使用说明 |
| `README.zh-CN.md` | 简体中文说明 |
| `agents/WORKFLOW.md` | 权威 Stage 顺序和验证规则 |
| `profiles/re6-pl0600.json` | 目标配置和资源路径 |
| `scripts/` | 工程、Blender Stage、验证和导出脚本 |
| `assets/RE6/` | 模板、插件、辅助工具和参考资源 |
| `references/` | 低 token 模式和工程 manifest 文档 |
| `THIRD_PARTY_NOTICES.md` | 第三方组件和游戏数据说明 |

## 第三方组件与授权

本仓库包含 Blender 插件、辅助程序、游戏派生模板、参考 Mod 资源和游戏贴图。
这些内容并不都使用同一种授权。重新发布仓库或输出内容前，请阅读
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 以及每个组件目录中的
`LICENSE` 或 `NOTICE` 文件。

本仓库不对 Capcom 游戏资产主张所有权。请只使用你合法拥有并有权使用的游戏
文件和模型。
