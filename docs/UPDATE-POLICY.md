# Pocket DS 软件包与内核更新策略

本文件回答“这台机器能否像普通 Fedora 一样更新”。结论是：普通用户态包可以在
审计后分批更新；内核、固件和 Pocket DS 硬件栈不能混进普通全量更新。

## 为什么不能直接全量更新

当前设备是 Fedora 44/aarch64，但不是标准 Fedora 启动链：

- 根文件系统为 ext4，`/boot` 为 VFAT，没有 Btrfs 快照、UEFI、GRUB 或 BLS 回退；
- ROCKNIX ABL 直接加载 VFAT 上的 `\boot\Image`；
- 运行内核来自 `linux4switch/pocketds` COPR 的单体 `kernel` RPM，系统没有标准的
  `kernel-core`、`kernel-modules`、`kernel-modules-core` 包；
- 当前安装代际的唯一启动镜像为 `/boot/boot/Image`；窄安装器要求旧 Image/boot.img 别名不存在。历史多别名流程不能用于此代际；候选须按对应 manifest 绑定镜像、DTB 与回退点；
- 当前 A740 GPU 故障的首要嫌疑就在定制内核 IFPC 差异，`linux-firmware`、Mesa、
  libdrm、KWin 或 Qt Wayland 变化也会改变故障矩阵。

现有 Fedora/updates 已固定到日本 JAIST 镜像并启用 RPM GPG 检查；Pocket DS COPR
仍是单独启用的第三方构建源。系统不使用 versionlock；所有 DNF 前端必须加载仓库
管理的全局 exclude。唯一完整集合由下文 evaluator 的全部受保护规则生成：硬件环四项
`kernel*`/`pocketds-*`/`linux-firmware*`/`qcom-firmware*`，再加 Mesa、libdrm、
libglvnd、VA-API/VDPAU、KWin/Plasma、Qt/KWayland/Wayland/Xwayland、Xorg server/
drivers 和 Vulkan 对应的 18 个 glob，再加启动基础设施的 17 个 glob，共 39 项。启动基础设施包括 `systemd*`、`dracut*`、`fwupd*`、引导器等；即使只更新 `systemd-libs` 也不能归为普通应用。缺少任意一项都视为保护未生效，
否则 Discover 或裸 `dnf5 upgrade` 仍会把不同风险等级混在一个事务里。

配置由 `scripts/render-update-protection.py` 从 evaluator 的 `PROTECTED_RINGS` 生成；运行该脚本并将输出保存到 `components/system/90-pocketds-hardware-protection.conf`。测试核对生成结果，避免默认保护和计划审计出现不同边界。

## 三个更新环

| 环 | 包范围 | 默认策略 | 放行条件 |
|---|---|---|---|
| 硬件启动环 | `kernel*`、`pocketds-*`、`linux-firmware*`、`qcom-firmware*`，以及 `BOOT_PATTERNS` 中的 systemd/dracut/fwupd/引导基础设施 | 禁止进入日常事务 | 独立来源/签名/内容审计、离机 baseline、真实 RAM recovery、受监督安装与回退 |
| 图形会话环 | Mesa、libdrm/libglvnd、KWin/Plasma、Qt Wayland、Wayland/Xwayland、Xorg 驱动、Vulkan/VA-API/VDPAU | 单独计划，不自动更新 | 保存精确 NEVRA 与事务，短回归、KWin/GPU 日志、双屏/Chromium/Steam/模拟器验收通过 |
| 普通用户态环 | 不属于前两环的 Fedora 包 | 可预览后小批在线更新 | 事务中无受保护包、无 erase/downgrade/vendor change，更新后做一次相关 smoke test |

任何包一旦被前两环依赖，按更高风险环处理。`--allowerasing`、`--no-gpgchecks`、
禁用 excludes、跳过不可用仓库或未经审查的 vendor change 都不是解决依赖冲突的
捷径，遇到这些需求应停止事务并调查。

## 落地顺序

1. 日用阶段不再把 24 小时 soak 或产品级测试作为普通软件更新前置；只处理实际需要
   的普通用户态包，并保留当前可用 baseline。
2. 由仓库管理的 `90-pocketds-hardware-protection.conf` 通过 DNF5 main
   drop-in 目录让所有前端默认排除硬件启动环（含启动基础设施）和图形会话环；可用窄事务
   `scripts/install-update-protection.sh` 单独安装，也随显式 `make install-all`
   安装。窄事务先备份并用 `dnf5 --dump-main-config` 验证精确生效；安装或验证失败时
   自动恢复原文件（原先不存在则移除新文件），但绝不刷新仓库、解析事务、下载或更新
   包。必须看到 39 项精确有效值后才允许规划普通用户态事务；在此之前禁用无人值守
   更新，不从 Discover 点击“全部更新”。默认 glob 覆盖未来 `qcom-firmware-*` 子包
   和现有完整图形环，但不匹配 Chromium、KDE Connect、RetroArch、bash 等普通用户态。
3. 单个明确的普通包可先查看 DNF5 事务摘要，确认只包含预期包后直接执行；多包或依赖
   较复杂时再用 `upgrade --store=PATH` 和下文的只读 evaluator 辅助审计。不要用裸
   `dnf5 upgrade` 或 Discover“全部更新”。
4. 普通用户态更新后只确认包版本和对应功能能启动/执行一次；若出现真实问题，用独立
   DNF history 事务尝试回退并记录到 `KNOWN-ISSUES`，不预先构造压力矩阵。
5. 图形会话环一次只推进一个成组变量，保留原版本和反向事务；只做双屏、Chromium、
   视频、reboot/shutdown 的最小 smoke，明显回退即撤回。
6. 内核不走日常 DNF 通道。安装新内核时必须保留旧内核可启动入口和明确救援方式；
   IFPC-only 等候选只做当前日用阶段要求的最小 A/B。

由于根文件系统是 ext4，RPM history 不是整机快照，不能覆盖 `/boot`、`/etc` 和用户
状态的所有变化。每次受监督更新必须另存配置、包清单和引导文件哈希；不把
`dnf history undo` 当作唯一恢复路径。

## 普通用户态事务 evaluator

仓库提供 `scripts/pocketds-userspace-update-plan.py`。它只读取一个 DNF5
`upgrade --store` 目录，绝不调用 DNF5、RPM、网络、sudo 或任何安装接口。它不是
更新器，也没有 replay/install 开关。即使审计通过，JSON 仍固定输出：

```json
{"accepted_for_userspace_review":true,"execution_authorized":false,"execution_state":"NOT RUN"}
```

离线 mock 测试与 evaluator 本身都不会接触 Pocket DS。将来在设备上由人在场生成
计划时，使用一个全新的私有目录；这个步骤会解析仓库并下载 RPM，但不会安装：

```bash
umask 077
install -d -m 0700 "$PWD/build"
plan_dir=$(mktemp -d "$PWD/build/userspace-update.XXXXXX")
dnf5 --best --no-allow-vendor-change upgrade \
  --no-allow-downgrade --store="$plan_dir"
python3 scripts/pocketds-userspace-update-plan.py \
  --transaction "$plan_dir" >"$plan_dir/pocketds-audit.json"
```

evaluator 的成功退出只表示“可进入下一轮人工审查”，绝不表示可执行。退出码 `0`
对应 `AUDITED_NOT_RUN`，退出码 `3` 对应 `REJECTED_NOT_RUN`；两者 stdout 都是单个
机器可读 JSON 文档。它执行以下 fail-closed 检查：

- 只接受当前 DNF5 serializer 的精确 `1.0` JSON 形状；pinned serializer 只在对应
  集合非空时输出 `rpms`/`groups`/`environments`，因此缺失是合法的原生形状，伪造的
  空数组、未知字段、重复键、格式漂移和 group/environment 动作全部拒绝；
- 非空事务必须由一对一、同 name/arch 的 `Upgrade` + `Replaced` 组成，只额外允许
  solver 标为 `Dependency` 或 `Weak Dependency` 的新装包；remove、downgrade、
  reinstall、reason change 和显式 user install 全部拒绝；
- `kernel*`、`pocketds-*`、`linux-firmware*`、`qcom-firmware*`、完整图形会话环、
  引导加载器、dracut、shim、fwupd 和 systemd 等受保护包只要出现一个，整个计划即拒绝；
- transaction 与 `packages/` 必须是 owner-only 的 mode `0500`/`0700` 目录；每个入站
  RPM 必须位于其中，是不可被 group/other 写入的普通非链接文件，并记录大小与
  SHA-256；路径逃逸、载荷缺失和重复载荷全部拒绝；
- 整轮审计持续持有 transaction 与 `packages/` 的目录 FD，所有文件都相对这些 FD
  打开；结束时重验目录路径、目录/文件 inode、mode、owner、nlink、size、mtime 与
  ctime。审计中换目录、增删文件或原地改载荷都会使整份报告 `REJECTED_NOT_RUN`，不会
  混合两套目录的证据。

DNF5 的 stored-transaction 格式不是稳定的安全授权协议。当前 evaluator 刻意绑定
format `1.0`、`@stored_transaction(repo)` 与 `@System`；任何未来变化都先拒绝，再由
测试和政策显式升级。JSON 本身没有可靠记录 generator 版本、包 vendor、RPM
签名验证结果、NEVRA 与载荷内容的绑定、磁盘余量或执行前的最新 solver 状态，因此
这些字段始终是 unresolved gates。**禁止仅凭 evaluator 的成功报告运行
`dnf5 replay`。** 真正安装流程必须另行设计并再次验证这些门，本阶段明确为
`NOT RUN`。

解析边界也刻意不做“兼容猜测”：旧 DNF/DNF4 的 transaction JSON 即使同样声称
`version: 1.0`，其 `Upgraded`/`Removed` 动作、全小写 reason 和 repository 语义都与
当前 DNF5 `upgrade --store` 产物不同，因此一律拒绝；DNF5 `history store` 因为没有
绑定本地下载载荷也一律拒绝；`--assumeno` 的表格、日志或任何截取的终端文字不是
JSON，更不会进入解析器。对这些输入不会降级为模糊匹配，结果只能是
`REJECTED_NOT_RUN`。

## 2026-08-29 现场保护与普通更新 smoke

本节保留当时的历史证据：当时为 22 项保护与多别名启动布局，不是 2026-09-06
新增 39 项源码策略的部署证明。新规则仍需单独安装并读取有效配置。

当时先部署并核对默认保护层：

- 仓库源文件与 root 安装文件的 SHA-256 都是
  `c6c4dda8c59290a0d13ce372d3ede9a1435961c8a38dae3cef415d9e568f03fe`；安装文件为
  root:root、0644、单链接；
- `dnf5 --dump-main-config` 的有效值精确为硬件环与图形环的 22 个有序 glob，并保持
  `protect_running_kernel=1`；
- 当前仍是定制单体 kernel RPM；标准 `kernel-core`、`kernel-modules` 与
  `kernel-modules-core` 均未安装，两份启动别名仍是同一个已锁定 baseline；
- `dnf-automatic` 与 DNF5 automatic plugin 均未安装，PackageKit 当时 inactive；
  Discover notifier 存在只表示会提示更新，不能据此点击“全部更新”；
- Fedora、updates、Pocket DS COPR 与 ChatGPT 仓库均启用，且没有 versionlock。

随后完成一次真实、窄范围普通更新 smoke：

- ChatGPT 源继续保持 `gpgcheck=1` 与 `repo_gpgcheck=1`；本机已有公钥指纹
  `3BFA0E4AE8B8CC16A2D9BA684A3B4A566C4660E4`，已导入 RPM 与 DNF5 源密钥库；
- 刷新 Fedora、updates、Pocket DS COPR 与 ChatGPT 四个源成功，不再出现密钥询问
  或 `repomd.xml` 签名错误；候选列表中没有任何受保护包；
- 仅把 `python3-typing-extensions` 从 4.15.0-3.fc44 更新到 4.16.0-4.fc44，DNF5
  事务号 29，未改动其他包；需要时可先尝试 `dnf5 history undo 29`；
- `chatgpt`、BlueZ、curl、Okular 等其余候选均未安装。

这证明日常事务默认不会误带硬件启动环或图形会话环，并且普通用户态窄更新路径已经
实际成功一次；它不表示任一受保护环可更新，也不授权 stored transaction replay 或
blanket upgrade。内核仍只走独立、有旧内核回退入口的路径。

## 当前允许程度

- **可以**：查询更新；预览后按单包或很小批次更新普通用户态包，并做一次相关 smoke。
- **需要单独处理**：ChatGPT、BlueZ、curl 等会影响已保留功能或系统面的候选，一次只
  处理一组，更新后立即验证对应功能。
- **暂不可以**：根据本 evaluator 报告 replay/install、无人值守更新、Discover
  全部更新、Fedora 大版本升级、把图形栈和普通包混更。
- **不可以**：把 COPR 新内核当普通软件包直接升级。真实独立回退尚未验证，当前
  IFPC candidate 仍明确 `candidate_install_authorized=false`。

依据：[DNF5 upgrade](https://dnf5.readthedocs.io/en/latest/commands/upgrade.8.html)、
[DNF5 package filtering](https://dnf5.readthedocs.io/en/latest/misc/filtering.7.html)、
[DNF5 versionlock](https://dnf5.readthedocs.io/en/latest/commands/versionlock.8.html)、
[DNF5 transaction 1.0 serializer](https://github.com/rpm-software-management/dnf5/blob/3cc84ee1540f1e40a29b19414a8f3e8521e5f3ff/libdnf5/transaction/transaction_sr.cpp)、
[DNF5 `--store` implementation](https://github.com/rpm-software-management/dnf5/blob/3cc84ee1540f1e40a29b19414a8f3e8521e5f3ff/dnf5/context.cpp)、
[Fedora system upgrade guidance](https://docs.fedoraproject.org/zh_Hans/quick-docs/upgrading-fedora-offline/)。
