# 随镜像提供的第三方说明

这些文件随 SD 镜像安装到 `/usr/share/doc/pocketds-linux-kit/third-party/`，并包含在对应的 Kit 源码快照中。它们补充系统软件包已有的 `/usr/share/licenses/` 文件；各组件保留自己的许可。

- `initramfs/components/`：内嵌 BusyBox、glibc、util-linux、e2fsprogs、dosfstools、FUSE 的原始版权与许可说明。具体源码版本和构建证据随 Release 的源码材料提供。
- `initramfs/splash-avfs-firmware/`：ROCKNIX 开机画面与 AVFS 的许可、固定来源及 Qualcomm 固件范围说明。其 README 最初为 Alpha2 整理；这些组件在 Alpha3 中保持相同字节，所以其署名和来源限制继续适用。这里包含完整 Qualcomm 条款，但没有将其改写为对所有 OEM 固件的无限授权。
- `inputplumber-public/`：新 InputPlumber 构建及其 343 个依赖的版权、许可文件和对应说明；镜像中安装到 `/usr/share/doc/pocketds-inputplumber-public/`。
- `fonts/Spleen-BSD-2-Clause.txt`：Spleen 字体的完整 BSD 2-Clause 条款。字体来自 Spleen 2.0.0，ROCKNIX 对 5×8 和 6×12 字体加入 FULL BLOCK 字形；其源码与补丁另附。
- `rpm-source-access/`：1,271 条已安装 RPM 记录及 915 个不同源 RPM 的确切获取位置。913 个远程地址已验证 RPM 文件标识，2 个修改过的源 RPM 随构建材料提供；这不是完整的离线依赖镜像。

ROCKNIX 标志由 ROCKNIX 提供，按 CC BY-NC-SA 4.0 使用；镜像保留原标志，未修改，也不暗示 ROCKNIX 认可本项目。软件许可与图像许可分别适用。InputPlumber 的替换构建、补丁、源码附件和两个安装路径，以当前镜像清单及组件源码锁为准，不能用 RPM 数据库中的原版本替代其来源。

源码材料从项目 [Releases](https://github.com/bobsixsixsix-boop/pocketds-linux-kit/releases) 中对应镜像版本的附件获取。镜像中的源码和清单可用于核对自己使用的版本；若该版本尚未发布附件，本说明不表示附件已上传。
