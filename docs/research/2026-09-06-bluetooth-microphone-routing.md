# 蓝牙麦克风与内置麦克风噪声排查

日期：2026-09-06。用户确认键盘语音识别恢复正常，随后报告三星 TWS 耳机麦克风无法使用，以及内置麦克风底噪／电流声很大。

## 两个独立的蓝牙阻塞

1. 键盘只把 `pactl get-default-source` 用作麦克风存在性检查，实际始终执行 `arecord -D plughw:0,2`。因此系统选择外置输入也仍录内置麦克风。
2. 运行内核 `7.1.12-pdsdiag.20260905.aarch64` 没有启用 `CONFIG_BT_RFCOMM`，也没有可加载的 rfcomm 模块。BlueZ 明确记录 Hands-Free Voice gateway 的 `socket(STREAM, RFCOMM): Protocol not supported (93)`。已连接的 Galaxy Buds3 广播 Handsfree 服务，但音频卡只有 A2DP 配置，系统没有耳机输入节点。

内置 Qualcomm 直连 ALSA 的历史原因仍有效：其 Pulse/PipeWire 捕获曾触发 Q6APM 启动超时。这不是所有外接麦克风都必须使用内置 ALSA 的理由。

## RFCOMM 恢复

使用设备保留的 `pds001-kernel-build-input/linux-7.1.12` 源码、生成头文件与 Module.symvers。构建前确认该树 `.config` 与 `/proc/config.gz` 完全一致，编译器与原内核相同。仅将原 RFCOMM `core.c`、`sock.c` 复制到独立目录，用外部模块 Kbuild 编译 `rfcomm.ko`；不启用本任务不需要的 TTY 仿真。

构建使用 CPU 0–2、并发 2，结束后原 `.config`、utsrelease.h 和 Module.symvers 哈希均未变化。新模块的 vermagic 与已安装 bluetooth 模块完全一致，唯一模块依赖为 bluetooth，协议别名为 `bt-proto-3`。SHA-256：`93c61dff166905350aaebab9682d357db927c9b072ffb3b4d7464c3bb2b603be`。

模块安装到当前版本的 `/lib/modules/7.1.12-pdsdiag.20260905.aarch64/extra/pocketds/rfcomm.ko`，root:root、0644；更新依赖索引并正常加载。没有强制加载不匹配模块、修改 boot Image、替换内核或重启设备。构建文件和校验记录保留于设备 `~/.cache/pocketds-rfcomm-20260906`。以后重建或升级内核应启用 `CONFIG_BT_RFCOMM=m` 或内建支持；不要将本次二进制复制到不同内核版本。`scripts/check.sh` 新增模块可用性提示。

随后对当前耳机的标准 Handsfree UUID 执行正常连接，BlueZ 返回成功。蓝牙卡新增 CVSD、mSBC 与默认 headset 配置，系统创建 `bluez_input.…` 非 monitor 输入，并自动将它选为默认源；音乐配置当时仍为 A2DP。

## 键盘录音路径

`4ef4754` 在每段开始时读取并验证系统选中的具体源，排除 monitor 与默认源占位符。已知内置源保留原 ALSA 参数；外接源使用绑定具体名称的 parecord，生成 16 kHz、单声道、S16_LE WAV，分片请求 50 ms。每段固定源、录音器类型和 generation，只有内置 ALSA 的有序 SIGINT/return-1 能使用原 WAV 长度修复。

外部流携带 `node.dont-fallback`、`node.dont-reconnect`、`node.dont-move`，目标消失时不改录另一支麦克风。属性语义依据 [PipeWire 官方说明](https://docs.pipewire.org/page_man_pipewire-props_7.html)。现有启动时限、冷却、目标校验和文件保护保持原实现。

WirePlumber 0.5.14 的 `bluetooth.autoswitch-to-headset-profile=true` 已启用。录音期间使用耳麦模式，录音流离开后恢复原配置，是 [WirePlumber 的既有策略](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/settings.html#bluetooth-autoswitch-to-headset-profile)。耳麦模式与音乐模式的带宽不同，不应为了使用麦克风而永久锁在低带宽配置。

实机键盘 189 项回归及音频路由检查通过，七文件事务 `bluetooth-mic-routing-20260906` 已 apply/activate/verify。键盘 PID 296196、active、NRestarts=0，模块协议别名 `bt-proto-3` 已能解析为 rfcomm。一次 50 秒只读音频流观察没有遇到键盘录音，不将其计作耳机逐段录音验收。

随后用户用 Galaxy Buds3 实际测试键盘语音，反馈“可以的，用耳机效果好多了”。耳机语音识别及主观效果验收通过；这不等同于长时间稳定性、断连恢复或每次通话结束恢复 A2DP 的逐项验收，也不代表内置麦克风底噪已解决。

## 内置底噪：已知与未决

用户授权一次 8 秒本地采样：前半安静、后半讲话；录音使用原生 48 kHz、双声道 S16_LE，正常结束，1,536,044 字节。样本留在设备私有目录，只在本机计算统计，没有上传 ASR 服务。ALSA 只读硬件约束查询确认内置 PCM 原生仅支持 48 kHz／双声道，没有启动第二次录音。

两路数字增益目前都是 +16 dB，来自上游 DMIC EnableSeq。样本没有削波；排除首采样瞬态后，底噪约 -31.06 dBFS，后半段仅高约 0.10 dB，暂不能可靠估计讲话信噪比。没有突出的 50/60 Hz 工频及谐波，噪声主要覆盖人声频带。

左右 99.953% 采样完全相同，剩余仅差几个最低位。由于录音已使用硬件原生通道数，不能归因于 plug 的单声道复制；噪声也已经存在于 48 kHz 原始数据，不能归因于键盘的 16 kHz 重采样。需要继续核对讲话时段和 DSP／通道路由，尚不能据此断定物理麦克风损坏。

没有盲目降低增益、关闭一路 DMIC 或增加工频陷波。现有样本下，纯数字降低 8 dB 会使人声与噪声同降，预计并不改善信噪比；选择左／右一路也不能解决几乎相同信号的噪声。

进一步只读核对当前 DTS、VA macro、Q6APM 与 topology，没有发现可定位的同槽映射或位深错误：VA mux0/1 独立，两路映射为 FL/FR、DMA mask=0b11，S16/LSB 对齐一致。硬件约束来自通用后端 fixup，不能用来证明两路物理麦克风均有效。闭源 DSP 行为与板级连接仍未确认，本轮未改混音器、UCM、DSP 映射或数字增益。
