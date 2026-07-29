import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from captain.artifacts import OutputArchArtifact, OutputArchArtifactType
from captain.flavor import BaseFlavor
from captain.flavors.common_armbian import ArmbianCommonFlavor
from captain.tools import _download_binary
from captain.util import ensure_dir, symlink_relative

log: logging.Logger = logging.getLogger(__name__)


def create_flavor() -> BaseFlavor:
    return TrixieArmbianRPiFlavor()


@dataclass
class TrixieArmbianRPiFlavor(ArmbianCommonFlavor):
    id = "trixie-armbian-rpi"
    name = "Trixie for Raspberry Pi - Armbian bcm2711-current Kernel"
    description = "Debian Trixie based on Armbian's rockchip64-edge kernel"
    supported_architectures = frozenset(["arm64"])  # does NOT support amd64
    rpi_fw_tag = "1.20260408"

    def flavor_packages(self) -> set[str]:
        return {"linux-image-current-bcm2711"}.union(super().flavor_packages())

    def list_arch_artifacts(self, output_arch: str) -> list[OutputArchArtifact]:
        artifacts = super().list_arch_artifacts(output_arch)
        artifacts.append(
            OutputArchArtifact(
                type=OutputArchArtifactType.DIRECTORY, name=self.firmware_out_dirname()
            )
        )
        return artifacts

    def post_mkosi_stage(self):
        self.download_rpi_firmware()

    def post_artifact_collect(self):
        out: Path = ensure_dir(self.cfg.output_dir)
        log.debug("Copying firmware directory from %s to %s", self.firmware_output(), out)
        target_fw_dir = out / self.firmware_out_dirname()
        self.copy_firmware_dir(target_fw_dir)
        log.info("Copied firmware directory: %s", target_fw_dir)

        ## Symlink all dtbs directly in the fw dir
        out_dtbs = out / self.dtb_artifact_dirname(self.cfg.arch_info.output_arch)
        broadcom_dtbs = out_dtbs / "broadcom"
        if not broadcom_dtbs.is_dir():
            log.error(
                "Expected dtb directory %s does not exist, skipping dtb symlinks", broadcom_dtbs
            )
            raise ValueError(f"Expected dtb directory {broadcom_dtbs} does not exist")
        for dtb in broadcom_dtbs.glob("*.dtb"):
            target_dtb = target_fw_dir / dtb.name
            log.debug("Symlinking dtb: %s to %s", dtb, target_dtb)
            symlink_relative(target_dtb, dtb)

        # Symlink all overlays (which are in the same level as "broadcom") into an "overlays" subdir
        broadcom_overlays = out_dtbs / "overlays"
        if not broadcom_overlays.is_dir():
            log.error("Expected dtb overlays directory %s does not exist", broadcom_overlays)
            raise ValueError(f"Expected dtb overlays directory {broadcom_overlays} does not exist")
        self.link_overlays(target_fw_dir, broadcom_overlays)

        ## Symlink the kernel and initramfs
        # kernel as kernel8.img
        # see https://www.raspberrypi.com/documentation/computers/config_txt.html#kernel
        kernel_src = out / self.kernel_artifact_name(self.cfg.arch_info.output_arch)
        if not kernel_src.is_file():
            log.error(
                "Expected kernel image %s does not exist, cannot symlink to firmware dir",
                kernel_src,
            )
            raise ValueError(f"Expected kernel image {kernel_src} does not exist")
        kernel_dst = target_fw_dir / "kernel8.img"
        log.debug("Symlinking kernel from %s to %s", kernel_src, kernel_dst)
        symlink_relative(kernel_dst, kernel_src)

        # initramfs as initramfs8
        # see https://www.raspberrypi.com/documentation/computers/config_txt.html#initramfs
        initramfs_src = out / self.initramfs_artifact_name(self.cfg.arch_info.output_arch)
        if not initramfs_src.is_file():
            log.error(
                "Expected initramfs image %s does not exist, cannot symlink to firmware dir",
                initramfs_src,
            )
            raise ValueError(f"Expected initramfs image {initramfs_src} does not exist")
        initramfs_dst = target_fw_dir / "initramfs8"
        log.debug("Symlinking initramfs from %s to %s", initramfs_src, initramfs_dst)
        symlink_relative(initramfs_dst, initramfs_src)

    def firmware_out_dirname(self) -> str:
        # return f"firmware-{self.cfg.flavor_id}-{self.cfg.arch_info.output_arch}"
        return "firmware-rpi"

    def copy_firmware_dir(self, target_fw_dir: Path):
        """Place the downloaded firmware at *target_fw_dir*; overridable by consolidators."""
        if target_fw_dir.exists():
            shutil.rmtree(target_fw_dir)
        shutil.copytree(self.firmware_output(), target_fw_dir)

    def link_overlays(self, target_fw_dir: Path, overlays_src_dir: Path):
        """Symlink dtb overlays into the firmware dir; overridable by consolidating flavors."""
        target_overlays = ensure_dir(target_fw_dir / "overlays")
        for overlay in overlays_src_dir.glob("*.dtbo"):
            log.debug("Symlinking dtb overlay %s to firmware overlays directory", overlay)
            symlink_relative(target_overlays / overlay.name, overlay)

    def firmware_output(self) -> Path:
        return ensure_dir(self.cfg.initramfs_output / "firmware")

    def rpi_firmware_base_url(self) -> str:
        return (
            "https://raw.githubusercontent.com/raspberrypi/firmware/refs/tags/"
            + self.rpi_fw_tag
            + "/boot/"
        )

    def download_rpi_firmware(self):
        rpi_fw_files = [
            "bootcode.bin",
            "fixup4cd.dat",
            "fixup4.dat",
            "fixup4db.dat",
            "fixup4x.dat",
            "fixup_cd.dat",
            "fixup.dat",
            "fixup_db.dat",
            "fixup_x.dat",
            "LICENCE.broadcom",
            "start4cd.elf",
            "start4db.elf",
            "start4.elf",
            "start4x.elf",
            "start_cd.elf",
            "start_db.elf",
            "start.elf",
            "start_x.elf",
        ]
        for filename in rpi_fw_files:
            url = self.rpi_firmware_base_url() + filename
            output_path = self.firmware_output() / filename
            if output_path.is_file():
                log.debug(
                    "Raspberry Pi firmware file %s already exists at %s, skipping download",
                    url,
                    output_path,
                )
                continue
            log.info(
                "Raspberry Pi firmware file %s does not exist at %s, downloading...",
                url,
                output_path,
            )
            log.info("Downloading Raspberry Pi firmware file %s to %s", url, output_path)
            _download_binary(url, output_path)
