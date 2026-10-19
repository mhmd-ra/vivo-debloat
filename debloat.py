#!/usr/bin/env python3
"""vivo-debloat: remove bloat and Google services from vivo / iQOO phones over ADB.

No root and no unlocked bootloader needed. Packages are removed only for user 0
(`pm uninstall -k --user 0`), falling back to `pm disable-user` for vivo's protected
preloads. Everything can be restored with `debloat.py restore`.

Python 3.8+, standard library only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "catalog" / "vivo.json"
PT_URL = "https://dl.google.com/android/repository/platform-tools-latest-{os}.zip"
FDROID_API = "https://f-droid.org/api/v1/packages/{pkg}"
FDROID_APK = "https://f-droid.org/repo/{pkg}_{code}.apk"

# Open-source replacements installed from F-Droid. role = Android role to assign.
REPLACEMENTS = {
    "browser":  ("org.mozilla.fennec_fdroid", "Fennec (Firefox without telemetry)", "android.app.role.BROWSER"),
    "sms":      ("org.fossify.messages", "Fossify Messages", "android.app.role.SMS"),
    "dialer":   ("org.fossify.phone", "Fossify Phone", "android.app.role.DIALER"),
    "gallery":  ("org.fossify.gallery", "Fossify Gallery", None),
    "files":    ("org.fossify.filemanager", "Fossify File Manager", None),
    "keyboard": ("rkr.simplekeyboard.inputmethod", "Simple Keyboard", None),
    "launcher": ("app.olauncher", "Olauncher (minimal text launcher)", "android.app.role.HOME"),
}
# Categories that remove a core function: warn unless a replacement is installed.
RISKY = {
    "browsers": "browser", "stock_basics": "sms", "google": None,
}


class Adb:
    def __init__(self, exe, serial=None):
        self.exe, self.serial = exe, serial

    def run(self, *args, check=True, timeout=300):
        cmd = [self.exe] + (["-s", self.serial] if self.serial else []) + list(args)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout).strip())
        return (p.stdout + p.stderr).strip()

    def shell(self, *args, **kw):
        return self.run("shell", *args, **kw)

    def packages(self, flag):
        out = self.shell("pm", "list", "packages", flag)
        return {l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("package:")}


def find_adb(explicit=None):
    cands = [explicit, shutil.which("adb"), str(HERE / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"))]
    for c in cands:
        if c and (Path(c).exists() or shutil.which(c)):
            return c
    return None


def get_adb():
    plat = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
    url = PT_URL.format(os=plat)
    print(f"Downloading Google platform-tools from {url}")
    zp = HERE / "platform-tools.zip"
    urllib.request.urlretrieve(url, zp)
    with zipfile.ZipFile(zp) as z:
        z.extractall(HERE)
    zp.unlink()
    exe = HERE / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
    if os.name != "nt":
        exe.chmod(0o755)
    return str(exe)


def connect(args):
    exe = find_adb(args.adb)
    if not exe:
        sys.exit("adb not found. Install Android platform-tools, pass --adb PATH, or run: debloat.py get-adb")
    adb = Adb(exe, args.serial)
    adb.run("start-server", check=False)
    out = adb.run("devices")
    devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("\tdevice") or l.split()[-1:] == ["device"]]
    unauth = [l.split()[0] for l in out.splitlines()[1:] if "unauthorized" in l]
    if not devs:
        if unauth:
            sys.exit("Phone is unauthorized: unlock it and tap 'Allow' on the USB debugging prompt (try: adb kill-server).")
        sys.exit("No device found. Enable Developer options > USB debugging and plug in the phone.")
    if len(devs) > 1 and not args.serial:
        sys.exit("Several devices connected, choose one with --serial: " + ", ".join(devs))
    adb.serial = args.serial or devs[0]
    model = adb.shell("getprop", "ro.product.model", check=False)
    brand = adb.shell("getprop", "ro.product.brand", check=False)
    release = adb.shell("getprop", "ro.build.version.release", check=False)
    print(f"Device: {brand} {model}  (Android {release}, serial {adb.serial})")
    if brand.lower() not in ("vivo", "iqoo"):
        print("WARNING: this catalog targets vivo/iQOO. Continue only if you know the package names apply.")
        if not args.yes and input("Continue? [y/N] ").lower() != "y":
            sys.exit(1)
    return adb


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)


def parse_selection(text, n):
    """'1,3-5' -> {1,3,4,5}; 'a' -> all."""
    text = text.strip().lower()
    if text in ("a", "all"):
        return set(range(1, n + 1))
    sel = set()
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(part)
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        sel.update(range(lo, hi + 1))
    if any(i < 1 or i > n for i in sel):
        raise ValueError("out of range")
    return sel


def choose_interactive(cats, installed):
    """Show categories, let the user pick whole categories or drill into packages."""
    names = [k for k in cats if any(p in installed for p in cats[k]["packages"])]
    if not names:
        print("Nothing from the catalog is installed on this phone.")
        return []
    chosen = {}
    while True:
        print("\nCategories (installed packages / total in category):")
        for i, k in enumerate(names, 1):
            pk = [p for p in cats[k]["packages"] if p in installed]
            mark = "*" if k in chosen else " "
            print(f" {mark}{i:2}. {cats[k]['title']}  [{len(pk)}]")
        print("\nType numbers (e.g. 1,3-5), 'a' = everything, 'd N' = pick single packages in category N,")
        print("'done' to continue, 'q' to quit.")
        s = input("> ").strip().lower()
        if s in ("q", "quit"):
            return []
        if s in ("done", ""):
            if chosen:
                break
            continue
        try:
            if s.startswith("d "):
                idx = int(s.split()[1])
                k = names[idx - 1]
                pk = [p for p in cats[k]["packages"] if p in installed]
                for j, p in enumerate(pk, 1):
                    print(f"  {j:3}. {p}")
                ans = input("  packages to remove (numbers, 'a' all, empty = none): ")
                sel = parse_selection(ans, len(pk)) if ans.strip() else set()
                chosen[k] = [pk[j - 1] for j in sorted(sel)]
                if not chosen[k]:
                    chosen.pop(k)
            else:
                for i in parse_selection(s, len(names)):
                    k = names[i - 1]
                    chosen[k] = [p for p in cats[k]["packages"] if p in installed]
        except (ValueError, IndexError):
            print("Could not understand that, try again.")
    out = []
    for pk in chosen.values():
        out += [p for p in pk if p not in out]
    return out


def remove_package(adb, pkg):
    adb.shell("am", "force-stop", pkg, check=False)
    r = adb.shell("pm", "uninstall", "-k", "--user", "0", pkg, check=False)
    if "Success" in r:
        return "removed"
    r2 = adb.shell("pm", "disable-user", "--user", "0", pkg, check=False)
    if "disabled" in r2:
        return "disabled"
    return "failed: " + (r2 or r)


def log_path(adb):
    return Path.cwd() / f"debloat-log-{adb.serial}.json"


def cmd_remove(args):
    adb = connect(args)
    cat = load_catalog()
    cats, protected = cat["categories"], set(cat["protected"])
    # only enabled packages are candidates; removed ones are not listed and disabled ones are already done
    installed = adb.packages("-e")
    if args.all:
        pkgs = [p for k in cats for p in cats[k]["packages"] if p in installed]
    elif args.category or args.package:
        pkgs = []
        for k in (args.category.split(",") if args.category else []):
            if k not in cats:
                sys.exit(f"Unknown category '{k}'. Known: {', '.join(cats)}")
            pkgs += [p for p in cats[k]["packages"] if p in installed]
        pkgs += [p for p in (args.package.split(",") if args.package else []) if p in installed]
    else:
        pkgs = choose_interactive(cats, installed)
    pkgs = list(dict.fromkeys(pkgs))
    blocked = [p for p in pkgs if p in protected]
    if blocked:
        print("Skipping protected packages (needed for boot, mobile data, lock screen or notifications):")
        print("  " + ", ".join(blocked))
        pkgs = [p for p in pkgs if p not in protected]
    if not pkgs:
        print("Nothing to do.")
        return
    selected_cats = set(args.category.split(",")) if args.category else (set(cats) if args.all else set())
    for k, role in RISKY.items():
        if k in selected_cats and k in ("browsers", "stock_basics"):
            print(f"NOTE: category '{k}' removes a core app. Make sure replacements are installed "
                  f"(`debloat.py replacements`) or you will have no browser/SMS/dialer/keyboard.")
    print(f"\n{len(pkgs)} packages selected" + (" (dry run)" if args.dry_run else "") + ":")
    print("  " + "\n  ".join(pkgs))
    if args.dry_run:
        return
    if not args.yes and input("\nProceed? Packages can be restored later. [y/N] ").lower() != "y":
        return
    done = {"device": adb.serial, "time": datetime.now().isoformat(timespec="seconds"), "packages": {}}
    lp = log_path(adb)
    if lp.exists():
        try:
            done["packages"] = json.loads(lp.read_text(encoding="utf-8")).get("packages", {})
        except ValueError:
            pass
    for p in pkgs:
        res = remove_package(adb, p)
        print(f"  {p}: {res}")
        if res in ("removed", "disabled"):
            done["packages"][p] = res
    lp.write_text(json.dumps(done, indent=2), encoding="utf-8")
    print(f"\nLog saved to {lp}  (use `debloat.py restore` to undo). Reboot the phone once.")


def cmd_restore(args):
    adb = connect(args)
    lp = log_path(adb)
    if args.package:
        pkgs = {p: "removed" for p in args.package.split(",")}
    elif lp.exists():
        pkgs = json.loads(lp.read_text(encoding="utf-8"))["packages"]
    else:
        sys.exit(f"No log at {lp}. Use --package a,b to restore specific packages.")
    for p, how in pkgs.items():
        r = adb.shell("cmd", "package", "install-existing", "--user", "0", p, check=False)
        adb.shell("pm", "enable", "--user", "0", p, check=False)
        print(f"  {p}: {r.splitlines()[-1] if r else 'ok'}")
    if not args.package:
        lp.rename(lp.with_suffix(".restored.json"))


def cmd_list(args):
    adb = connect(args)
    cat = load_catalog()
    installed = adb.packages("-e")
    disabled = adb.packages("-d")
    for k, c in cat["categories"].items():
        pk = [p for p in c["packages"] if p in installed]
        print(f"\n{k}: {c['title']}  ({len(pk)} installed)")
        if c.get("note"):
            print(f"  note: {c['note']}")
        for p in c["packages"]:
            state = "disabled" if p in disabled else ("installed" if p in installed else "-")
            if state != "-":
                print(f"   {state:9} {p}")


def fdroid_download(pkg, dest):
    with urllib.request.urlopen(FDROID_API.format(pkg=pkg), timeout=60) as r:
        code = json.load(r)["suggestedVersionCode"]
    apk = dest / f"{pkg}_{code}.apk"
    if not apk.exists():
        urllib.request.urlretrieve(FDROID_APK.format(pkg=pkg, code=code), apk)
    return apk


def cmd_replacements(args):
    adb = connect(args)
    keys = list(REPLACEMENTS)
    if args.only:
        keys = [k for k in args.only.split(",") if k in REPLACEMENTS]
    elif not args.yes:
        print("\nOpen-source replacements (from f-droid.org):")
        for i, k in enumerate(keys, 1):
            print(f"  {i}. {k:9} {REPLACEMENTS[k][1]}")
        try:
            keys = [keys[i - 1] for i in sorted(parse_selection(input("Install which? (numbers, 'a' all) > "), len(keys)))]
        except ValueError:
            sys.exit("Invalid selection.")
    dest = Path.cwd() / "downloads"
    dest.mkdir(exist_ok=True)
    print("Watch the phone: vivo asks you to tap 'Install' for every adb install.")
    for k in keys:
        pkg, name, role = REPLACEMENTS[k]
        print(f"\n{name} ({pkg})")
        apk = fdroid_download(pkg, dest)
        for attempt in range(3):
            out = adb.run("install", "-r", str(apk), check=False, timeout=600)
            if "Success" in out:
                break
            print("  install failed:", out.splitlines()[-1], "- tap Install on the phone, retrying...")
        else:
            continue
        if k == "keyboard":
            ime = next((l for l in adb.shell("ime", "list", "-a", "-s").splitlines() if pkg in l), None)
            if ime:
                adb.shell("ime", "enable", ime, check=False)
                adb.shell("ime", "set", ime, check=False)
        if role:
            if role.endswith("HOME"):
                print("  Note: vivo's stock launcher keeps priority; disable it with:"
                      "  adb shell pm disable-user --user 0 com.bbk.launcher2  (after Olauncher is set)")
            print("  set default:", adb.shell("cmd", "role", "add-role-holder", "--user", "0", role, pkg, check=False) or "ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adb", help="path to adb executable")
    ap.add_argument("--serial", help="device serial if several are attached")
    ap.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="show catalog packages present on the phone")
    r = sub.add_parser("remove", help="remove/disable packages (interactive unless a selector is given)")
    r.add_argument("--all", action="store_true", help="select every catalog category")
    r.add_argument("--category", help="comma-separated category names")
    r.add_argument("--package", help="comma-separated package names")
    r.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("restore", help="undo removals from the log (or --package a,b)")
    s.add_argument("--package")
    p = sub.add_parser("replacements", help="install open-source replacement apps from F-Droid")
    p.add_argument("--only", help="comma-separated: " + ",".join(REPLACEMENTS))
    sub.add_parser("get-adb", help="download Google platform-tools next to this script")
    args = ap.parse_args()
    if args.cmd == "get-adb":
        print("adb at", get_adb())
        return
    {"list": cmd_list, "remove": cmd_remove, "restore": cmd_restore,
     "replacements": cmd_replacements}.get(args.cmd, lambda a: ap.print_help())(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
