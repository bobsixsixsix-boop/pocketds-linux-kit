# PDS-008 24 小时遥测 soak 结果 — 2026-08-29

## 结论

这次运行完整结束，但**没有通过整体接纳**。最终报告是 schema 2，明确记录
`complete=true`、`completion_reason=completed`、`accepted=false`。唯一为假的
harness gate 是 `one_refresh_pair`：报告观察到 `165.0/59.999` 和
`120.0/59.999` 两组刷新率。schema 2 不记录转换过程，不能用本报告推断第二组模式
出现的原因。

除该刷新率组合 gate 外，遥测读取连续性、服务身份和资源门全部满足。这个结果只能说明
采集链在 24 小时内连续且开销受控，不能说明整机或 GPU 稳定。相同时段的 journal
包含不同类别的 OOB、hangcheck、preemption timeout 和 recovery 记录；它们必须分开
统计，不能把全部 11 条 `recover_worker` 记录都称为 hangcheck。PDS-002 因此继续处于
调查/真实 A/B 阶段。

## 报告身份

- schema：2
- 请求/实际时长：86,400.0 / 86,400.016 秒
- 报告 SHA-256：
  `c09f635b043bd9e20d1fa3425fafdef98102485cce7e87fd0bc44985912c097e`
- 保存副本权限：0600
- `read_only=true`

schema 2 不包含后来 schema 3 新增的逐次 refresh-pair transition 记录。因此这里只能
陈述观察到两组刷新率；不能从该报告虚构转换次数、精确转换时刻或变化原因。

## 连续性与显示结果

| 指标 | 结果 |
|---|---:|
| expected / attempted / read | 43,200 / 43,200 / 43,200 |
| distinct GPU timestamps | 43,200 |
| read failures | 0 |
| stale sample reads | 0 |
| GPU timestamp regressions | 0 |
| maximum GPU timestamp gap | 2,097 ms |
| maximum sample age | 667 ms |
| display statuses | 43,200 × `ok` |
| distinct display timestamps | 1,441 |
| non-null application FPS | 0 |
| refresh pairs | 165.0/59.999、120.0/59.999 |

物理输出刷新率没有被标成应用帧率；`display_app_fps` 全程保持 null。

## 服务身份与资源

| 指标 | 结果 |
|---|---:|
| telemetry PID before / after | 126678 / 126678 |
| restart delta | 0 |
| active/sub state after | active / running |
| current-memory growth | -106,496 bytes |
| telemetry service CPU | 0.2323% of one core |
| soak observer CPU | 0.0298% of one core |

这些数字通过了 harness 的 PID、restart、CPU、observer CPU 和 memory-growth 门。
它们不覆盖 journal 中的 GPU/KWin 故障，也不证明 Chromium、Steam、模拟器或 KWin
在负载下稳定。

## 同窗口 journal 独立只读分类

下面是对同一 24 小时窗口的独立只读 journal 分类，不是 schema-2 soak JSON 内的
字段，也不改变 harness 的 gate map：

| journal 类别 | 计数 |
|---|---:|
| GMU `GPU_SET` OOB timeout | 83 |
| explicit hangcheck | 6 |
| preemption timeout | 5 |
| `recover_worker` | 11 |
| KWin offender + full graphics reset | 6 |
| atomic `EBUSY` | 12 |
| HFI fault/error | 0 |
| fenced-register fault/delay | 0 |
| SMMU fault | 0 |
| runtime GPU fault | 0 |

11/11 recovery sequence 前都有 OOB burst。journal 时刻 13:20:45 以后直到窗口结束，
连续 52,730.946 秒没有新的 OOB 或 recovery。这个长静默段是重要的时序证据，但不能
抵消前段已经发生的 reset，也不能单独证明根因或整机稳定。

进程身份也保持连续：KWin child PID 1011 全程是同一进程；wrapper PID 为 1001，
`NRestarts=0`。这说明这些恢复没有通过更换 KWin 进程或 systemd restart 完成，不代表
GPU 没有发生 recovery/reset。

## Gate 判定

以下 gate 为真：完整结束、attempt coverage、0 read failure、每次读取独立 GPU
timestamp、0 regression/stale、gap 上限、display cadence、仅 `ok` display status、
application FPS 恒 null、服务 active/同 PID/0 restart、服务与 observer CPU 上限、
memory growth 上限。

`one_refresh_pair=false`，所以报告整体必须保持 `accepted=false`。不得把“只有一个
harness gate 失败”改写成“系统基本稳定”：harness 没有把整段 kernel journal 的
83 OOB、6 explicit hangcheck、5 preemption timeout、11 recover_worker、6 KWin
offender/full reset 和 12 atomic EBUSY 纳入 acceptance map。

## 同日 soak 后可见修复部署

以下部署发生在正式 soak 完成后，不能算作 24 小时报告内的验证：

- 六文件 UI 事务：`visible-fixes-20260829-0402`
- UI manifest：
  `56d7e1c6174a1310e52de789103dbe63bd7beec3ae7f920393ead18e157efee6`
- Panel QML：
  `e530d601cd79010e5f6607e49907cb186148d4bb1254662894bdb611f12401da`
- keyboard main：
  `06cd83cf511645651d484b1d02c800fbc0a938ca9647d07981b16b5d1dd41b2a`
- Codex quota collector：
  `06a886319fe83abe703ce1701224ec7409fd142de8a994edd6c30af3a149833b`
- joymouse profile：
  `c758417b5c16033db47d33298e2be41a705ffa725ef114ea30dd7ba1092b8da2`
- input-mode helper：
  `4a56c45ec9781383ac7c02763e06d226a22c98e499a83c470f6f3c27964f6bc2`
- held-modifier observer：
  `3845eac93e9361861df2dcfcf4f54ce6f4f13e98a9e66332ac6bd98c07fd686c`

现场 source/live 哈希一致。quota verifier 得到 fresh `source=app-server`，替换了旧的
stale session fallback；InputPlumber 在 target 重建后报告 `held_modifiers=[]`，且同
PID、0 restart。部署后的设备仓库 HEAD 为 `9f745d0`（tree 前缀 `227e92f1`）。这些
是可见修复的数据面/部署完整性证据；实体 R1/R2 的按下、释放、抖动和实际左右键手感
仍须用户验收。

## 后续门

1. 固定刷新率配置后重跑 PDS-008，要求全程只有一组 refresh pair。
2. 用有救援边界的测试验证 KWin 丢失/恢复时显示字段降级而 GPU 采样继续。
3. 继续 PDS-002 baseline/candidate 的受控 A/B；分别比较 OOB、explicit hangcheck、
   preemption timeout、recover_worker、full reset 与 atomic EBUSY，不合并类别。
4. 用户完成 Panel 额度显示和 R1/R2 实体输入验收，并把结果写入现有私有台账。
