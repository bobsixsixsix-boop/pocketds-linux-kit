# PDS-014：五次冷启动证据链

日期：2026-08-28

## 目的

一次“开机后测试通过”不能证明连续冷启动稳定，也不能证明五份文件真的来自五次
启动。PDS-014 v2 在既有只读测试、硬件检查、suspend preflight、诊断包和服务前后
稳定门之外，加入两类机器可判定的启动状态，并用不可逆 boot token 支持离线去重。

本次只修改仓库源码并运行 fixture/static 测试。没有重启、休眠、部署、安装或执行
真机 post-boot matrix；五轮结果保持 **NOT RUN**。

## 每轮新增门

### 双屏亮度恢复

只接受当前用户所有、mode 0600、单链接、≤64 KiB、严格 JSON 的
`brightness.json`。schema 和字段必须精确，top/bottom 百分比必须是 5～100 的真整数；
两个 backlight 的 `brightness` 必须分别精确等于 `max_brightness * target / 100` 的
整数下取整结果。矩阵开始和结束都满足才通过。

sysfs 属性的 `st_size` 是伪大小（现场为 4096），读取内容可能只有数个字节；探针以
64 KiB 上限读到 EOF，不把伪大小当真实长度。普通亮度 JSON 仍要求读取字节数与
`st_size` 完全相等，避免放宽持久文件的竞态边界。该差异有专门 fixture 覆盖。

这验证的是“保存的用户目标已恢复”，不会错误要求老开发机也回到首次安装默认 60%。
新状态 60%/60% 仍由亮度状态机自身 fixture 保证。

### Renesas xHCI wake

只读扫描 PCI sysfs，必须恰有一个 vendor `0x1912`、device `0x0014`、绑定
`xhci-pci-renesas` 的控制器，且矩阵开始和结束 `power/wakeup` 都精确为 `disabled`。
缺失、重复、driver 漂移或未知状态均失败关闭。探针不写 sysfs。

## 启动去重与隐私

Linux boot ID 先按 UUID 语法验证，再计算：

`SHA-256("pocketds.post-boot-acceptance.v2" + NUL + boot_id)`

原值不写报告。单轮报告是 mode 0600、拒绝覆盖的私有文件；token 只用于证明五份输入
互不相同。聚合器不会把输入 token、路径、时间戳或原始 boot ID复制到输出。

聚合 PASS 同时要求：

1. 恰好五份当前用户所有、mode 0600、单链接、≤2 MiB 的 v2 报告；
2. 严格 UTF-8 JSON，无重复 key、非有限数或未知字段；
3. 五个 boot token 全不同；
4. 五份报告的 Git revision 完全一致；
5. 每份报告的步骤顺序、状态、门值、complete/result 内部一致且全部 PASS。

聚合器完全离线，不含 subprocess、服务、电源、进程信号或网络入口。输入文件 inode
也必须不同，不能把同一文件通过不同路径重复计数。

## 使用顺序（soak 后）

每次有意冷启动后 15 分钟内，运行一次确认门控的 `make accept-post-boot`，输出到新的
私有文件。收齐五次且中间不改 revision 后，以 `REPORT1`～`REPORT5` 调用
`make evaluate-post-boot-series`。任何单轮失败都保留为证据，不覆盖、不“修”JSON；
修复后必须重新收集完整的同 revision 五轮。

这套证据覆盖 PDS-003 冷启动亮度恢复、PDS-014 冷启动只读矩阵和 PDS-023 的 xHCI
wake 冷启动保持，但不能替代物理 USB 端口/内置输入回归，也不能替代真实 deep resume。

## 2026-08-28 源码与只读现场验证

- 本地最终实现提交：`2a12b7c05d5144e9dbcd62aefc7e27dbc099f849`；
- 设备对应提交：`38232261d9c219f555980f5c9bd189a77234e528`；
- 两端精确 tree：`04266b59979793bffe3d06176ca78d48a435236d`；
- 两端 PDS-014 目标测试 22/22、lint、全量 `make test` 均 PASS；
- 设备只读调用同一源码的 observation collector 得到 brightness target restored=true、
  Renesas xHCI wake disabled=true；没有写 backlight 或 sysfs；
- 验证前后 PDS-008 soak 均为 active/running，同一 PID 162791、同一 InvocationID
  `e6d19f0a75fe4c54ad46c8eb2d9a7faa`、`NRestarts=0`，UI transaction root 不存在。

当前开机已超过 post-boot 窗口，因此没有生成或伪造第一份 v2 报告。五次冷启动聚合
仍是 **NOT RUN**。
