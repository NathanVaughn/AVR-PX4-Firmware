import argparse
import os
import shutil
import subprocess
import sys
from typing import List

THIS_DIR = os.path.abspath(os.path.dirname(__file__))
DIST_DIR = os.path.join(THIS_DIR, "dist")

# warning, v1.10.2 does not appear to build anymore
with open(os.path.join(THIS_DIR, ".px4-version"), "r") as fp:
    PX4_VERSION = fp.read().strip()

# use a specific directory for a version
BUILD_DIR = os.path.join(THIS_DIR, "build", PX4_VERSION)
PX4_DIR = os.path.join(BUILD_DIR, "PX4-Autopilot")


if PX4_VERSION < "v1.13.0":
    PYMAVLINK_DIR = os.path.join(BUILD_DIR, "pymavlink")
else:
    PYMAVLINK_DIR = os.path.join(
        PX4_DIR,
        "src",
        "modules",
        "mavlink",
        "mavlink",
        "pymavlink",
    )


def print2(msg: str) -> None:
    """
    Print statement with a prefix.
    """
    print(f"--- {msg}", flush=True)


def touch_file(filename: str) -> None:
    """
    Creates the given file with no contet
    """
    with open(filename, "w"):
        pass


def clean_directory(directory: str, file_endings: List[str]) -> None:
    """
    Clean a directory of files with specific ends of the filenames
    """

    # cancel if the directory is not already there
    if not os.path.isdir(directory):
        return

    for filename in os.listdir(directory):
        if any(filename.endswith(e) for e in file_endings):
            os.remove(os.path.join(directory, filename))


def clone_pymavlink() -> None:
    """
    Clone pymavlink.
    """
    if PX4_VERSION >= "v1.13.0":
        return

    if os.path.isdir(PYMAVLINK_DIR):
        # update the checkout if we already have it
        print2("Updating pymavlink")
        subprocess.check_call(["git", "pull"], cwd=PYMAVLINK_DIR)

    else:
        # clone fresh
        print2("Cloning pymavlink")
        subprocess.check_call(
            ["git", "clone", "https://github.com/ardupilot/pymavlink", PYMAVLINK_DIR]
        )


def clone_px4() -> None:
    """
    Clone and patch PX4.
    """

    # file to record if PX4 has been patched
    check_patch_file = os.path.join(BUILD_DIR, ".px4-patched")

    if os.path.isdir(PX4_DIR):
        # first, figure out what version we have locally
        local_version = next(
            l.split("/")[-1]
            for l in subprocess.check_output(
                ["git", "remote", "show", "origin", "-n"], cwd=PX4_DIR
            )
            .decode()
            .splitlines()
            if l.strip().startswith("refs")
        )
        # if version does not match, nuke it
        if local_version != PX4_VERSION:
            print(f"Existing PX4 checkout is {local_version}, re-cloning")
            shutil.rmtree(BUILD_DIR)
            clone_px4()

    else:
        # clone fresh
        print2("Cloning PX4")
        subprocess.check_call(
            [
                "git",
                "clone",
                "https://github.com/PX4/PX4-Autopilot",
                PX4_DIR,
                "--depth",
                "1",
                "--branch",
                PX4_VERSION,
                "--recurse-submodules",
            ]
        )

    if not os.path.isfile(check_patch_file):
        print2("Applying PX4 patch")
        subprocess.check_call(
            [
                "git",
                "apply",
                "--ignore-space-change",
                "--ignore-whitespace",
                os.path.join(
                    THIS_DIR, "patches", f"hil_gps_heading_{PX4_VERSION}.patch"
                ),
            ],
            cwd=PX4_DIR,
        )

        # record that it has been patched
        touch_file(check_patch_file)


def install_dependencies() -> None:
    """
    Install any needed dependencies
    """
    print2("Installing Python dependencies")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "wheel"]
    )
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            os.path.join(PYMAVLINK_DIR, "requirements.txt"),
        ]
    )


def build_pymavlink(
    message_definitions_dir: str, bell_xml_def: str, should_build_wireshark: bool
) -> None:
    print2("Generating pymavlink package")

    # file to record if Pymavlink has been patched
    check_patch_file = os.path.join(BUILD_DIR, ".pymavlink-patched")

    if not os.path.isfile(check_patch_file):
        print2("Applying Pymavlink patch")
        subprocess.check_call(["git", "reset", "--hard"], cwd=PYMAVLINK_DIR)
        subprocess.check_call(
            [
                "git",
                "apply",
                "--ignore-space-change",
                "--ignore-whitespace",
                os.path.join(THIS_DIR, "patches", f"pymavlink_{PX4_VERSION}.patch"),
            ],
            cwd=PYMAVLINK_DIR,
        )
        # record that it has been patched
        touch_file(check_patch_file)

    # copy message definitions from px4 so we're using the exact same version
    print2("Copying message definitions")
    shutil.rmtree(
        os.path.join(PYMAVLINK_DIR, "message_definitions", "v1.0"),
        ignore_errors=True,
    )
    shutil.copytree(
        message_definitions_dir,
        os.path.join(PYMAVLINK_DIR, "message_definitions", "v1.0"),
    )

    pymavlink_dist_dir = os.path.join(PYMAVLINK_DIR, "dist")

    # clean the pymavlink build and target dirs
    # print2("Cleaning output")
    # clean_directory(pymavlink_dist_dir, [".tar.gz", ".whl"])
    # clean_directory(DIST_DIR, [".tar.gz", ".whl"])

    # make a new environment with the mavlink dialect set
    new_env = os.environ.copy()
    new_env["MAVLINK_DIALECT"] = "bell"
    print2("Building package")
    subprocess.check_call(
        [
            sys.executable,
            "setup.py",
            "sdist",
            "bdist_wheel",
        ],
        cwd=PYMAVLINK_DIR,
        env=new_env,
    )

    # copy the outputs to the target directory
    for filename in os.listdir(pymavlink_dist_dir):
        shutil.copyfile(
            os.path.join(pymavlink_dist_dir, filename),
            os.path.join(DIST_DIR, filename),
        )

    # generate lua plugins for Wireshark
    # https://mavlink.io/en/guide/wireshark.html
    if should_build_wireshark:
        print2("Building wireshark plugin")
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pymavlink.tools.mavgen",
                "--lang=WLua",
                "--wire-protocol=2.0",
                f"--output={os.path.join(DIST_DIR, 'bell-avr.lua')}",
                bell_xml_def,
            ],
            cwd=os.path.join(PYMAVLINK_DIR, ".."),
        )


def build_px4(targets: List[str], version: str) -> None:
    print2("Building PX4 firmware")

    px4_build_dir = os.path.join(PX4_DIR, "build")

    # clean the PX4 build and target dir
    # clean_directory(px4_build_dir, [".px4"])
    # clean_directory(DIST_DIR, [".px4"])

    for target in targets:
        subprocess.check_call(["make", target, "-j"], cwd=PX4_DIR)
        shutil.copyfile(
            os.path.join(px4_build_dir, target, f"{target}.px4"),
            os.path.join(DIST_DIR, f"{target}.{PX4_VERSION}.{version}.px4"),
        )


def main(
    should_build_pymavlink: bool,
    should_build_px4: bool,
    should_build_wireshark: bool,
    version: str,
    targets: List[str],
) -> None:
    os.makedirs(DIST_DIR, exist_ok=True)

    # clone pymavlink if necessary
    clone_pymavlink()

    # get px4 cloned
    clone_px4()

    # install python dependencies for pymavlink
    install_dependencies()

    # build directory paths
    if PX4_VERSION < "v1.13.0":
        message_definitions_dir = os.path.join(
            PX4_DIR,
            "mavlink",
            "include",
            "mavlink",
            "v2.0",
            "message_definitions",
        )
        generated_message_dir = os.path.join(message_definitions_dir, "..")
    else:
        message_definitions_dir = os.path.join(
            PX4_DIR,
            "src",
            "modules",
            "mavlink",
            "mavlink",
            "message_definitions",
            "v1.0",
        )
        generated_message_dir = os.path.join(message_definitions_dir, "..", "..", "..")

    bell_xml_def = os.path.join(message_definitions_dir, "bell.xml")

    # file to record if mavlink has been committed
    check_patch_file = os.path.join(BUILD_DIR, ".mavlink-commited")

    if not os.path.isfile(check_patch_file):
        print2("Injecting Bell MAVLink message")
        shutil.copyfile(os.path.join(THIS_DIR, "bell.xml"), bell_xml_def)

        # generate the mavlink C code
        if PX4_VERSION < "v1.13.0":
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "pymavlink.tools.mavgen",
                    "--lang=C",
                    "--wire-protocol=2.0",
                    f"--output={generated_message_dir}",
                    bell_xml_def,
                ],
                cwd=os.path.join(PYMAVLINK_DIR, ".."),
            )

        # git config does not matter, just need *something* to commit,
        # they're not pushed anywhere
        subprocess.check_call(
            ["git", "config", "user.email", "github-bot@nvaughn.email"], cwd=PX4_DIR
        )
        subprocess.check_call(
            ["git", "config", "user.name", "Github Actions"], cwd=PX4_DIR
        )

        # changes need to be committed to build
        subprocess.check_call(["git", "add", "."], cwd=PX4_DIR)
        subprocess.check_call(
            [
                "git",
                "commit",
                "--no-gpg-sign",
                "-m",
                "Local commit to facilitate build",
            ],
            cwd=PX4_DIR,
        )

        # record that it has been committed
        touch_file(check_patch_file)

    if should_build_pymavlink:
        build_pymavlink(message_definitions_dir, bell_xml_def, should_build_wireshark)

    if should_build_px4:
        build_px4(targets, version)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a PX4/Pymavlink build")
    parser.add_argument(
        "--version",
        type=str,
        default=subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=THIS_DIR
        )
        .decode("utf-8")
        .strip(),
    )
    parser.add_argument(
        "--pymavlink", action="store_true", help="Build Pymavlink package"
    )
    parser.add_argument("--px4", action="store_true", help="Build PX4 firmware")
    parser.add_argument(
        "--wireshark", action="store_true", help="Build Wireshark Lua plugins"
    )
    # pixhawk v5X, v6x, v6c and NXP
    parser.add_argument(
        "--targets",
        nargs="+",
        default=[
            "px4_fmu-v5x_default",
            "px4_fmu-v6c_default",
            "px4_fmu-v6x_default",
            # "nxp_fmuk66-v3_default",
        ],
    )

    args = parser.parse_args()

    if args.wireshark and not args.pymavlink:
        parser.error("Cannot build Wireshark plugins without pymavlink")

    main(args.pymavlink, args.px4, args.wireshark, args.version, args.targets)
