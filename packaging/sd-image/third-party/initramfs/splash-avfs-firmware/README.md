# SD Alpha2：Splash、AVFS 与 GPU 固件说明补充

本包为 `pocketds-kde-kit-20260908-alpha2` 提供相邻的源码与许可说明。它不改变镜像，不替代整个系统的源码包，也不声称重新构建过内嵌程序。固件本身未在本包重复打包。

## ROCKNIX 开机画面

程序作者为 ROCKNIX。官方发行工程 `LICENSE.md` 将团队原创软件与脚本授予 GNU GPL v2；对应 `rocknix-splash` recipe 也将软件包标为 GPL。本包保存这两份官方依据、完整 GPL v2 文本，以及官方源码缓存中精确对应 recipe 所固定提交的源码归档。源码归档缺少独立 LICENSE 文件，不等于没有授权依据。

该源码的 `main.c` 直接含 ROCKNIX 标志的 SVG 路径。ROCKNIX 标志与图像由 ROCKNIX 提供，按 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 授权；本镜像保留上游开机画面，未改动该标志。此署名不表示 ROCKNIX 对本项目的认可。软件 GPL 授权与图像的非商业、署名和相同方式共享条件分别保留。依据见 `notices/ROCKNIX-LICENSE.md`。

## AVFS

提供 AVFS 1.1.5 官方源码及其 COPYING、COPYING.LIB，另附 ROCKNIX recipe 和 patch。源码 SHA-256 与 recipe 一致。所用历史发行工程提交根据现有版本证据选定，尚未证明它就是原 initramfs 的构建提交；这一构建来源限制不否定 AVFS 已有的开源许可。

## Qualcomm GPU 固件

`notices/LICENSE.qcom.txt` 和 `notices/NOTICE.qcom.txt` 保存官方 linux-firmware 固定提交中的完整原文。前者对适用 Materials 提供有限的二进制复制、分发许可，并要求用于 Qualcomm 平台、随附协议和保留 notices。该范围不是针对任意 Qualcomm 品牌文件的笼统授权；NOTICE 本身也不是额外授权。

内嵌 GMU 与官方 linux-firmware 的 `qcom/gmu_gen70200.bin` 完全一致，WHENCE 明确关联上述条款。旧版 SQE 和 AYANEO 签名的 ZAP 则与该通用参考文件不同。字节不同本身不意味着不能分发。

Pocket DS 上游 `LICENSE` 明确断言其 GPU blobs 可在 Qualcomm 授予设备厂商的 OEM 条款下分发；COPR spec 同样声明 Qualcomm 可再分发许可，并说明 AYANEO 签名 ZAP 来自原厂 Android。上述断言以原文及公开来源保存在 `evidence/`，没有被改写成更强的授权。全部七个 GPU 文件均已与 Pocket DS 公共 initramfs 和所取 SRPM 的有效载荷对应。

本次可直接补齐的遗漏是完整 Qualcomm 条款和 notices。仍缺少的具体材料是将这份协议或其他 OEM 协议明确关联到本次旧 SQE、AYANEO 签名 ZAP 的供应方文本；所审阅的固件 SRPM 只有 spec 和固件 tar，未随附该 OEM 条款。这是当前授权范围证据的限制，不能据此断言上游无权分发，也不能把补入通用条款描述为证明所有 OEM 文件授权已完备。

文件和范围对应见 `evidence/embedded-gpu-scope.json`；固定来源与哈希见 `PROVENANCE.json`、`evidence/` 和 `SHA256SUMS`。该说明只涵盖本包列明的三个组件组，不扩展为整张系统镜像的审核结论。
