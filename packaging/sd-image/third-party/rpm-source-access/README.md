# 已安装软件包的对应源码

本包给出 SD 镜像软件包数据库中各版本的源码获取位置，供下载、检查和重新构建。它是来源索引，不是完整离线源码镜像。

`RPM-SOURCES.tsv` 的三列为已安装包名、版本/架构、源 RPM 文件名。1,271 条记录中有 2 条为仓库公钥记录，没有源 RPM；其余记录对应 915 个不同的源 RPM。

## 获取方式

在 `RPM-SOURCES.tsv` 找到软件包的源 RPM 名称，再在 `SOURCE-RPM-LOCATIONS.tsv` 查找同名条目：

- 907 个 Fedora 原版源 RPM 对应 Fedora Koji 的完整 HTTPS 下载链接。
- 6 个 Pocket DS 上游源 RPM 对应 `linux4switch/pocketds` COPR 的具体构建目录。
- 2 个本项目修改过的源 RPM 位于同一 Release 的 `build-materials` 附件内，见 `LOCAL-SOURCE-RPMS.json` 的相对路径、文件大小和完整 SHA-256。

Fedora 用户也可用 `koji download-build --arch=src <名称-版本-发行号>` 获取指定版本；不要用当前最新版替代索引中的版本。参考 [Fedora Koji](https://fedoraproject.org/wiki/Koji) 和 [Fedora 源 RPM 获取说明](https://fedoraproject.org/wiki/Building_a_custom_kernel/Source_RPM)。COPR 构建方式见[官方重建说明](https://docs.pagure.org/copr.copr/user_documentation/reproducing_builds.html)。

源 RPM 包含打包规范、上游源码和下游补丁。可在隔离的 Fedora 开发环境中用 RPM 工具展开源包并按其 spec 构建。实际编译还需要各 spec 的构建依赖；本索引不声称包含所有依赖的离线副本。

## 镜像中另外安装的组件

软件包数据库中的来源描述原 RPM。镜像另外集成的 Pocket DS Kit、定制内核和模块、以及替换后的 InputPlumber，以镜像最终安装清单、源码锁和对应的源码附件为准；不能仅用数据库中原 RPM 的源包说明这些替换文件。InputPlumber 的两个命令路径如经同一构建覆盖，会在最终清单中分别记录。

系统软件包自带的许可证保留在镜像内各自的 `/usr/share/licenses` 等路径；内嵌 initramfs、固件和其他补充组件另附完整 notices 与源码材料。项目原创代码的许可证不会改写第三方条款。

## 核对记录

2026-09-08 已逐个请求上述 913 个远程地址，确认可以响应并返回 RPM 文件标识；这不是对远程完整文件逐一下载后的 SHA-256 核对。两个本地修改的源 RPM 已按完整内容计算 SHA-256。详情见 `SOURCE-ACCESS-RECEIPT.json`。

上游服务器可能清理旧构建，仓库也不是不可变快照。需要长期复现时，请连同具体源码包和构建依赖保存副本；遇到条目失效可按公开仓库贡献指南报告，勿附个人配置或密钥。
