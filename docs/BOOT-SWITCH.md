# Pocket DS 双系统切换

适用范围：AYANEO Pocket DS、ROCKNIX ABL v1.1.8、当前 50/50
Android + Azkali Fedora 分区。

## Linux → Android

在下屏 Panel 点 `切到安卓`，阅读说明后再点 `切换并重启`。后端会先验证机型、
`devinfo` 分区大小和完整模板，保存 4 KiB preimage，只把 `BootMode` 改为 Android，
整块回读一致后才请求重启。独立的 `BootSourceMode` 不会被修改。

## Android → Linux

Android 桌面安装原生应用 `切换到 Linux`（包名
`li.azka.pocketds.dualboot`）。打开后点 `重启到 Linux`，再点 `切换并重启`。
应用通过原厂系统自带的 `/product/bin/xsu` 执行受限操作，不依赖 Magisk、Shizuku、
终端或手工选择脚本。

应用会先验证机型、`devinfo` 大小、完整 ABL 模板，以及当前内置盘上的
`ROCKNIX` 启动分区和 `STORAGE` 数据分区。之后保存 4 KiB preimage，只把
`BootMode` 改为 Linux，保持 `BootSourceMode` 原值，整块回读一致后才重启。

源码位于 `components/android-boot-switch/`，可复现构建入口为
`scripts/build-android-boot-switch.sh`。签名继续使用 2026-08-26 归档的原应用密钥，
密钥本身不进入 Git。

Android 的 `userdata` 第一次启动并完成初始化以前，不能安全地离线预装这个用户
文件；不要为此修改签名 `super`、`boot` 或尚未建立的加密 `/data`。

## 不变量与恢复

- `devinfo` 必须恰好为 4096 字节。
- 归一化 SHA-256 必须为
  `fb560ce40bf17c7fc0dbda4b7e17748eb6e2fd30bbc8281ee0fc95acb28e19da`。
- 允许变化的记录只有 `BootMode` 与被保留的 `BootSourceMode`；一次切换实际只写
  `BootMode` 偏移 `0xa34`。
- Linux 备份位于 `/var/lib/pocketds-linux-kit/boot-switch/`；Android 备份位于
  `/sdcard/PocketDS-boot-switch-backups/`。
- 任一验证失败均不重启。不要用通用 `dd` 命令替代切换器。
