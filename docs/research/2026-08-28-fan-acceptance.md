# PDS-009 风扇控制事实与只读验收设计

审计时间：2026-08-28。初始设备事实源提交：`ee537f0`。审计采样没有修改风扇
profile、PWM、服务、浏览器或负载；验收器随后进入设备事实源仓库，但没有安装成
常驻服务。

## 已核实的实现和来源

- `pocketds-fancontrol.service` 处于 `active/running`，`NRestarts=0`，主进程为 root
  Python `/usr/bin/pocketds-fancontrol`，轮询间隔 1.5 秒。
- unit 和原始集成来自签名包
  `pocketds-userspace-20260507-20260730174706.fc44.noarch`；Source RPM 是
  `pocketds-userspace-20260507-20260730174706.fc44.src.rpm`，供应方为
  `Fedora Copr - user linux4switch`，包 URL 指向 ROCKNIX distribution。
- 当前控制器不是包内原样文件：`rpm -V pocketds-userspace` 对它返回
  `S.5....T.`。现场 SHA-256 为
  `9f04ff7113a3e2ea8412fca17a44cd728c20f6cbb7ab5cd463d8f66dd006f0fe`；
  `/usr/bin/pocketds-fancontrol.pre-smooth-20260826` 是旧阶梯控制器备份，SHA-256
  为 `07c8a931672cc90d5033f00a2bbc849ddc30e82b275a5373d5491f3f8efbdcc7`。
  因此不能把当前平滑曲线表述成“上游官方默认”；它是本机可审计的下游修改。
- 当前实现读取 CPU/GPU 热区的最热三点并做 0.5 平滑，目标 PWM 以 8 为量化，
  每 1.5 秒最多升 18、降 3；20% 地板为 PWM 51。瞬时热点仍有安全地板：84°C
  至少目标 153，88°C 至少目标 204，95°C 直接实际 PWM 255。
- 当前 profile 是 `moderate`。运行态同时提供 `temp_c`、`hotspot_c`、
  `target_pwm`、`pwm`。
- 内核 `pwmfan` hwmon 同时提供 `pwm1` 和只读 `fan1_input`。因此本机可以读真实
  tachometer RPM；PWM 不能再被标成 RPM。现场六次 1 秒间隔的空闲点为 PWM 51、
  2021–2052 RPM，只能作为这台机器此刻的点测，不能外推成通用 RPM 曲线。
- 先前 120 秒空闲基线为 40 个样本、40–44°C、目标 PWM=实际 PWM=51，未观察到
  跳变。这只否定“空闲时必然抽风”，没有复现浏览器视频投诉。
- `/dev/video0` 名称为 `qcom-iris-decoder`，`/dev/video1` 为
  `qcom-iris-encoder`。已有另一次测试确认 Chromium 使用 V4L2 stateful decoder；
  本采集器只报告进程是否持有 decoder fd。打开 fd 是支持性证据，不是成功解码
  每一帧的证明；没有观察到 fd 也不能单独证明软件解码。

## 新的默认只读入口

直接运行脚本默认只采一次并输出 stdout，不会在设备落盘：

```sh
./scripts/pocketds-fan-acceptance.py
```

持续观察仍然只读，但样本数必须显式给出：

```sh
make observe-fan SAMPLES=80 INTERVAL=1.5
```

JSONL 把以下来源分开：

- controller state 的 profile、平滑温度、瞬时热点、目标 PWM 和控制器 PWM；
- hwmon 的实际 PWM 和（存在时）实际 tachometer RPM；
- `/proc/stat` 全机 CPU delta；
- 现有 MSM DRM telemetry cache 的 GPU 百分比、频率、温度和明确语义；
- 同用户进程持有 qcom Iris decoder fd 的支持性视频解码证据。

为避免每 1.5 秒遍历一次进程 fd 和 22 个 live CPU/GPU 热区，video-node 与独立
live 热区证据最多每 10 秒刷新一次，每个样本均携带对应 `scan_age_ms`；控制器温度、
PWM、RPM、CPU 和现有 GPU cache 仍按采样间隔读取。hwmon 路径和控制器 hash 只在
启动时发现/计算一次，节点消失后读取会失败关闭，而不会每轮扫描全部 hwmon。

采集器锁定现场控制器 SHA-256。hash 不符、state 超过 6 秒未更新（控制循环四倍）、
关键 state 字段、实际 PWM 或 CPU/GPU live 热区缺失时，仍输出证据，但整体以退出码
2 和 `collector_integrity=FAIL`/`NOT_EVALUATED` 失败关闭。RPM 节点缺失则明确
`available=false,value=null`，不会伪造，也不会使其他采集失效。

采集器只依据已审计控制器代码检查安全地板和 ramp envelope。它不对噪声好坏自动
宣判，最终保持 `acoustic_acceptance=NOT_EVALUATED`。

后续证据安全审计将 GPU cache 读取收紧为当前用户所有、owner-private、
单链接、`O_NOFOLLOW`、非空且 ≤256 KiB 的单 fd UTF-8 JSON；公开权限、
硬链接、符号链接、非对象、损坏或超限 cache 均只报 unavailable。指定
`--output` 时创建新 0600 JSONL 并拒绝覆盖，因为证据可能含进程名。
这些改动不改 profile、PWM、RPM 或采样语义。fixture 与本地全量
`make test` PASS。
源码提交为本地 `6535989`、设备 `1be49ef`。设备单样本只读复验的当前
用户状态为 `quiet`（本轮没有切换）、目标/实际 PWM 51、2052 RPM、热点
40°C、GPU 0%、无 decoder fd，policy PASS 且声学仍 `NOT_EVALUATED`。设备
全量 `make test` PASS；风扇服务保持 PID 568/0 重启，soak 保持原 PID/0 重启。

真机通过 SSH stdin 做了 3 样本功能验证和 8 样本开销点测，均未把脚本写入设备。
8 样本/10.710 秒的 shell time 为 user 0.162 秒、system 0.039 秒，约占一个逻辑 CPU
的 1.88%（约为八核总容量的 0.24%，短测包含 Python 启动成本）。这满足临时诊断的
低影响目标，但不应把本采集器安装成常驻服务；长期遥测仍应复用现有低频 cache。

合入当前事实源前又从仓库脚本执行了一次 8 样本/10.556 秒只读复验：服务保持
PID 568、`NRestarts=0`，profile 全程 `moderate`，目标/控制器/实际 PWM 均为 51，
实际转速 2021--2052 RPM，controller policy PASS，ramp 和安全地板违反均为 0。
本轮没有打开 decoder fd，因而没有把先前的硬解支持性证据冒充为当前视频负载；
声学验收仍为 NOT_EVALUATED。

## 私有噪声标记与离线关联

为避免用户一边听噪声一边手写时间、以及把含进程名的原始 JSONL 直接发出来，
仓库增加 `pds009-fan-correlate.py`。它有两个互相隔离的入口：

```sh
make mark-fan-noise MARKERS=/path/to/private-markers.jsonl
make analyze-fan-noise \
  EVIDENCE=/path/to/private-fan.jsonl \
  MARKERS=/path/to/private-markers.jsonl \
  OUTPUT=/path/to/new-correlation.json
```

`mark` 只向当前用户拥有、0600、单链接、上限 64 KiB 的 JSONL 追加时间点；不会
录音、读取浏览器或访问风扇。`analyze` 离线读取同样受限的 marker 和最大 32 MiB
collector 证据，要求样本时间严格递增、最终 summary 数量匹配、collector integrity
和 controller policy 都 PASS。每个标记必须在指定偏移内同时找到前后样本。

报告只保留标记序号、前后偏移及热点、目标/实际 PWM、RPM、CPU/GPU 和 decoder
状态的变化，不输出绝对时间戳、进程名或视频标识；输出只创建新的 0600 文件并
拒绝覆盖。即使 PWM/RPM 与标记同时上升，它仍固定输出
`acoustic_causality=NOT_DETERMINED`，因为时间相关性不能证明噪声来源。9 项 fixture
覆盖正常对齐、失配、策略失败、非单调/非有限数据、公开/硬链接/符号链接/超限
输入以及不覆盖输出。当前没有真人视频噪声标记，不能据此改风扇曲线。
源码提交为本地 `7756a13`、设备 `078a41e`；本地和设备目标 9 项及全量
`make test` 均 PASS。同步后 24 小时 soak 仍保持 PID 162791、`NRestarts=0`。
本轮没有实际执行 `mark`/`analyze`，因而没有制造或冒充真人噪声证据。

## 未来需要用户在场的视频复现协议

1. 记录当前 profile，并保持外接电源状态、上/下屏刷新率、Chromium 版本、视频
   URL/本地测试片、分辨率和编码格式在各轮一致。测试内容不得含账户或私人媒体。
2. 先执行 2 分钟只读空闲采集，再由用户手动启动同一段 10 分钟视频；采集器本身
   不启动浏览器、不播放声音、不制造负载。把 stdout 从 SSH 保存到另一台机器。
3. 用户在听到一次明显“突然拉高/拉低”时记下时间；报告对齐热点、目标 PWM、实际
   PWM、实际 RPM、CPU、GPU 和 decoder fd。目标 PWM 跳变次数只作测量，不预设一个
   没有证据的“合格次数”。
4. 每轮必须满足可机器判定的事实门：服务无重启；profile 全程一致；state 无缺失；
   控制器 hash 一致；安全地板无违反。相邻采样的 ramp 检查按控制器 1.5 秒周期、
   升 18/降 3 计算，并为采集器与控制器相位差保留一个控制 tick。95°C 直接 255
   是代码规定的例外。
5. RPM 只报告观测范围和与 PWM 的对应，不从现有六点空闲数据发明最小/最大阈值。
   `decoder_fd_observed=true` 只支持“硬解路径被打开”，还需结合浏览器媒体内部页或
   可复现日志确认真实帧解码。
6. 停止视频、关闭测试页，确认温度下降和风扇按每 tick 最多降 3 的代码路径回落。
   无论测试中是否发生意外切档，都要在结束时通过现有 UI/helper 恢复第 1 步记录的
   profile，再读取 `/etc/pocketds-fancontrol/profile` 和运行态 state 复核一致。

拿到这一组用户在场证据后，才能判断投诉来自视频软件解码、温度本身、目标曲线，
还是 PWM 到实际 RPM/声学响应；在此之前不改曲线。

## 发布源码闭环

后续供应链复核确认：签名 SRPM 的 `Source70` SHA-256 是旧阶梯版
`07c8a931…`，现场 `9f04ff71…` 平滑版此前只存在于 `/usr/bin` 和临时工作目录。
现已把与现场逐字节相同的 GPL-2.0-or-later 文件纳入
`packaging/pocketds-userspace/pocketds-fancontrol.pds1`，并扩展 `.pds1` 的确定性
spec 变换。准备器只有在原 Source70、仓库覆盖和 spec 三者都匹配时才会成对输出
新 spec/Source70；binary RPM 审计还会对最终 `/usr/bin/pocketds-fancontrol` 的
0755 模式、11,955 字节和完整哈希失败关闭。签名 SRPM 的完整 67 文件提取树也已由
计数、总字节和规范化 manifest 哈希绑定。13 项 source 与 11 项 binary fixture
PASS；未构建、安装、重启服务或改变 profile/曲线。
