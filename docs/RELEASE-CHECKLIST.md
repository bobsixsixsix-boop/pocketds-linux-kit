# 系统镜像与可安装二进制发行检查清单

系统镜像与可安装二进制版本必须满足或明确阻断以下项目。源码预览按
[源码公开清单](release/SOURCE-RELEASE.md) 检查许可证、隐私、来源及可读性；
公开源码不会自动满足或绕过这里的硬件、签名、救援和完整系统事务验收。

## 安全与可恢复性

- [ ] 没有未解决 P0/P1 问题
- [ ] 安装、升级、重复安装和回滚均在实机验证
- [ ] 分区/Android/Bootloader 方案在副本验证且不会默认删除用户数据
- [ ] 有救援介质、恢复步骤和校验和
- [ ] 无密钥、账号、Cookie、SSID、ROM、BIOS、存档或私有日志
- [ ] 从干净 composition 构建，不从日用 home/根文件系统删数据后打包
- [ ] SSH host key、machine-id、随机种子和网络/VPN 凭据均在首启生成或录入
- [ ] mounted-root audit 对最终挂载镜像返回 0，报告为 0600 并关联镜像摘要
- [ ] `make preflight-composition` 返回 0；只产出 non-flashable rootfs staging，
  builder、项目 RPM、硬化 userspace RPM 与仓库快照均为签名且内容锁定
- [ ] 用锁定且验签的 `pocketds-userspace` SRPM 构建独立 `.pds2` RPM；payload 不含
  `10-wheel-nopasswd`，且 `/usr/bin/pocketds-fancontrol` 精确匹配锁定的现场平滑版；
  不含退休 Onboard 集成/硬依赖；pre/post-sign payload audit、完整依赖/scriptlet/trigger
  安装-卸载-离线重装均通过；完整目标集 321 个 Fedora RPM 已逐包验签且 empty-root
  本地安装通过；322 包 repo metadata 已用固定 revision/mtime/参数独立生成两次并逐字节
  复现，primary/filelists/other 与包归档全等；最终仍须由维护者选择发行密钥、签名项目
  RPM，随后只替换该归档成员、重建两份 repodata、签名 `repomd.xml` 并通过专用
  final-repository receipt 隔离验签；最终 DNF5 必须以 `repo_gpgcheck=true` 完成空根
  smoke，显式 `gpgcheck=true` 和 `skip_if_unavailable=false`，精确核对 322 包并完成
  scriptlet/trigger、`dnf check`、payload contract 和清理；composition repository gate
  必须直接消费该结构化回执，且升级后不会恢复全局免密

## 日用可靠性

- [ ] deep、盒盖、唤醒和长待机达到测试门槛
- [ ] 唤醒后双屏、亮度、Wi-Fi、蓝牙、声音、触摸和手柄恢复
- [ ] GPU 压力和故障恢复通过
- [ ] Panel 可退出、可恢复、无裁切和持续异常占用
- [ ] 键盘或语音至少一套达到日用水平
- [ ] Steam、Chromium 和主要模拟器启动/退出状态恢复

## 证据与文档

- [ ] 每个 DONE issue 有 commit 和测试记录
- [ ] 诊断包和测试矩阵对应发行 commit
- [ ] 版本号、变更日志、已知问题、许可证、源码和第三方来源完整
- [ ] 最终 composition 的 SPDX 3.0.1 SBOM 通过锁定官方 JSON Schema 和 OWL/SHACL
  两层离线验证；验证器回执与 image/package hashes 绑定，资产来源/授权/可再分发逐项闭环
- [ ] `THIRD-PARTY.json` 每项有固定版本、HTTPS 来源、许可证、再分发许可和哈希证据；
  非空占位字段不得通过
- [ ] Chromium 及兼容层五个原包的哈希/签名/提取物已复核；最终 composition 仍须
  带齐许可证、源码提供、SBOM、回执和镜像摘要并重新验收
- [ ] 安装文档可由另一台 Pocket DS 独立复现
- [ ] 发布声明不使用“完美/无 Bug”等无证据表述
