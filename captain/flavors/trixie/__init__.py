"""Consolidated Trixie flavor.

Builds the slim (captainos kernel) image on amd64 and the Armbian bcm2711
(Raspberry Pi) image on arm64, publishing everything under standardized,
flavor-less artifact paths:

    vmlinuz-{x86_64,aarch64}
    initramfs-{x86_64,aarch64}
    dtb/                # single boot dir: RPi firmware + kernel8.img/initramfs8
                        # symlinks at its root, dtbs in broadcom/, overlays in
                        # overlays/ — servable directly as an RPi TFTP root
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from captain.artifacts import OutputArchArtifact, OutputArchArtifactType
from captain.config import Config
from captain.flavor import BaseFlavor
from captain.flavors.trixie_armbian_rpi import TrixieArmbianRPiFlavor
from captain.flavors.trixie_slim import TrixieSlimFlavor

log: logging.Logger = logging.getLogger(__name__)


def create_flavor() -> BaseFlavor:
    return TrixieFlavor()


class TrixieConsolidatedMixin(BaseFlavor):
    """Standardized (flavor-less) artifact naming shared by both per-arch variants."""

    id = "trixie"
    name = "Trixie"
    description = (
        "Consolidated Debian Trixie: captainos slim kernel on amd64 and "
        "Armbian bcm2711 (Raspberry Pi) kernel on arm64 with standardized artifact paths"
    )
    supported_architectures = frozenset(["amd64", "arm64"])

    def kernel_artifact_name(self, output_arch: str) -> str:
        return f"vmlinuz-{output_arch}"

    def initramfs_artifact_name(self, output_arch: str) -> str:
        return f"initramfs-{output_arch}"

    def dtb_artifact_dirname(self, output_arch: str) -> str:
        return "dtb"

    def list_arch_artifacts(self, output_arch: str) -> list[OutputArchArtifact]:
        # Spelled out per-arch (instead of delegating to whichever variant this
        # instance resolved to) so `release publish --target combined` lists the
        # other architecture's artifacts correctly too.
        artifacts = [
            OutputArchArtifact(
                type=OutputArchArtifactType.FILE, name=self.kernel_artifact_name(output_arch)
            ),
            OutputArchArtifact(
                type=OutputArchArtifactType.FILE, name=self.initramfs_artifact_name(output_arch)
            ),
        ]
        if output_arch == "aarch64":
            artifacts.append(OutputArchArtifact(type=OutputArchArtifactType.DIRECTORY, name="dtb"))
        else:
            artifacts.append(
                OutputArchArtifact(
                    type=OutputArchArtifactType.FILE, name=self.iso_artifact_name(output_arch)
                )
            )
        log.info("Artifacts for flavor %s for arch '%s': %s", self.id, output_arch, artifacts)
        return artifacts


@dataclass
class TrixieRpiVariant(TrixieConsolidatedMixin, TrixieArmbianRPiFlavor):
    def firmware_out_dirname(self) -> str:
        # RPi firmware merges into dtb/, making it a self-contained boot dir
        return "dtb"

    def copy_firmware_dir(self, target_fw_dir: Path) -> None:
        # Merge instead of replace: target is dtb/, already populated by
        # collect_dtbs() (which recreates it fresh each build).
        shutil.copytree(self.firmware_output(), target_fw_dir, dirs_exist_ok=True)

    def link_overlays(self, target_fw_dir: Path, overlays_src_dir: Path) -> None:
        # No-op: firmware merges into dtb/, so overlays already sit at
        # dtb/overlays/ — linking would point the files at themselves.
        pass


@dataclass
class TrixieSlimVariant(TrixieConsolidatedMixin, TrixieSlimFlavor):
    pass


@dataclass
class TrixieFlavor(TrixieConsolidatedMixin):
    """Dispatcher: resolves to the per-arch variant during setup()."""

    def has_iso(self) -> bool:
        # Placeholder to satisfy BaseFlavor's abstract method; the resolved
        # variant's implementation is what actually gets used post-setup.
        return False

    def setup(self, cfg: Config, flavor_dir: Path) -> None:
        variant = TrixieRpiVariant if cfg.arch == "arm64" else TrixieSlimVariant
        log.debug("Resolving consolidated trixie flavor to %s for arch %s", variant, cfg.arch)
        self.__class__ = variant  # type: ignore[assignment]
        # Re-dispatch: runs the resolved variant's setup (mixin defines none).
        self.setup(cfg, flavor_dir)
