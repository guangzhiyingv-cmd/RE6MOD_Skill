RE6 Sideload Converter
======================

把"重新封包 arc、替换原文件"类型的 RE6 mod（.zip）转换成侧载 mod。
Turns a Resident Evil 6 mod that ships repacked .arc files (.zip) into a side-loader mod.

用法 / Usage
  把一个或多个 mod 的 .zip 拖到 "Convert - drop mod zip here.bat" 上。
  旁边会生成 <名字>_sideload.zip，里面是 nativePC_mod/data/...、nativePC_mod/arc/...，可以直接用 mod 管理器安装，
  或者解压到游戏目录（BH6.exe 所在的文件夹）。改了同一个文件的 mod 互相冲突，后装的覆盖先装的。
  Drag one or more mod .zip files onto "Convert - drop mod zip here.bat". It writes <name>_sideload.zip next
  to each input, laid out as nativePC_mod/data/..., nativePC_mod/arc/..., ready for a mod manager or to extract
  into the game folder. Mods that replace the same file conflict; the one installed last wins.

  游戏需要装好 RE6 Sideloader（dinput8.dll）。
  The game needs the RE6 Sideloader (dinput8.dll) next to BH6.exe.

说明 / Notes
  - 不需要安装 Python，也不需要装游戏：runtime\ 是 python.org 官方的嵌入版 Python（未修改），
    app\data\re6_retail_db.json.gz 是零售版条目的路径和 MD5 指纹（不包含任何游戏内容）。
    No Python install and no game install needed: runtime\ is the unmodified official embeddable Python from
    python.org, app\data\re6_retail_db.json.gz holds retail entry paths + MD5 fingerprints (no game content).
  - 只保留 mod 真正改过的文件；每个文件为什么放在那里，见 nativePC_mod/_reports/<名字>.txt。
    Only the files the mod really changed are kept; nativePC_mod/_reports/<name>.txt explains each one.
  - 命令行 / command line:
      runtime\python.exe app\arc_mod_to_loose.py <mod.zip> [-o out.zip] [--name Name] [--folder]
    --folder: 放进 nativePC_mod/<名字>/，作为单独的一个 mod / put the files into nativePC_mod/<name>/ as a separate mod
