package li.azka.pocketds.dualboot;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

public final class MainActivity extends Activity {
    private static final String XSU = "/product/bin/xsu";
    private static final String DEVINFO = "/dev/block/by-name/devinfo";
    private static final String FEDORA_BOOT = "/dev/block/sda12";
    private static final String FEDORA_DATA = "/dev/block/sda13";
    private static final int IMAGE_SIZE = 4096;
    private static final int BOOT_MODE_OFFSET = 0xA34;
    private static final int BOOT_SOURCE_OFFSET = 0xA92;
    private static final int LINUX_MODE = 0;
    private static final int ANDROID_MODE = 1;
    private static final String NORMALIZED_SHA256 =
            "fb560ce40bf17c7fc0dbda4b7e17748eb6e2fd30bbc8281ee0fc95acb28e19da";

    private final Handler main = new Handler(Looper.getMainLooper());
    private TextView status;
    private TextView detail;
    private Button switchButton;
    private ProgressBar progress;
    private Probe lastProbe;
    private boolean busy;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!busy) {
            refresh();
        }
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(dp(28), dp(30), dp(28), dp(26));
        root.setBackgroundColor(Color.rgb(16, 23, 19));

        TextView title = text("Pocket DS", 30, Color.WHITE, Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        root.addView(title, matchWrap());

        TextView subtitle = text("切换到 Linux", 24, Color.WHITE, Typeface.BOLD);
        subtitle.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams subtitleLayout = matchWrap();
        subtitleLayout.topMargin = dp(5);
        root.addView(subtitle, subtitleLayout);

        TextView context = text("ROCKNIX ABL · 内置 Fedora", 14,
                Color.rgb(174, 194, 182), Typeface.NORMAL);
        context.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams contextLayout = matchWrap();
        contextLayout.topMargin = dp(10);
        root.addView(context, contextLayout);

        status = text("正在检查启动环境…", 20,
                Color.rgb(120, 214, 164), Typeface.BOLD);
        status.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams statusLayout = matchWrap();
        statusLayout.topMargin = dp(34);
        root.addView(status, statusLayout);

        detail = text("", 14, Color.rgb(218, 231, 223), Typeface.NORMAL);
        detail.setGravity(Gravity.CENTER);
        detail.setLineSpacing(0, 1.25f);
        LinearLayout.LayoutParams detailLayout = matchWrap();
        detailLayout.topMargin = dp(12);
        root.addView(detail, detailLayout);

        progress = new ProgressBar(this);
        LinearLayout.LayoutParams progressLayout = new LinearLayout.LayoutParams(dp(38), dp(38));
        progressLayout.topMargin = dp(22);
        root.addView(progress, progressLayout);

        switchButton = new Button(this);
        switchButton.setText("重启到 Linux");
        switchButton.setTextSize(18);
        switchButton.setTextColor(Color.rgb(16, 23, 19));
        switchButton.setBackgroundTintList(ColorStateList.valueOf(Color.rgb(120, 214, 164)));
        switchButton.setAllCaps(false);
        switchButton.setEnabled(false);
        switchButton.setContentDescription("确认后将设备重启到 Linux");
        switchButton.setOnClickListener(view -> confirmSwitch());
        LinearLayout.LayoutParams buttonLayout = new LinearLayout.LayoutParams(-1, dp(64));
        buttonLayout.topMargin = dp(26);
        root.addView(switchButton, buttonLayout);

        TextView foot = text("只写入 BootMode；不改变启动源，不刷 boot 分区。", 13,
                Color.rgb(151, 170, 159), Typeface.NORMAL);
        foot.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams footLayout = matchWrap();
        footLayout.topMargin = dp(22);
        root.addView(foot, footLayout);
        return root;
    }

    private void refresh() {
        setBusy(true);
        status.setText("正在检查启动环境…");
        detail.setText("");
        new Thread(() -> {
            try {
                Probe probe = probe();
                lastProbe = probe;
                main.post(() -> {
                    status.setText(probe.androidDefault
                            ? "当前默认启动：Android"
                            : "当前默认启动：Linux");
                    detail.setText("ABL：1.1.8\n内置 Fedora：已就绪\n启动源：保持不变");
                    setBusy(false);
                    switchButton.setEnabled(true);
                });
            } catch (Exception error) {
                main.post(() -> {
                    lastProbe = null;
                    status.setText("暂时不能切换");
                    detail.setText(cleanMessage(error));
                    setBusy(false);
                    switchButton.setEnabled(false);
                });
            }
        }, "dualboot-probe").start();
    }

    private void confirmSwitch() {
        if (lastProbe == null) {
            refresh();
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("重启到 Linux？")
                .setMessage("将把默认启动目标设为内置 Fedora，并立即重启。\n\n" +
                        "写入前会备份 devinfo，写入后会整块回读校验。")
                .setNegativeButton("取消", null)
                .setPositiveButton("切换并重启", (dialog, which) -> switchToLinux())
                .show();
    }

    private void switchToLinux() {
        setBusy(true);
        status.setText("正在安全写入…");
        detail.setText("请勿关机");
        new Thread(() -> {
            try {
                Probe before = probe();
                byte[] original = before.devInfo;
                savePrivateBackup(original);

                String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
                        .format(new Date());
                String backup = "/sdcard/PocketDS-boot-switch-backups/devinfo-before-" +
                        stamp + ".img";
                RootResult backupResult = runRoot(
                        "mkdir -p /sdcard/PocketDS-boot-switch-backups && " +
                        "dd if=" + DEVINFO + " of=" + backup +
                        " bs=4096 count=1 conv=fsync 2>/dev/null && chmod 0600 " + backup);
                if (backupResult.exitCode != 0) {
                    throw new IllegalStateException("无法保存启动配置备份");
                }

                byte[] expected = original.clone();
                expected[BOOT_MODE_OFFSET] = LINUX_MODE;
                if (original[BOOT_MODE_OFFSET] != LINUX_MODE) {
                    RootResult write = runRoot("printf '\\000' | dd of=" + DEVINFO +
                            " bs=1 seek=" + BOOT_MODE_OFFSET +
                            " count=1 conv=notrunc,sync 2>/dev/null && sync");
                    if (write.exitCode != 0) {
                        throw new IllegalStateException("BootMode 写入失败");
                    }
                }

                byte[] actual = readDevInfo();
                try {
                    validateDevInfo(actual);
                    if (!Arrays.equals(expected, actual)) {
                        throw new IllegalStateException("启动配置出现了预期外变化");
                    }
                } catch (Exception verificationError) {
                    restoreBootMode(original[BOOT_MODE_OFFSET] & 0xff, original);
                    throw new IllegalStateException("回读校验失败；原配置已恢复");
                }

                main.post(() -> {
                    status.setText("校验成功，正在重启…");
                    detail.setText("下一次启动将进入内置 Fedora");
                });
                Thread.sleep(500);
                new ProcessBuilder(XSU, "sync; /system/bin/reboot")
                        .redirectErrorStream(true).start();
            } catch (Exception error) {
                main.post(() -> {
                    status.setText("没有切换");
                    detail.setText(cleanMessage(error));
                    setBusy(false);
                    switchButton.setEnabled(lastProbe != null);
                    new AlertDialog.Builder(this)
                            .setTitle("操作已停止")
                            .setMessage(cleanMessage(error))
                            .setPositiveButton("知道了", null)
                            .show();
                });
            }
        }, "dualboot-switch").start();
    }

    private Probe probe() throws Exception {
        RootResult identity = runRoot("id");
        if (identity.exitCode != 0 || !identity.text.contains("uid=0")) {
            throw new IllegalStateException("原厂系统的 root 服务不可用；未写入任何内容");
        }
        RootResult device = runRoot("getprop ro.product.device");
        if (device.exitCode != 0 || !"PocketDS".equalsIgnoreCase(device.text.trim())) {
            throw new IllegalStateException("设备不是 AYANEO Pocket DS；操作被拒绝");
        }
        RootResult size = runRoot("blockdev --getsize64 " + DEVINFO);
        if (size.exitCode != 0 || !"4096".equals(size.text.trim())) {
            throw new IllegalStateException("devinfo 分区大小异常；未写入任何内容");
        }
        RootResult layout = runRoot("blkid " + FEDORA_BOOT + " " + FEDORA_DATA + " 2>/dev/null");
        boolean bootReady = layout.text.contains(FEDORA_BOOT + ":") &&
                layout.text.contains("LABEL=\"ROCKNIX\"") &&
                layout.text.contains("TYPE=\"vfat\"");
        boolean dataReady = layout.text.contains(FEDORA_DATA + ":") &&
                layout.text.contains("LABEL=\"STORAGE\"") &&
                layout.text.contains("TYPE=\"ext4\"");
        if (layout.exitCode != 0 || !bootReady || !dataReady) {
            throw new IllegalStateException("没有检测到已验证的内置 Fedora 分区");
        }

        byte[] data = readDevInfo();
        validateDevInfo(data);
        return new Probe(data, data[BOOT_MODE_OFFSET] == ANDROID_MODE);
    }

    private byte[] readDevInfo() throws Exception {
        RootResult result = runRoot("base64 " + DEVINFO + " 2>/dev/null");
        if (result.exitCode != 0) {
            throw new IllegalStateException("无法读取启动配置");
        }
        byte[] data;
        try {
            data = Base64.decode(result.text, Base64.DEFAULT);
        } catch (IllegalArgumentException error) {
            throw new IllegalStateException("启动配置读取结果无效");
        }
        if (data.length != IMAGE_SIZE) {
            throw new IllegalStateException("启动配置长度异常");
        }
        return data;
    }

    private void validateDevInfo(byte[] data) throws Exception {
        if (data.length != IMAGE_SIZE) {
            throw new IllegalStateException("启动配置长度异常");
        }
        int bootMode = data[BOOT_MODE_OFFSET] & 0xff;
        int bootSource = data[BOOT_SOURCE_OFFSET] & 0xff;
        if ((bootMode != LINUX_MODE && bootMode != ANDROID_MODE) ||
                (bootSource != 0 && bootSource != 1)) {
            throw new IllegalStateException("启动配置字段异常");
        }
        byte[] normalized = data.clone();
        normalized[BOOT_MODE_OFFSET] = 0;
        normalized[BOOT_SOURCE_OFFSET] = 0;
        if (!NORMALIZED_SHA256.equals(sha256(normalized))) {
            throw new IllegalStateException("启动配置不是已验证的 Pocket DS ABL 1.1.8 模板");
        }
    }

    private void savePrivateBackup(byte[] data) throws Exception {
        File backup = new File(getFilesDir(), "devinfo-latest-preimage.img");
        try (FileOutputStream output = new FileOutputStream(backup)) {
            output.write(data);
            output.getFD().sync();
        }
    }

    private void restoreBootMode(int mode, byte[] original) throws Exception {
        String value = mode == 0 ? "\\000" : "\\001";
        RootResult restore = runRoot("printf '" + value + "' | dd of=" + DEVINFO +
                " bs=1 seek=" + BOOT_MODE_OFFSET +
                " count=1 conv=notrunc,sync 2>/dev/null && sync");
        if (restore.exitCode != 0 || !Arrays.equals(original, readDevInfo())) {
            throw new IllegalStateException("回读失败，而且无法确认原配置已恢复");
        }
    }

    private RootResult runRoot(String command) throws Exception {
        Process process = new ProcessBuilder(XSU, command).redirectErrorStream(true).start();
        if (!process.waitFor(20, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            throw new IllegalStateException("原厂 root 服务响应超时");
        }
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try (InputStream input = process.getInputStream()) {
            byte[] chunk = new byte[4096];
            int read;
            while ((read = input.read(chunk)) != -1) {
                buffer.write(chunk, 0, read);
            }
        }
        return new RootResult(process.exitValue(), buffer.toString("UTF-8").trim());
    }

    private static String sha256(byte[] data) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(data);
        StringBuilder value = new StringBuilder(64);
        for (byte part : digest) {
            value.append(String.format(Locale.US, "%02x", part & 0xff));
        }
        return value.toString();
    }

    private void setBusy(boolean value) {
        busy = value;
        progress.setVisibility(value ? View.VISIBLE : View.GONE);
        if (value) {
            switchButton.setEnabled(false);
        }
    }

    private TextView text(String value, int sp, int color, int style) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setTypeface(Typeface.create("sans", style));
        return view;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(-1, -2);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String cleanMessage(Exception error) {
        String message = error.getMessage();
        if (message == null || message.trim().isEmpty()) {
            return error.getClass().getSimpleName();
        }
        String clean = message.trim();
        return clean.length() > 240 ? clean.substring(0, 240) : clean;
    }

    private static final class RootResult {
        final int exitCode;
        final String text;

        RootResult(int exitCode, String text) {
            this.exitCode = exitCode;
            this.text = text;
        }
    }

    private static final class Probe {
        final byte[] devInfo;
        final boolean androidDefault;

        Probe(byte[] devInfo, boolean androidDefault) {
            this.devInfo = devInfo;
            this.androidDefault = androidDefault;
        }
    }
}
