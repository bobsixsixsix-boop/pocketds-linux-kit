# pocketds-initramfs

The busybox initramfs that gets **embedded into the AYANEO Pocket DS
kernel image** via `CONFIG_INITRAMFS_SOURCE`. Single source of truth: the
kernel SRPM (`copr/kernel`) consumes the tarball produced here as one of
its `Source` files; there is no separate `/boot/initramfs.img` on the
device, the cpio is folded into `/boot/Image` itself.

This is **not** a COPR package. Plain git repo, plain tarball, consumed
during kernel build.

## Why this repo exists separately from the kernel SRPM

Two reasons:

1. The Adreno 740 GPU firmware (`qcom/a740_sqe.fw`,
   `qcom/gmu_gen70200.bin`, `qcom/a740_zap.{mdt,bNN,mbn}`) **must** be
   visible at `/lib/firmware/qcom/` from the kernel's POV before
   `switch_root`. `msm.ko` is built into the kernel; it probes during
   early init and asks for these files via `request_firmware_direct()`,
   which reads from whatever `/lib/firmware/...` looks like at that
   moment. Until `init` mounts the rootfs, that's the busybox
   initramfs's view -- so the firmware has to live inside this tree.
   Without it the kernel logs `Direct firmware load for
   qcom/a740_sqe.fw failed with error -2`, the GPU never comes up, and
   `kwin_wayland` aborts on first launch.

2. Decoupling the busybox + firmware payload from the kernel SRPM lets
   us bump either side independently and keeps the kernel's spec file
   small.

## Layout

```
pocketds-initramfs/
├── README.md
├── LICENSE
├── make-tarball.sh                    # initramfs/  -> tar.zst for kernel build
├── refresh-fw-from-ayaneo-zip.sh      # initramfs/usr/lib/firmware/qcom <- AR11
└── initramfs/                         # the cpio source tree
    ├── init                           # busybox PID 1 -- mounts /sysroot, switch_root
    ├── functions
    ├── device.init
    ├── etc/                           # mtab, fstab, nsswitch.conf
    ├── lib -> usr/lib
    ├── lib64 -> usr/lib
    └── usr/
        ├── bin/   busybox, bash, sh, avfsd, bc, rocknix-splash
        ├── lib/   libc, libfuse, libblkid, libmount, ld-linux-aarch64.so.1
        ├── sbin/  fsck.{vfat,ext2,ext3,ext4,fat,msdos}, e2fsck, blkid,
        │          resize2fs (static e2fsprogs 1.47.2, aarch64 -- grows
        │          the rootfs offline in init; see "resize2fs" below)
        ├── share/consolefonts/
        └── lib/firmware/qcom/         # GPU firmware (this is the unique bit)
            ├── a740_sqe.fw
            ├── gmu_gen70200.bin
            ├── a740_zap.mdt
            ├── a740_zap.b00
            ├── a740_zap.b01
            ├── a740_zap.b02
            └── a740_zap.mbn
```

## How the kernel SRPM consumes it

In `copr/kernel/kernel.spec`:

```spec
Source2:        pocketds-initramfs.tar.zst   # produced by ./make-tarball.sh

%prep
...
tar -xf %{SOURCE2}                  # -> ./initramfs/
sed -i -e "s|\"@INITRAMFS_SOURCE@\"|\"$(pwd)/initramfs\"|" .config
```

The kernel's `usr/Makefile` then walks `./initramfs/` with
`scripts/gen_initramfs.sh`, emits `usr/initramfs_data.cpio`, and the
linker script glues it into the kernel image.

Because the tarball already has the firmware under
`initramfs/usr/lib/firmware/qcom/`, no additional spec gymnastics needed
on the kernel side.

## Updating the firmware

```sh
./refresh-fw-from-ayaneo-zip.sh ~/Downloads/AR11_FlatBuild_TurboX_C8550_*.zip
git diff initramfs/usr/lib/firmware/qcom/   # review
git commit -m '...'
```

Then bump `pocketds-gpu-firmware.spec` and run
`copr/pocketds-gpu-firmware/refresh-from-ayaneo-zip.sh` against the same
zip so the rootfs RPM stays in lock-step with the kernel-embedded copy.

## Updating the busybox tree

Re-extract from a fresh ROCKNIX clone (the upstream of this initramfs is
`packages/sysutils/busybox/scripts/init` and the busybox/fsck binaries
the ROCKNIX build process produces). Pattern: rebuild ROCKNIX for SM8550,
copy out `target/initramfs/`, drop it into `initramfs/` here, re-add
`initramfs/usr/lib/firmware/qcom/`, commit.

## resize2fs

`usr/sbin/resize2fs` is NOT from the ROCKNIX tree (ROCKNIX never grew
the rootfs from initramfs). It's a fully static aarch64 build of
e2fsprogs 1.47.2, produced with:

```sh
curl -fsSLO https://www.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v1.47.2/e2fsprogs-1.47.2.tar.xz
tar xf e2fsprogs-1.47.2.tar.xz && cd e2fsprogs-1.47.2
./configure --disable-nls --disable-elf-shlibs LDFLAGS=-static CFLAGS=-O2
make -C lib/et && make -C lib/ext2fs && make -C lib/e2p \
  && make -C lib/support && make -C lib/uuid && make -C lib/blkid
make -C resize resize2fs && strip resize/resize2fs
```

`init` runs it against the root partition before mounting: a no-op boot
costs ~nothing ("Nothing to do!"), and after pocketds-fs-resize.service
has grown the STORAGE partition it grows the filesystem offline, with an
`e2fsck -fy` retry if resize2fs demands a clean fs first.

## Producing the tarball

```sh
./make-tarball.sh                     # -> pocketds-initramfs.tar.zst
./make-tarball.sh 20260507             # -> pocketds-initramfs-20260507.tar.zst
```

CI passes today's date; local builds without an arg drop the suffix --
both forms are accepted by `kernel.spec`.

## License

The busybox `init` script and ROCKNIX-derived helpers are
GPL-2.0-or-later (see `LICENSE`). The firmware blobs under
`initramfs/usr/lib/firmware/qcom/` are Qualcomm-signed proprietary
binaries redistributed under the same OEM-redistributable terms ROCKNIX
uses; see `copr/pocketds-gpu-firmware/LICENSE` for the firmware-only
terms.
