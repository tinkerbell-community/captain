"""QEMU boot testing."""

from __future__ import annotations

import atexit
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from captain.config import Config
from captain.util import detect_current_machine_arch, run

log = logging.getLogger(__name__)

# @TODO: not used, qemu cmd passes everything after -- as raw kernel cmdline.
# @TODO: maybe move those to Click params in _qemu.py and remove this list?
_TINK_PARAMS: list[tuple[str, str]] = [
    # (namespace_attr,              cmdline_key)
    ("tink_worker_image", "tink_worker_image"),
    ("tink_docker_registry", "docker_registry"),
    ("tink_grpc_authority", "grpc_authority"),
    ("tink_worker_id", "worker_id"),
    ("tink_tls", "tinkerbell_tls"),
    ("tink_insecure_tls", "tinkerbell_insecure_tls"),
    ("tink_insecure_registries", "insecure_registries"),
    ("tink_registry_username", "registry_username"),
    ("tink_registry_password", "registry_password"),
    ("tink_syslog_host", "syslog_host"),
    ("tink_facility", "facility"),
]


def run_qemu(cfg: Config, args: list[str] | None = None) -> None:
    """Boot the built image in QEMU for quick testing."""
    import captain.flavor

    flavor = captain.flavor.create_and_setup_flavor_for_id(cfg.flavor_id, cfg)
    kernel = cfg.output_dir / flavor.kernel_artifact_name(cfg.arch_info.output_arch)
    initrd = cfg.output_dir / flavor.initramfs_artifact_name(cfg.arch_info.output_arch)

    log.debug("Looking for kernel at %s", kernel)
    log.debug("Looking for initramfs at %s", initrd)

    log.debug("Current machine arch: %s", detect_current_machine_arch())
    log.debug("Current machine platform: %s", sys.platform)

    missing: list[str] = []
    if not kernel.is_file():
        missing.append(str(kernel))
    if not initrd.is_file():
        missing.append(str(initrd))
    if missing:
        log.error("Build artifacts not found:")
        for m in missing:
            log.error("  %s", m)
        sys.exit(1)

    tink = " ".join(args) if args is not None else ""
    log.info("Booting CaptainOS in QEMU ('Ctrl-A x' to exit)...")

    cmdline_compos: list[str] = [
        "systemd.journald.forward_to_console=1",
        # "systemd.log_level=debug",
        # "systemd.log_target=console",
        f"{tink}",
    ]

    per_arch_extra_cmds: list[str] = []
    match cfg.arch:
        # direct kernel boot for x86
        case "amd64":
            qemu_amd64_params(cfg, cmdline_compos, per_arch_extra_cmds)

        # indirect boot via OVMF for arm64
        case "arm64":
            qemu_arm64_params(cfg, cmdline_compos, per_arch_extra_cmds)

    # If matching the host architecture, enable hardware acceleration via KVM or HVF
    if cfg.arch == detect_current_machine_arch():
        # if on Darwin and arm64 (Apple Silicon), enable hvf accel.
        if sys.platform == "darwin":
            log.info("Native run on Apple, enabling HVF acceleration")
            per_arch_extra_cmds += ["-accel", "hvf"]

        if sys.platform == "linux" and Path("/dev/kvm").is_char_device():
            log.info("Native run on Linux with KVM available, enabling KVM acceleration")
            per_arch_extra_cmds += ["-accel", "kvm"]

    append = " ".join(cmdline_compos).strip()
    log.info("Kernel cmdline: '%s'", append)
    run(
        [
            cfg.arch_info.qemu_binary,
            *per_arch_extra_cmds,
            "-kernel",
            str(kernel),
            "-initrd",
            str(initrd),
            "-append",
            append,
            "-nographic",
            "-m",
            cfg.qemu_mem,
            "-smp",
            cfg.qemu_smp,
            "-nic",
            "user,model=virtio-net-pci",
            "-no-reboot",
        ],
    )


def qemu_amd64_params(cfg: Config, cmdline_compos: list[str], per_arch_extra_cmds: list[str]):
    cmdline_compos.insert(0, "console=ttyS0")
    per_arch_extra_cmds += ["-machine", "q35"]
    per_arch_extra_cmds += ["-cpu", "max"]
    find_ovmf_firmware(
        cfg,
        per_arch_extra_cmds,
        [
            "edk2-x86_64-code.fd",  # Darwin/homebrew
            "OVMF_CODE_4M.fd",  # Debian's 'ovmf' package - recent
            "OVMF_CODE.fd",  # Debian's 'ovmf' package - older
        ],
        [
            "edk2-i386-vars.fd",  # Darwin/homebrew
            "OVMF_VARS_4M.fd",  # Debian's 'ovmf' package - recent
            "OVMF_VARS.fd",  # Debian's 'ovmf' package - older
        ],
    )


def qemu_arm64_params(cfg: Config, cmdline_compos: list[str], per_arch_extra_cmds: list[str]):
    cmdline_compos.insert(0, "console=ttyAMA0")
    per_arch_extra_cmds += ["-machine", "virt"]
    per_arch_extra_cmds += ["-cpu", "host"]
    find_ovmf_firmware(
        cfg,
        per_arch_extra_cmds,
        [
            "edk2-aarch64-code.fd",  # Darwin/homebrew
            "AAVMF_CODE.no-secboot.fd",  # Debian's qemu-efi-aarch64 - recent
            "AAVMF_CODE.fd",  # Debian's qemu-efi-aarch64 - older
        ],
        [
            "edk2-arm-vars.fd",  # Darwin/homebrew
            "AAVMF_VARS.fd",  # Debian's qemu-efi-aarch64
        ],
    )


def find_ovmf_firmware(
    cfg: Config, per_arch_extra_cmds: list[str], fw_paths: list[str], vars_paths: list[str]
):
    base_dirs: list[Path] = []
    match sys.platform:
        case "darwin":
            qemu_brew_prefix = run(["brew", "--prefix", "qemu"], capture=True).stdout.strip()
            base_dirs.append(Path(qemu_brew_prefix) / "share" / "qemu")
        case "linux":
            base_dirs += [Path("/usr/share/OVMF"), Path("/usr/share/AAVMF")]

    # keep only the base_dirs that actually exist and are directories
    valid_base_dirs = [d for d in base_dirs if d.is_dir()]
    if not valid_base_dirs:
        log.error(
            "No valid base directories found for OVMF firmware on platform %s: %s",
            sys.platform,
            base_dirs,
        )
        log.error(
            "Install the appropriate OVMF package for arch %s your distribution and try again.",
            cfg.arch,
        )
        sys.exit(1)

    # Now just combinatorics: for each base dir, check if any of the expected firmware paths exist.
    combined_full_fw_paths = [base / f for base in valid_base_dirs for f in fw_paths]
    # find the first that exists and is a file; if none, edk2_path is None
    edk2_path = next((p for p in combined_full_fw_paths if p.is_file()), None)

    if edk2_path is None:
        log.error(
            "OVMF code binary for arch %s not found in any of the expected paths: %s",
            cfg.arch,
            combined_full_fw_paths,
        )
        sys.exit(1)

    # Again, but for the vars binary, which is also required.
    combined_full_vars_paths = [base / f for base in valid_base_dirs for f in vars_paths]
    vars_path_ro = next((p for p in combined_full_vars_paths if p.is_file()), None)
    if vars_path_ro is None:
        log.error(
            "OVMF vars binary for arch %s not found in any of the expected paths: %s",
            cfg.arch,
            combined_full_vars_paths,
        )
        sys.exit(1)

    # create a temporary file and copy the vars_path_ro there; clean it up on exit
    vars_path = Path(tempfile.mkstemp(suffix=f"captain.qemu.efivars.{vars_path_ro.name}.fd")[1])
    log.info("Copying OVMF vars binary from %s to temporary file %s", vars_path_ro, vars_path)
    shutil.copy2(vars_path_ro, vars_path)
    atexit.register(
        lambda: (
            log.info("Cleaning up temporary OVMF vars file at %s", vars_path),
            vars_path.unlink(missing_ok=True),
        )
    )

    log.info("Using OVMF firmware code binary at %s", edk2_path)
    per_arch_extra_cmds += ["-drive", f"if=pflash,format=raw,readonly=on,file={edk2_path!s}"]

    log.info("Using OVMF firmware vars binary at %s from %s", vars_path, vars_path_ro)
    per_arch_extra_cmds += ["-drive", f"if=pflash,format=raw,file={vars_path!s}"]
