# Pocket DS Android/Linux 共用游戏库

## 目标

同一张 exFAT TF 卡只挂载一次，Android 的 Pegasus G（天马 G）和 Fedora 的
ES-DE 共用唯一一份 ROM。两个前端的列表、封面和运行配置分开保存，避免一端更新时
破坏另一端。未经逐模拟器兼容性验证前，电池存档和即时存档也按系统隔离。

当前执行顺序固定为：先验证卡内唯一 ROM 树与清单，再接入 Fedora 的固定挂载和
ES-DE 系统定义，最后分别做 Linux 启动/退出与 Android 天马 G 分类/启动验收。
任何一步失败都不得通过复制 ROM、增加 bind mount 或重新格式化 TF 卡绕过。

## 卡内布局

```text
Roms/                         两端共用、唯一的 ROM 本体
  ARCADE/ FC/ GBA/ ...
    metadata.pegasus.txt      天马 G 按游戏机分类的筛选列表
  精选集/
    metadata.pegasus.txt      天马 G 的 71 款精选列表
PocketDS/
  Frontends/ES-DE/            ES-DE gamelist、媒体和精选集定义
  BIOS/                       将来统一审计后的 BIOS
  Saves/Android/              Android 电池存档
  Saves/Linux/                Linux 电池存档
  States/Android/             Android 即时存档
  States/Linux/               Linux 即时存档
  Manifests/                  共享库身份与逐文件 SHA-256
```

Android 由系统把卡挂载为 `/storage/<UUID>`，天马 G 自动发现卡根目录的 `Roms`。
Fedora 固定只挂载一次到 `/mnt/pocketds-games`；ES-DE 的 ROM 根为
`/mnt/pocketds-games/Roms`。目录采用天马生态常见的大写名称，Linux 使用专用
`es_systems.xml` 映射，不创建一堆 bind mount 或软链接。
ES-DE 通过官方 `--home /mnt/pocketds-games/PocketDS/Frontends` 参数直接读取卡内
`ES-DE/` 的 gamelist、封面和精选集；模拟器入口仍使用 Fedora 用户目录下的绝对路径。

## 当前全量内容

2026-09-04 已由固定的 v2 私有备份清单导入全量库：22,670 个 ROM、14 个主机分类、
21,189 个媒体文件和 71 个精选条目。精选集只引用各主机目录中的原文件，不复制 ROM；
Android 天马 G 和 Linux ES-DE 也共用相同的封面、截图和视频文件。当前分类如下：

| 分类 | ROM 数 | 分类 | ROM 数 |
|---|---:|---|---:|
| ARCADE | 24 | FC | 874 |
| GBA | 3,969 | GBC | 1,331 |
| MAME2003 | 408 | MD | 5,001 |
| N64 | 258 | NDS | 32 |
| NEOGEO | 327 | NES | 7,639 |
| PCE | 404 | PS1 | 9 |
| PSP | 9 | SFC | 2,385 |

源 manifest SHA-256 为
`7939c6f985198b6580e2f02fc547ca41320e51c146243ef85f22cac1c392abd8`，源 inventory
SHA-256 为 `0b22badff62c8fa88a3e1f15a4b2c052658f49ebf7cbae7d380e2c8ad8098f98`。
构建器只接受这两个固定身份和清单中的 43,874 个文件；备份目录后来出现的 2,578 个
Finder 杂项/重名截图不在清单中，已明确排除。构建器不包含 ROM，调用方式为：

```bash
scripts/pocketds-full-library-overlay.py \
  --source-stage /path/to/esde-library-stage \
  --pegasus-system-metadata /path/to/platform/metadata/root \
  --android-volume-id 9C33-6BBD \
  --expected-manifest-sha256 7939c6f985198b6580e2f02fc547ca41320e51c146243ef85f22cac1c392abd8 \
  --expected-inventory-sha256 0b22badff62c8fa88a3e1f15a4b2c052658f49ebf7cbae7d380e2c8ad8098f98 \
  --output /path/to/output
```

NDS 条目使用 `WatermelonDS Pocket DS`，并把每个 ROM 的 Android SAF 地址在构建时写进游戏条目；
天马 G 只传递普通字符串参数，由模拟器包内的 Pocket DS 入口把游戏窗口启动到下屏，
WatermelonDS 再把 DS 上画面单独送到上屏。不能直接让天马 G 用 `--display 2` 启动另一个
应用，也不能把 SAF 地址放进 `-d`，前者会被 Android 忽略，后者会触发跨应用 URI 权限检查。
入口在游戏运行期间覆盖天马上屏，并在退出游戏后等待 2 秒再恢复天马，避免 Android 同时
拆除下屏任务和恢复 Qt 窗口时触发崩溃。专用 APK、固定源码版本和可重放修改保存在
`build/android/` 与 `components/emulation/android-watermelonds-pocketds/`。
`--android-volume-id` 必须与该卡在 Android `/storage` 下显示的 ID 一致；换卡或重新格式化
后若 ID 改变，需要重新生成元数据。

部署后的 `/mnt/pocketds-games/PocketDS/Manifests/files.sha256` 可在 TF 卡根目录直接
运行 `sha256sum -c`。不得通过格式化卡来部署；应先复制进同文件系统 staging、完成
全量 SHA-256，再原子切换 `Roms` 与 `PocketDS/Frontends`。SteamLibrary、Android
标准目录、BIOS、存档和即时存档不属于本次全量库事务。

## 验收状态

- 卡内共享树：已部署。当前卷标识 `9C33-6BBD`；43,890 个受管文件、
  31,128,052,235 bytes 的逐文件 SHA-256 全部通过，目标 inventory SHA-256 为
  `983c58672495983ff74ad0ac2ba3f193a33557f95e6034f8576aec575b22a9f0`。卡当前占用
  44 GiB，可用 195 GiB；旧的 71 款精选库保存在
  `PocketDS/Backups/full-library-preimage-20260904-212549-7939c6f9`。
- Android：14 份 Pegasus 主机元数据、22,670 个 ROM 引用和 21,189 个媒体引用已
  全量做存在性检查。NDS 的 32 个条目均使用本卡 ID 的精确 SAF URI 和
  WatermelonDS Pocket DS 入口，旧 DraStic 入口为零。Android 端天马 G 的实际分类
  显示和选游戏启动仍需切回 Android 后由用户目视验收。
- Fedora：共享卡已固定单次挂载到 `/mnt/pocketds-games`；ES-DE 3.4.1 ARM64、
  共享库配置和专用系统定义均已部署。真实 KDE 会话加载 14 个主机分类、精选集和
  22,644 个可见游戏，启动耗时 5,124 ms，零 error/fatal/解析失败；与清单相差的
  26 项是 ES-DE 在街机集合中自动排除的 BIOS/设备包，卡上文件没有丢失。截图已确认
  分类界面和上下屏 Panel 均正常；服务停止后无遗留 ES-DE/gamescope 进程或失败单元。
