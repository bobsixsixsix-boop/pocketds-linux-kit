# Android 原厂 SY7758 背光行为对照（2026-08-29）

## 目的与边界

下屏在 Linux 中即使把 `sy7758-backlight` 拉到最高，主观亮度仍明显偏低。为避免
直接在真机上猜寄存器，本轮只读分析已备份的 Android 原厂 `vendor_boot` ramdisk 中
的 `msm_drm.ko`，并与当前 Pocknix 内核的 SY7758 补丁逐项对照。本轮没有写 I2C、
背光 sysfs、内核或设备树。

原厂模块没有纳入仓库或发行包。用于锁定输入身份的 SHA-256 为：

```text
6f3ac08218a462217917b7cb42d49e81d82f7ec5fdf36cb3bb6e4541b7153402  msm_drm.ko
```

它是未 strip 的 AArch64 relocatable ELF，Build ID 为
`f8b29f471779c8d43cc02095069e2f1820e9db5b`。

## 原厂驱动证据

符号表保留了以下边界：

```text
000000000005c580 0000000000000010 r sy7758vgs_bl_init_reg
00000000001779c8 0000000000000374 T sy7758vgs_bl_device_init
0000000000177d3c 0000000000000188 T sy7758vgs_bl_set_led_current
```

`sy7758vgs_bl_init_reg` 的 16 个字节可直接还原为 8 组寄存器和值：

```text
01 85  10 00  11 00  a5 64  a0 55  a1 9a  a9 80  a2 28
```

这与 Linux 补丁中 `sy7758_init()` 的八次初始化写完全一致；因此当前没有证据表明
Linux 因缺少原厂初始化 magic 而让背光明显变暗。

`sy7758vgs_bl_set_led_current(brightness, max)` 的反汇编还锁定两种编码：

- `max == 0xff`：低寄存器值为 `brightness << 4`，高寄存器值为
  `brightness >> 4`；
- `max == 0xfff`：低寄存器值为 brightness 低 8 位，高寄存器值为
  `brightness >> 8`；
- 其他 max 会使用固定的低值 `0xff`、高值 `0x03`，不是正常 12-bit 路径。

两次 I2C 写的寄存器地址分别是 `0x10` 和 `0x11`。

## 与 Linux 驱动的差异

对照补丁 SHA-256：

```text
a9b2bf64d1fb8bf418e88645577f0aae3b502204bf0ed024fd0d708843762ad0  0060-Add-Silergy-SY7758-backlight-driver.patch
```

Linux 驱动当前声明 `MAX_BRIGHTNESS=4080`，写 `0x10` 时使用
`brightness & 0xf0`，写 `0x11` 时使用 `(brightness >> 8) & 0x0f`。这会丢弃
12-bit 亮度值的最低 4 位，并让最大值成为 `0xff0`；Android 的 12-bit 路径允许
`0xfff`。二者最大编码只相差 15/4095（约 0.37%），不足以解释肉眼明显的亮度差。

因此可把以下改动作为**正确性候选**，但不能当作“下屏偏暗修复”直接发布：

1. 最大值改为 4095；
2. `0x10` 写完整的 `brightness & 0xff`；
3. 用单变量内核候选验证 0、最低安全值、60%、100%、休眠/唤醒和冷启动。

## 结论与下一步

- 已排除“Linux 初始化表与 Android 原厂不一致”这一主要假设。
- 已找到 Linux 12-bit 编码的低 4 位精度缺陷，但其幅度不可能单独造成当前主观差异。
- 下一步优先在不写硬件的前提下继续对照 Android 面板/DCS、显示引擎颜色管线、
  regulator 和实际 I2C 事务；只有得到新的单变量证据后才做可启动候选。
- 任何内核或设备树候选都必须走已有 A/B、回滚镜像和用户在场门禁，不能与 GPU
  IFPC 或电池 unit 候选合并。
