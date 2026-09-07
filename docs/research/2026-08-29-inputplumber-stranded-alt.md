# InputPlumber 残留 Alt 根因与修复（2026-08-29）

## 症状和只读定位

桌面图标单击会打开属性、右键行为失常，外接键盘已断开，症状仍持续。用 Linux
`EVIOCGKEY` 读取当时所有 `/dev/input/event*` 的当前按键 bitmap 后，唯一残留的
modifier 是 InputPlumber 虚拟键盘上的 `KEY_LEFTALT`；AYANEO 物理输入设备没有
任何 Alt/Ctrl/Shift/Meta 处于按下状态。

这把问题从“触摸、Plasma 桌面或实体键坏了”收敛为虚拟键盘状态泄漏。旧 joymouse
profile 会为任务切换、关闭窗口和全屏分别合成 Alt+Tab、Alt+F4、Alt+F11。若模式或
target 在按钮 release 到达前切换，虚拟键盘可能被保留但 release 丢失，KWin 随后会
把所有鼠标/触摸操作解释为 Alt 修饰后的动作。

## 修复边界

1. Y、Start、LC 不再产生任何 Alt chord，改为 InputPlumber D-Bus action：
   `ui_task_switcher`、`ui_window_close`、`ui_window_fullscreen`。
2. 已有的用户态键盘服务订阅这些 action，并调用 KWin 的原生全局 shortcut；窗口
   动作因此是一次 D-Bus 请求，不存在按下/释放分裂。
3. `pocketds-input-mode set` 和硬件 toggle 在每次切换（包括幂等 set）前先把 target
   缩成仅 D-Bus，销毁虚拟键盘；随后加载精确 profile，再恢复 keyboard/mouse 或
   keyboard/gamepad。KWin 会从输入设备移除得到强制 release 边界。
4. 独立 `install-input.sh` 先备份，只在显式 `--activate-joymouse` 时激活，不重启
   InputPlumber。激活后运行 root-owned 的只读 held-modifier observer，任何设备上
   残留 Alt/Ctrl/Shift/Meta 都让部署门失败。

Select 当前仍是 Meta+Shift+Right 多键映射，用于跨屏移动窗口；本次现场没有残留
Meta/Shift，但它仍是下一轮物理验收的观察项。若出现同类泄漏，应把它也迁到单个
D-Bus action，不能通过延时或重复发送 release 掩盖。

## 已完成和待完成

- 静态 profile 测试拒绝任何 `KeyLeftAlt`/`KeyRightAlt`，并锁定三个 KWin action
  只能走 D-Bus。
- exact-mode mock 锁定“先移除 keyboard target、再 load、再恢复 target”的顺序。
- focused installer 测试锁定备份、显式激活、不重启服务，以及不以 root 执行
  用户可写源码。
- held-modifier observer 的合成 ioctl bitmap/static 测试通过，工具没有写 input、
  uinput、服务或 sysfs 的路径。
- 真机修复部署和部署后的 `--require-clear` 仍须等待当前 24 小时 soak 结束；完成前
  不能把现场症状标为 DONE。
