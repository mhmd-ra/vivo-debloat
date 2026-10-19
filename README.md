# vivo-debloat

Debloat and de-Google **vivo / iQOO** phones over ADB. No root, no unlocked bootloader, no custom ROM.
Pick everything or only the categories / single packages you want, and undo it any time.

Tested on an iQOO Neo (V1824BA), Funtouch OS (Android 11, China ROM).
Other vivo/iQOO models should work, but package names differ between ROMs: use `--dry-run` first.

## How it works

For each selected package the tool runs `pm uninstall -k --user 0` (removes it for your user only, the
system image is untouched). vivo protects its preloads in `/data/vivo-apps`, where that call fails, so it
falls back to `pm disable-user --user 0`. A log of what was done is written to
`debloat-log-<serial>.json`, and `restore` brings everything back (`cmd package install-existing` /
`pm enable`). A factory reset also restores everything.

A **protected list** (SystemUI, Settings, telephony, lock screen, push service, WebView, always-on display
and so on) is never touched, even if you select it.

## Requirements

* Python 3.8+ (standard library only)
* Phone: Developer options > USB debugging on, USB cable, tap *Allow* on the prompt.
* `adb`: either already on your PATH, or run `python debloat.py get-adb` (downloads Google's official
  platform-tools into this folder).

## Usage

```bash
python debloat.py list                       # what from the catalog is on your phone
python debloat.py remove                     # interactive: choose categories or single packages
python debloat.py remove --all               # everything in the catalog
python debloat.py remove --category google,ai_voice,games_wallpapers
python debloat.py remove --package com.vivo.weather,com.vivo.space
python debloat.py remove --all --dry-run     # show what would happen
python debloat.py replacements               # install open-source replacements from F-Droid
python debloat.py restore                    # undo everything from the log
python debloat.py restore --package com.vivo.weather
```

In the interactive menu type numbers (`1,3-5`), `a` for all, `d 3` to choose individual packages of
category 3, `done` to continue.

vivo shows an **"Install" confirmation on the phone for every `adb install`**; keep the phone unlocked and tap it.

## Categories

| name | what |
|---|---|
| `preinstalled_apps` | shopping, video, news, travel and map apps |
| `chinese_social` | WeChat, Weibo, Xiaohongshu, Douyin |
| `games_wallpapers` | game center, live wallpapers, screen savers |
| `vivo_apps` | weather, notes, music, health, wallet, tips, calculator... |
| `widgets_search` | widgets, global search, smart scene, minus-one screen |
| `ai_voice` | Jovi, voice wake, AI/translate/smart-shot services |
| `car_iot` | car mode, roaming, misc connectivity extras |
| `telemetry_engineering` | logging, feedback, factory/engineering tools |
| `google` | Play Services, Play Store, GSF, sync adapters (keeps WebView) |
| `system_extras` | printing, backup, tags, misc AOSP/vivo extras |
| `browsers` | stock browsers, **install a replacement first** |
| `stock_basics` | stock SMS, dialer, files, gallery, keyboard, **install replacements first** |
| `face_unlock` | face unlock (fingerprint / PIN keep working) |

## Open-source replacements (`replacements`)

From f-droid.org: Fennec (browser), Fossify Messages / Phone / Gallery / File Manager,
Simple Keyboard. The tool installs them and assigns the default SMS, dialer
and browser roles.

## Warnings

* **Do not replace or disable the vivo launcher (`com.bbk.launcher2`).** The bottom swipe gestures (home, recents) are implemented inside it; with a third-party launcher only the back gesture keeps working.
* Removing Google Play Services breaks push notifications of Google-dependent apps (Instagram, WhatsApp, X, ...).
  Chinese apps use vivo's push service, which is on the protected list.
* Removing the stock keyboard/browser/SMS/dialer before installing replacements leaves you without them.
* Use at your own risk. Everything is reversible with `restore` or a factory reset, but start with `--dry-run`.
* Always-on display needs `com.vivo.nightpearl` and `com.vivo.doubletimezoneclock`; both are protected / not in the catalog.

## License

MIT

