# ES-DE 原生游戏库导入事务（2026-08-29）

## 目标与格式依据

旧备份来自 Pegasus/Android 风格目录，不能直接让 ES-DE 扫描。按照
[ES-DE User Guide](https://gitlab.com/es-de/emulationstation-de/-/blob/master/USERGUIDE.md)
的原生约定，转换器生成：

- `/home/pocketds/ROMs/<system>/...` ROM 树；
- `/home/pocketds/ES-DE/gamelists/<system>/gamelist.xml`；
- `/home/pocketds/ES-DE/downloaded_media/<system>/{covers,screenshots,videos}`；
- `/home/pocketds/ES-DE/collections/custom-精选集.cfg`。

这四个目录是本事务唯一受管的内容树。`ES-DE/settings`、`custom_systems`、
`logs` 和其他用户文件不属于 inventory，不能随库一起覆盖。设置合并后必须同时满足
`CollectionSystemsCustom=精选集`、`ParseGamelistOnly=true`，以及严格的
`ROMDirectory=/home/pocketds/ROMs`。prepare helper 的结果和 `--check-only` 都报告
实际生效的 ROMDirectory；部署一律传
`--require-rom-directory /home/pocketds/ROMs`，不匹配时在任何写入前失败。
prepare 以同一次预检快照生成两个配置结果，写前做 identity/content CAS，写后重读
ROMDirectory；settings 或 custom systems 任一写入/复核失败时，两个文件一起恢复到预检
快照。父目录包含 symlink 时直接拒绝。

PSX 的真实 gamelist 使用 `.img`，因此受管 `es_systems.xml` 明确声明 `.img`。
安装器在覆盖旧 managed source 前把它作为精确迁移依据：只有 live custom systems 与旧
source 原文或旧 helper 的 home-transformed 输出逐字节相同时才升级；任意用户修改都
保持不动。`install-emulation.sh` 在安装任何文件前先用新 helper/current source 和旧
installed source 执行 `--check-only`，已知 ROMDirectory 冲突不会留下半升级 launcher。
installer 备份根使用微秒时间戳并以单次 `mkdir` 独占；已有 root 或目标都会失败，不会把
同秒重跑静默混入旧回滚证据。
可移植单元测试读取合成 gamelist；另一次只读 real-stage 审计逐一读取 14 个
gamelist，当前所有后缀都受 system 声明支持，测试代码不硬编码 Mac 绝对路径。

## 已完成的本地 stage 与固定身份

私有 stage v2 未因本次加固而重建或修改：

- 14 个系统、22,670 个 ROM、26,320,791,460 ROM bytes；
- 22,670 条元数据、21,189 个媒体文件、4,798,664,918 media bytes；
- 71 条“精选集”，缺失 0；
- 总计 43,874 个文件、31,122,917,449 logical bytes；
- NDS 损坏隔离区排除 1 个文件；
- 43,859 个 stage 文件是硬链接，避免在 Mac 上再复制约 31 GB。

本次导入的两个控制文件身份固定为：

```text
manifest.json  7939c6f985198b6580e2f02fc547ca41320e51c146243ef85f22cac1c392abd8
files.jsonl    0b22badff62c8fa88a3e1f15a4b2c052658f49ebf7cbae7d380e2c8ad8098f98
```

不得在设备上临时计算“期望值”。两个 SHA-256 必须作为命令参数从这份已审阅文档传入；
这样篡改 manifest 和 inventory 后重新配套不能绕过门。

## 设备隔离 stage 验收（未切换 live library）

正确的 v2 stage 已传到受管 home 之外的私有目录
`/home/pocketds-esde-stage-20260829-v2`。顶层目录为当前用户所有、模式 `0700`；
两个控制文件为当前用户所有、模式 `0600`。传输和本节验收没有改动
`/home/pocketds/ROMs` 或 `/home/pocketds/ES-DE`。

设备端逐路径元数据核对结果为：

- expected/actual 均为 43,874 个普通文件，逻辑字节均为 31,122,917,449；
- missing、extra、size mismatch、invalid/duplicate path 均为 0；
- symlink 和 special file 均为 0；
- 8,842 个内部 hardlink group、19,419 个 hardlinked path；
- 验收时剩余 71,731,617,792 bytes 和 9,531,789 inodes；
- 设备端重新计算的两个控制 SHA-256 与上节固定值逐字节一致。

随后从固定 Mac v2 stage 对该设备隔离 stage 运行只读
`rsync -aHcni --delete --itemize-changes`。命令退出 0 且 stdout 为空，因此在该次
读取时源、目标逐文件内容和目录集合一致。这个结果关闭了 Mac→设备 staging 传输错误，
但仍不是持久化的逐文件 digest inventory，也不批准切换 live library。独立事务审查已
明确给出 NO-GO：真实 launcher 尚未持有同一 canonical shared lock，事务路径仍有
ancestor-symlink TOCTOU 等阻断项。修复、Linux 断电矩阵和新的独立审查通过前，隔离
stage 只能保留为只读候选。

## fast 与 full 两级门

`pocketds-esde-library-verify.py --mode fast` 是低成本设备门。它固定 manifest 和
inventory 的 SHA-256，验证 schema、计数、大小、所有者和普通文件类型，并精确遍历
四个受管目录；缺失文件、额外文件、额外空目录、symlink 或 special file 都失败。
它不读取 31 GB 文件内容，报告保持 `content_digests_pinned=false` 和
`trusted_source_pairwise_verified=false`。

`--mode full --source-home <trusted-stage-home>` 在源树和目标树同时本地可见时逐文件读取
并比较 SHA-256，报告 `trusted_source_pairwise_verified=true`。source 与 target 是同一
目录或任一受管文件是同一 inode 时会失败。这个门只证明“目标等于操作员认定可信的当前
source”，inventory 没有逐文件固定 digest，因此必须保持 `content_digests_pinned=false`，
不能描述成固定内容身份。远端设备导入使用等价的组合门：设备端 fast 精确树检查负责
extra-file 检测，Mac 端只读的
`rsync -rHnci --checksum` 负责逐文件内容身份。checksum 命令输出必须为空。full 门会
读取源和目标合计约 62 GB，是导入后的一次性验收成本，不应放进每次启动路径。

开发加固时没有为了证明代码改动而重哈希现有 31 GB stage；只运行 fast 门。正式导入
后必须完成上述 trusted-source pairwise 组合门，未完成前不得启动 ES-DE；完成后也只能
声称设备内容与当时的可信 stage 相等，不能声称每个 ROM 已由固定摘要认证。

## 设备导入顺序

只在遥测 soak 完成且确认 ES-DE 没有运行后执行：

1. 固定设备仓库 revision，确认 `/home/pocketds` 有足够空间，并保存受管四目录和两个
   可能被 prepare 修改的配置文件的存在性、owner、mode、文件数与字节数到账本。
2. 在 `/home/pocketds/.local/state/pocketds-linux-kit/backups/` 建立唯一事务目录；用
   `stat -c %d` 确认它和四个受管目录位于同一文件系统。若任一备份目标已经存在则停止。
3. 用同盘 `mv` 原子移动现有的四个受管目录到备份中的相同相对路径：`ROMs`、
   `ES-DE/gamelists`、`ES-DE/downloaded_media`、`ES-DE/collections`。只复制（不移动）
   原有 `ES-DE/settings/es_settings.xml` 和 `ES-DE/custom_systems/es_systems.xml`，并在
   账本记录原先不存在的路径。移动后四个目标必须为空缺，不能向旧树增量合并。
4. 从固定 stage 的 `home/` 执行
   `rsync -aH --no-owner --no-group <stage>/home/ pocketds@host:/home/pocketds/`。
   不使用 `--ignore-existing`、`--delete`、`--remove-source-files`、`--inplace` 或
   `--partial`；因为目标受管树是空的，任何同名碰撞本身就是事务错误。
5. 以 pocketds 用户把四个受管目录正规化：目录 `0755`、普通文件 `0644`；owner/group
   必须是 `pocketds:pocketds`。manifest 和 inventory 复制到私有 import state，模式
   `0600`，然后以文档中的两个固定 SHA-256 运行设备端 fast 门。
6. 先运行 prepare 的 `--check-only`，再以完全相同参数执行写入：
   `--home /home/pocketds --enable-collection 精选集 --parse-gamelist-only
   --require-rom-directory /home/pocketds/ROMs`。两个 JSON 结果都必须报告实际
   `rom_directory=/home/pocketds/ROMs` 和 `rom_directory_requirement_met=true`。
7. 再运行一次设备端 fast 门；必须得到 43,874 文件、31,122,917,449 logical bytes、
   `metadata_identity_verified=true`、`extra_files_verified=true`、
   `content_digests_pinned=false` 和 `trusted_source_pairwise_verified=false`。
8. 从 Mac 对同一固定 stage 执行不带 `--ignore-existing`、不带 `--delete` 的
   `rsync -rHnci --checksum <stage>/home/ pocketds@host:/home/pocketds/`。标准输出必须
   为空；非空即 full 门失败。
9. 最后才做有界 ES-DE 启动，核对 startup、错误计数、精选集数量、输入模式恢复和
   退出时间；不得把“进程存在”当作可交互 PASS。

## 中断与精确回滚

- 任一门失败都不启动 ES-DE。把失败后的四个受管目录分别用同盘 `mv` 移到唯一的
  `<transaction>-failed/` 证据目录，不递归删除，也不覆盖已有证据。
- 根据事务前的存在性账本处理两个配置文件：把当前版本移动到 failed 证据目录；原先
  存在的版本从备份原子 `mv` 回原路径，原先不存在的则保持不存在。
- 将备份中的四个受管目录逐个原子 `mv` 回原相对路径。每一步都要求目标不存在；任一
  目标被重新创建或备份项缺失时停止人工核对，不能用 `cp` 合并来“修复”。
- 对恢复树重新核对事务前记录的 owner、mode、计数和字节数。stage、备份、failed
  证据、manifest 和 inventory 都保留到启动验收完成后；任何清理另开显式事务。
- 传输中断只允许重新从空目标开始：先按上述流程把部分树移动到 failed，再创建新的
  事务目录。不能依赖重复 rsync 覆盖半成品，也不能用 `--delete` 做回滚。
