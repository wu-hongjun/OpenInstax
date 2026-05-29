"""Bridge UI localisation.

Single-file translation table keyed by the English source string. Calling
``t("Ready", language)`` returns the Chinese (or any future locale) string
when one is registered, else returns the English source unchanged.

Design choices:

* Source-string keys (gettext style) — adding a new English string costs
  nothing until we want to translate it; the literal in code stays self-
  documenting and a missing translation degrades gracefully.

* Single in-memory dict — at ~150 strings total the bridge UI doesn't
  warrant gettext .mo compilation or a runtime file load. Plain Python
  keeps the deploy path simple (no extra files to scp).

* Frozen at module load — registering at import time means a missing
  translation is a code-review issue, not a runtime surprise.

* No string formatting helpers — callers do ``t("Battery") + ": 95%"``
  rather than f-strings inside the translation, so the key set stays
  small (we don't multiply per-value variants). Helper functions that
  produce composite strings still work because they assemble already-
  translated fragments.

Translation voice for ``_ZH_HANS``: aligned to Apple's iOS 26 Simplified
Chinese system labels. Reuse Apple's exact wording when the bridge offers
a comparable concept (设置, 蓝牙, 电池, 辅助功能, 还原, 浅色/深色/跟随系统),
keep Arabic numerals with units (``5 秒`` not ``五秒``), and avoid
ALL-CAPS / mid-sentence Western punctuation. Proper nouns (Wi-Fi, FTP, BLE,
BlueZ, Python, INSTAX, Mini, Square, Wide) stay in Latin to match Apple's
practice of leaving brand and technical identifiers untranslated.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Language", "t", "translatable_strings"]


class Language(StrEnum):
    """User-selectable LCD languages.

    Tags follow BCP 47 so they can be reused for any future Mac/web
    surfaces without renaming.
    """

    EN = "en"
    ZH_HANS = "zh-Hans"  # Chinese, Simplified


# ---------------------------------------------------------------------------
# Translations — English source on the left, target string on the right.
# Untranslated keys fall through to the English source.
# ---------------------------------------------------------------------------

_ZH_HANS: dict[str, str] = {
    # --- Top status-bar words ---------------------------------------------
    # Apple iOS uses 已连接 / 已断开连接 for Bluetooth devices; we keep the
    # short ``已断开`` form because the top bar is space-constrained.
    "Attention": "注意",
    "Complete": "已完成",
    "Connected": "已连接",
    "Disconnected": "已断开",
    "Done": "完成",
    "Error": "错误",
    "Finding": "查找中",
    "No film": "无相纸",
    "No printer": "无打印机",
    "Pair failed": "配对失败",
    "Pairing": "正在配对",
    "Preview": "预览",
    "Printer setup": "打印机设置",
    "Printing": "打印中",
    "Received": "已接收",
    "Searching": "搜索中",
    "Settings": "设置",
    "Starting": "正在启动",
    "Validating": "校验中",
    "Waiting": "等待中",
    # --- READY body title + info-row labels -------------------------------
    "1 photo": "1 张照片",
    "Battery": "电池",
    "Film": "相纸",
    "Printer": "打印机",
    "Queue": "队列",
    "Ready": "就绪",
    "SSID": "网络名",
    "Setup needed": "需要设置",
    "Type": "型号",
    "photos": "张照片",
    # --- Settings page titles + main-page rows ---------------------------
    # iOS calls Settings "设置"; we mirror About / Accessibility wording.
    "About": "关于本机",
    "Accessibility": "辅助功能",
    "Connect": "连接",
    "Network": "网络",
    "Print": "打印",
    "System": "系统",
    # --- Settings row labels ---------------------------------------------
    # Apple's iOS Reset/Forget vocabulary: 还原, 忘记此网络/设备.
    "Active Wi-Fi": "当前 Wi-Fi",
    "App version": "应用版本",
    "Appearance": "外观",
    "Auto print": "自动打印",
    "BlueZ": "BlueZ",
    "Bluetooth": "蓝牙",
    "Bridge FTP": "桥接 FTP",
    "Device ID": "设备 ID",
    "FTP PIN": "FTP 密码",
    "FTP host": "FTP 主机",
    "FTP user": "FTP 用户",
    "Find printer": "查找打印机",
    "Forget & re-pair": "忘记并重新配对",
    "Forget printer": "忘记打印机",
    "Idle": "空闲",
    "Idle poweroff": "空闲自动关机",
    "Image fit": "图像适配",
    "JPEG quality": "JPEG 质量",
    "Keepalive": "保持连接",
    "Language": "语言",
    "No-film test": "无相纸测试",
    "OS": "操作系统",
    "Power": "电源",
    "Printer type": "打印机型号",
    "Python": "Python",
    "Refresh status": "刷新状态",
    "Reset BLE link": "还原 BLE 连接",
    "Reset credentials": "还原凭据",
    "Same Wi-Fi adv": "同 Wi-Fi 通告",
    "Search rate": "搜索频率",
    "Serial": "序列号",
    "Text size": "文字大小",
    "USB IP": "USB IP",
    "Unknown": "未知",
    "Upload note": "上传说明",
    "Wi-Fi Mode": "Wi-Fi 模式",
    "Wi-Fi PIN": "Wi-Fi 密码",
    # --- Hint-bar labels --------------------------------------------------
    "4-way Pan": "四向平移",
    "Hold K3": "长按 K3",
    "K1 OK": "K1 确认",
    "K1 Print": "K1 打印",
    "K1 Retry": "K1 重试",
    "K1 Select": "K1 选择",
    "K1 Setting": "K1 设置",
    "K2 Back": "K2 返回",
    "K2 Cancel": "K2 取消",
    "K2 Refresh": "K2 刷新",
    "K3 FTP": "K3 FTP",
    "K3 Help": "K3 帮助",
    "K3 Retry": "K3 重试",
    "Left Back": "左 返回",
    "Left/Right": "左/右",
    "Move": "移动",
    "Scanning": "扫描中",
    "Up/Dn": "上/下",
    "Up/Dn Edit": "上/下 编辑",
    # --- Body action / status copy ---------------------------------------
    "Blocked": "已阻止",
    "Bluetooth lookup failed": "蓝牙查询失败",
    "Bluetooth setup failed": "蓝牙设置失败",
    "Checking": "检查中",
    "Checking printer": "正在检查打印机",
    "Close phone app if it fails": "失败时请关闭手机 App",
    "Close phone app or phone BT": "请关闭手机 App 或手机蓝牙",
    "Connecting": "正在连接",
    "Do not power off": "请勿断电",
    "FTP and printer ready": "FTP 与打印机已就绪",
    "Failed": "失败",
    "Film should feed now": "相纸即将送出",
    "Hold K3 to re-pair": "长按 K3 重新配对",
    "If stuck, close phone app": "若卡住请关闭手机 App",
    "Keep it awake near bridge": "请保持打印机唤醒并靠近桥接",
    "Keep printer awake": "请保持打印机唤醒",
    "Looking for printer": "正在查找打印机",
    "Looking": "正在查找",
    "No INSTAX printer found": "未找到 INSTAX 打印机",
    "No printer found": "未找到打印机",
    "No printer signal": "无打印机信号",
    "No settings available": "暂无可用设置",
    "Next action": "下一步操作",
    "Opening Bluetooth session": "正在打开蓝牙会话",
    "Phone Bluetooth may grab it": "手机蓝牙可能占用",
    "Power-cycle printer, then retry": "请重启打印机后重试",
    "Preparing preview": "正在准备预览",
    "Press K1": "请按 K1",
    "Press K2 to cancel": "按 K2 取消",
    "Print in {n}s": "{n} 秒后打印",  # informational; not actually queried
    "Printer not found nearby": "附近未发现打印机",
    "Printer off": "打印机已关闭",
    "Printer offline": "打印机离线",
    "Printer searching": "正在搜索打印机",
    "Printer seen; connecting": "已发现打印机，正在连接",
    "Printer timed out": "打印机连接超时",
    "Printing soon": "即将打印",
    "Received over FTP": "已通过 FTP 接收",
    "Re-pair printer": "重新配对打印机",
    "Restart printer": "请重启打印机",
    "Retrying": "正在重试",
    "Retrying printer": "正在重试打印机",
    "Saw other Instax": "发现其他 Instax",
    "Saw {n} Instax": "发现 {n} 台 Instax",  # informational
    "Scanning for INSTAX-*": "正在扫描 INSTAX-*",
    "Scanning for printer": "正在扫描打印机",
    "Scanning: 0 printers": "扫描中：未发现打印机",
    "Searching for printer": "正在搜索打印机",
    "Select printer again": "请重新选择打印机",
    "Selected printer not visible": "未发现所选打印机",
    "Sending to printer": "正在发送至打印机",
    "Sent": "已发送",
    "Starting print": "正在开始打印",
    "Starting services": "正在启动服务",
    "Then press K1": "然后请按 K1",
    "Turn on printer first": "请先打开打印机",
    "Turn printer on": "请打开打印机",
    "Turn printer on and keep awake": "请打开打印机并保持唤醒",
    "Turn printer on first": "请先打开打印机",
    "Turn selected printer on": "请打开所选打印机",
    "Try again": "重试",
    "Updating preview": "正在更新预览",
    "Wait for printer": "请等待打印机",
    "Waiting for upload": "等待上传",
    "Working": "处理中",
    "Wrong one": "型号不符",
    # --- Confirm / toast messages ----------------------------------------
    # iOS confirms reuse 取消/确认; we keep K1/K2 labels in Latin since the
    # bridge surfaces those physical key names verbatim on the LCD.
    "Already selected": "已选择",
    "BLE link reset": "BLE 连接已还原",
    "Bridge battery critical": "桥接电量过低",
    "Cancel": "取消",
    "Choose option": "请选择选项",
    "Config not writable": "配置不可写",
    "Credential write failed": "凭据写入失败",
    "Credentials regenerated": "凭据已重新生成",
    "Enter these on sender": "请在发送端输入这些信息",
    "Forget failed": "忘记失败",
    "Idle shutdown": "空闲关机",
    "KEY1 opens category": "KEY1 打开分类",
    "Not implemented": "尚未支持",
    "No choices": "无可选项",
    "No printer saved": "未保存打印机",
    "Pairing cancelled": "配对已取消",
    "Please wait": "请稍候",
    "Preview failed": "预览失败",
    "Press K1 again to FORGET and re-pair": "再按 K1 忘记并重新配对",
    "Press K1 again to FORGET printer": "再按 K1 忘记打印机",
    "Press K1 again to RESET BLE link": "再按 K1 还原 BLE 连接",
    "Printer forgotten": "已忘记打印机",
    "Refresh failed": "刷新失败",
    "Refreshing status": "正在刷新状态",
    "Reset Wi-Fi/FTP creds? K1 confirm K2 cancel": "还原 Wi-Fi/FTP 凭据？K1 确认 K2 取消",
    "Resetting BLE link": "正在还原 BLE 连接",
    "Save failed": "存储失败",
    "Saved": "已存储",
    "Status refreshed": "状态已刷新",
    "Wi-Fi + FTP credentials": "Wi-Fi 与 FTP 凭据",
    # --- Picker / option labels ------------------------------------------
    # Apple's appearance picker on iOS: 浅色 / 深色 / 跟随系统.
    "Advanced": "高级",
    "Auto": "自动",
    "Client": "客户端",
    "Contain": "适应",
    "Crop": "裁剪",
    "Dark": "深色",
    "English": "英文",
    "Hotspot": "热点",
    "Large": "大",
    "Light": "浅色",
    "Medium": "中",
    "Off": "关",
    "On": "开",
    "Small": "小",
    "Stretch": "拉伸",
    "中文": "中文",
    # ``System`` is reused as both the Settings sub-page header and the
    # appearance picker option (Light/Dark/System). Apple's iOS uses
    # ``跟随系统`` for the appearance picker but ``系统`` for navigation
    # surfaces. We keep the navigation form here because it is the more
    # frequent use; refining the picker requires a code-side source change.
    # --- FTP / network status text ---------------------------------------
    # Mirrors Apple's iOS Wi-Fi panel where "Wi-Fi" itself is left in Latin
    # and only descriptors (已连接/已开启/已关闭) get localised.
    "Battery case": "电池仓",
    "Battery unknown": "电量未知",
    "Bridge Wi-Fi": "桥接 Wi-Fi",
    "Bridge Wi-Fi failed": "桥接 Wi-Fi 失败",
    "Bridge Wi-Fi is primary": "桥接 Wi-Fi 为主要连接",
    "Bridge Wi-Fi FTP": "桥接 Wi-Fi FTP",
    "Bridge Wi-Fi name": "桥接 Wi-Fi 名称",
    "Bridge Wi-Fi PIN": "桥接 Wi-Fi 密码",
    "Bridge Wi-Fi off": "桥接 Wi-Fi 已关闭",
    "Bridge Wi-Fi ready": "桥接 Wi-Fi 已就绪",
    "Bridge Wi-Fi selected": "已选择桥接 Wi-Fi",
    "Bridge Wi-Fi starting": "桥接 Wi-Fi 启动中",
    "Bridge battery telemetry": "桥接电量信息",
    "Bridge off": "桥接已关闭",
    "Bridge power hardware": "桥接电源硬件",
    "Bridge ready": "桥接已就绪",
    "Choose Bridge or Same-Wi-Fi FTP": "请选择桥接或同 Wi-Fi FTP",
    "Connection failed": "连接失败",
    "FTP active client": "FTP 活动客户端",
    "FTP password": "FTP 密码",
    "FTP username": "FTP 用户名",
    "Idle dim and poweroff": "空闲变暗与关机",
    "Joining saved Wi-Fi": "正在加入已存储的 Wi-Fi",
    "LED only": "仅 LED",
    "No FTP Wi-Fi": "无 FTP Wi-Fi",
    "No battery": "无电池",
    "No telemetry": "无遥测",
    "Power monitor": "电源监测",
    "PiSugar": "PiSugar",
    "Printer Bluetooth": "打印机蓝牙",
    "Same Wi-Fi adv off": "同 Wi-Fi 通告已关闭",
    "Same Wi-Fi adv ready": "同 Wi-Fi 通告已就绪",
    "Same Wi-Fi adv selected": "已选择同 Wi-Fi 通告",
    "Same-Wi-Fi subnet conflict": "同 Wi-Fi 子网冲突",
    "Selecting Wi-Fi": "正在选择 Wi-Fi",
    "Sender joins Bridge Wi-Fi": "发送端加入桥接 Wi-Fi",
    "Sender uses saved Wi-Fi": "发送端使用已存储的 Wi-Fi",
    "Starting bridge Wi-Fi": "正在启动桥接 Wi-Fi",
    "USB IP off": "USB IP 已关闭",
    "USB IP only": "仅 USB IP",
    "USB IP connected": "USB IP 已连接",
    "USB IP for setup and updates": "USB IP 用于设置与更新",
    "USB IP missing": "USB IP 缺失",
    "USB IP selected": "已选择 USB IP",
    "USB IP unchanged": "USB IP 未变更",
    "USB is debug/update only": "USB 仅用于调试与更新",
    "Use these FTP settings": "请使用以下 FTP 设置",
    "Use a Wi-Fi FTP profile": "请使用 Wi-Fi FTP 配置",
    "Wi-Fi join failed": "Wi-Fi 加入失败",
    "Wi-Fi profile": "Wi-Fi 配置",
    "join bridge": "加入桥接",
    "no IP": "无 IP",
    "not selected": "未选择",
    "not set": "未设置",
    "offline": "离线",
    "off": "关",
    "saved": "已存储",
    "same Wi-Fi adv": "同 Wi-Fi 通告",
    "searching": "搜索中",
    "see Network": "见网络",
    # --- Misc short LCD copy ---------------------------------------------
    "Advanced Same-Wi-Fi status": "高级同 Wi-Fi 状态",
    "Allow 10 min idle shutdown": "允许 10 分钟空闲关机",
    "Any FTP client works (camera, app, scp)": "任何 FTP 客户端均可（相机、App、scp）",
    "Auto detects from printer": "从打印机自动检测",
    "Bluetooth stack version": "蓝牙协议栈版本",
    "Bridge battery/UPS hardware": "桥接电池/UPS 硬件",
    "Bridge battery/UPS hardware (legacy)": "桥接电池/UPS 硬件（旧版）",
    "Bridge health and updates": "桥接健康与更新",
    "Bridge software version": "桥接软件版本",
    "Bridge Wi-Fi name to join from camera": "供相机加入的桥接 Wi-Fi 名称",
    "Bridge Wi-Fi password (8 digits)": "桥接 Wi-Fi 密码（8 位数字）",
    "Camera connects here for upload": "相机由此连接以上传",
    "Dim and screen-off timing": "变暗与息屏时长",
    "Editable preview, then prints": "可编辑预览后打印",
    "Enter as FTP password in camera": "请在相机中输入为 FTP 密码",
    "Enter as FTP server in camera": "请在相机中输入为 FTP 服务器",
    "Enter as FTP user in camera": "请在相机中输入为 FTP 用户",
    "BLE link to Instax printer": "至 Instax 打印机的 BLE 连接",
    "How camera reaches bridge": "相机连接桥接的方式",
    "How to fit photo to film aspect": "照片如何适配相纸比例",
    "Hotspot: bridge AP. Client: join existing.": "热点：桥接为 AP；客户端：加入已有网络",
    "Info only": "仅供信息",
    "LCD text size": "LCD 文字大小",
    "LCD language (中文 / English)": "LCD 语言（中文 / English）",
    "Light / Dark / System theme": "浅色 / 深色 / 跟随系统",
    "Linux distribution version": "Linux 发行版版本",
    "Linux/Debian release on the Pi": "Pi 上的 Linux/Debian 版本",
    "Path the camera actually used": "相机实际使用的路径",
    "Pairing and photo/print options": "配对与照片/打印选项",
    "PIN: enter as FTP password in camera": "PIN：请在相机中输入为 FTP 密码",
    "Polls printer while idle": "空闲时轮询打印机",
    "Python runtime version": "Python 运行时版本",
    "Re-check printer and FTP now": "立即重新检查打印机与 FTP",
    "Reconnect to the saved printer": "重新连接已存储的打印机",
    "Remove the saved printer": "移除已存储的打印机",
    "Remove the saved printer (no re-pair)": "移除已存储的打印机（不重新配对）",
    "Right/KEY1 choose": "右 / KEY1 选择",
    "Right/KEY1 info": "右 / KEY1 信息",
    "Right/KEY1 open": "右 / KEY1 打开",
    "Right/KEY1 run": "右 / KEY1 执行",
    "Scans when printer offline": "打印机离线时扫描",
    "Scan and remember one Instax printer": "扫描并记住一台 Instax 打印机",
    "Serial of the saved Instax printer": "已存储 Instax 打印机的序列号",
    "Shuts down after 10 min idle": "空闲 10 分钟后关机",
    "Stays on indefinitely": "持续开机",
    "Test mode: skip 0/10 film check": "测试模式：跳过 0/10 相纸检查",
    "Text size, language, and appearance": "文字大小、语言与外观",
    "Trade-off: higher = bigger, sharper": "权衡：越高越大越清晰",
    "Unique ID; used by the Mac app": "唯一 ID；供 Mac App 使用",
    "Versions and device identity": "版本与设备标识",
    "Wi-Fi, FTP credentials, Bluetooth, USB": "Wi-Fi、FTP 凭据、蓝牙、USB",
    "Wipe pairing, then start a fresh scan": "清除配对并重新扫描",
    "USB network to Mac (setup, updates)": "至 Mac 的 USB 网络（设置、更新）",
    "Advanced: bridge on existing Wi-Fi": "高级：桥接接入已有 Wi-Fi",
    "Battery charge if telemetry available": "电池电量（如有遥测）",
    "BlueZ: Linux Bluetooth stack for pairing": "BlueZ：用于配对的 Linux 蓝牙协议栈",
    "Debian/Linux release on the Pi": "Pi 上的 Debian/Linux 版本",
    "Generate new Wi-Fi & FTP credentials": "生成新的 Wi-Fi 与 FTP 凭据",
    "Python: language running bridge code": "Python：运行桥接代码的语言",
    # --- Preview tool hints ----------------------------------------------
    # Mirrors Apple's "Zoom" / "Crop" / "Rotate" verbs in the Photos app.
    "Crop: joystick  K3 tool": "裁剪：摇杆  K3 工具",
    "Rotate: Left/Right  K3 tool": "旋转：左/右  K3 工具",
    "Zoom: Up/Down  K3 tool": "缩放：上/下  K3 工具",
    # --- Status indicator / signal words ---------------------------------
    "Bridge": "桥接",
    "BT": "蓝牙",
    "USB": "USB",
    "Wi-Fi": "Wi-Fi",
}

_TRANSLATIONS: dict[Language, dict[str, str]] = {
    Language.EN: {},  # English keys are identity; never accessed.
    Language.ZH_HANS: _ZH_HANS,
}


def t(text: str, language: Language | str = Language.EN) -> str:
    """Return ``text`` translated to ``language``.

    Unknown translations fall through to the English source so a missing
    string degrades gracefully (text stays readable, never blank).
    """

    if isinstance(language, str):
        try:
            language = Language(language)
        except ValueError:
            return text
    if language is Language.EN:
        return text
    return _TRANSLATIONS.get(language, {}).get(text, text)


def translatable_strings(language: Language) -> dict[str, str]:
    """Return the registered (source → target) map for ``language``.

    Used by tests + tooling to enumerate coverage; not part of the
    runtime translation path.
    """

    return dict(_TRANSLATIONS.get(language, {}))
