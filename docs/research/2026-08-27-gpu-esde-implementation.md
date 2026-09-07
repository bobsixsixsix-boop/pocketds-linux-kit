# 2026-08-27 GPU 观测与 ES-DE 启动链实施记录

对应 PDS-002、PDS-007、PDS-012。所有 ROM 检查只统计文件元数据；未读取或修改
ROM 内容。没有执行休眠、重启、显示模式切换或内核替换。

## GPU/DRM 基线

新增 `scripts/pocketds-gpu-observer.py`：

- 读取 MSM DRM fdinfo 的 `drm-engine-gpu`、`drm-cycles-gpu`、maxfreq 和 memory。
- 同一客户端的多个 fd 按 `drm-client-id` 去重。
- 输出权限受 umask 077 保护的 JSONL header/sample/summary。
- 仅在观测结束时汇总 journal 中 atomic、GMU、HFI、hangcheck 和 fence 关键词。

首轮命令：

```sh
make observe-gpu DURATION=600 INTERVAL=2
```

结果路径：`test-results/gpu-drm-observation-20260827-210056.jsonl`（Git 忽略）

- 600.001 秒、300 个样本、峰值 6 个 DRM 客户端。
- 峰值可观测 GPU 11.92%。
- atomic commit EBUSY=6；GMU/HFI/hangcheck/fence=0。
- 观测器 CPU=4.423%，不符合 `<0.5%` 目标。

优化与复测：

1. 缓存 fdinfo、每 60 秒全扫描：180 秒 CPU 0.583%。
2. 先检查 `/dev/dri/*` fd：120 秒 CPU 0.496%，余量不足。
3. 新 PID 发现：能捕获 ES-DE client，但高频枚举在压力下 CPU 0.95%。
4. 每 client 只保留一个 fd、缓存进程名、每 10 秒发现新 PID、300 秒全量兜底：
   120 秒/60 样本 CPU 0.268%，ES-DE 新 client 被发现，峰值 client=6、GPU=11.45%。

最终报告：`test-results/gpu-drm-observation-20260827-212525.jsonl`（Git 忽略）。
它达到采样器阶段 `<0.5%` 目标；接入 Panel 后仍需 24 小时与合成负载准确性验收。

## ES-DE 配置迁移

- 新增独立 `make install-emulation`，避免合并设备上含休眠/锁屏实验的脏
  `scripts/install.sh`。
- `pocketds-es-de-prepare` 只在 `ROMDirectory` 为空或缺失时写入 `~/ROMs`；非空
  用户路径优先。修改前备份到 `.local/state/pocketds-linux-kit/backups/`。
- 自定义系统安装到 ES-DE 3.x 实际读取的 `~/ES-DE/custom_systems/es_systems.xml`。
- 迁移前后 ROM 树普通文件均为 174；二次运行 settings/systems changed 均为 false。

## InputPlumber 启动竞态

新增 `pocketds-input-mode`：

1. 显式调用 `SetTargetDevices` 和 `LoadProfilePath`，不调用 toggle。
2. 同时检查 ProfilePath、TargetDevices 和对应虚拟 event name。
3. 状态连续稳定 3 个样本后才允许前端启动。
4. launcher 转发 HUP/INT/TERM，等待子进程退出后恢复原 joymouse 模式。

真机在 20 轮 joymouse→gamepad 往返中无 InputPlumber error/fail/panic，最终模式
为原始 gamepad。mock 测试覆盖非法 mode、信号转发、无孤儿子进程和模式恢复。

## ES-DE 图形冒烟

远程 SSH 首次测试在 GUI 前被 systemd inhibitor 的 polkit 认证拒绝；这是远程测试
上下文，不作为桌面启动失败证据。显式仅为测试跳过 inhibitor 后：

- 首次 ES-DE 启动时间 2517 ms。
- 解析 209 个定义，因 ROM 树只有 `.txt` 而加载 0 个系统/0 个游戏。
- 运行约 30 秒后记录 `ES-DE cleanly shutting down`。
- 新增 core=0、KWin killer=0，输入最终为 gamepad。
- 同期 KWin atomic commit EBUSY=6，证明显示栈异常仍独立存在。

随后补跑 9 次空占位库：9/9 clean shutdown，启动时间 1371～1565 ms，新增
core=0、KWin killer=0、最终输入=gamepad；连同首次结果为空占位库 10/10 通过。
这不能代替合成内容或真实游戏库测试。

日志：`test-results/esde-smoke-20260827-210913.log` 和 `~/ES-DE/logs/es_log.txt`。
两者均保留在设备本地，没有上传。

## 未完成验收

- 合成自由内容与真实游戏库尚未测试。
- 尚未验证 GUI 交互延迟、退出菜单、上下屏/全屏和 suspend 后启动。
- 默认桌面路径仍使用 systemd inhibitor；需要从实体桌面点击验证 polkit 行为。
- atomic EBUSY 的显示矩阵和内核补丁 A/B 尚未开始。
