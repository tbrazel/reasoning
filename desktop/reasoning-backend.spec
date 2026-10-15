"""Build the Python executables embedded in the Electron package."""

import json
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
# jsonschema loads bundled specifications by filesystem path. These resources
# belong in the common bundle used by both executables.
# The explicit certifi data keeps urllib TLS portable in the frozen backend.
PACKAGE_DATA = collect_data_files("reasoning_ledger") + collect_data_files("certifi") + collect_data_files("jsonschema_specifications")  # noqa: E501
# These are copied directly from the canonical source directories.  Only the
# Desktop backend consumes hybrid records, so the ledger binary
# must not carry a second copy.
PLATFORM_CONTRACT_DATA = [
    (str(path), f"contracts/platform/{version}")
    for version in ("v1alpha1", "v1alpha2")
    for path in sorted((ROOT / "contracts" / "platform" / version).glob("*.json"))
]


def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Cloud-client manifest contains a duplicate key")
        value[key] = item
    return value


CLOUD_CLIENT_MANIFEST_PATH = (
    ROOT / "contracts" / "cloud-client" / "v1alpha1" / "bundle-manifest.json"
)
CLOUD_CLIENT_MANIFEST = json.loads(
    CLOUD_CLIENT_MANIFEST_PATH.read_text(encoding="utf-8"),
    object_pairs_hook=strict_object,
)
if (
    CLOUD_CLIENT_MANIFEST.get("bundle_digest")
    != "sha256:58f45ca5f9e5a53382b40444399e4cc792d297ead59b6387fd482ab72e0ad6b7"
):
    raise ValueError("Desktop packaging requires the exact accepted Cloud-client bundle")
CLOUD_CLIENT_PATHS = {
    "contracts/cloud-client/v1alpha1/bundle-manifest.json",
    *(item["path"] for key in ("members", "dependencies") for item in CLOUD_CLIENT_MANIFEST[key]),
}
# Platform assets are already the exact verified planner inputs above. Avoid a
# second packaging entry for their two manifest dependencies.
CLOUD_CLIENT_DATA = [
    (str(ROOT / relative), str(Path(relative).parent))
    for relative in sorted(CLOUD_CLIENT_PATHS)
    if not relative.startswith("contracts/platform/")
]
CLOUD_PRODUCT_POLICY_MANIFEST_PATH = (
    ROOT
    / "contracts"
    / "cloud-product-policy"
    / "v1alpha1"
    / "bundle-manifest.json"
)
CLOUD_PRODUCT_POLICY_MANIFEST = json.loads(
    CLOUD_PRODUCT_POLICY_MANIFEST_PATH.read_text(encoding="utf-8"),
    object_pairs_hook=strict_object,
)
if (
    CLOUD_PRODUCT_POLICY_MANIFEST.get("bundle_digest")
    != "sha256:c78d03c0204a2524af0e78209a4cc51c1aaf40cac092b3fb4d0063cc81540b26"
):
    raise ValueError("Web backend packaging requires the exact accepted Cloud product-policy bundle")
CLOUD_PRODUCT_POLICY_PATHS = {
    "contracts/cloud-product-policy/v1alpha1/bundle-manifest.json",
    *(item["path"] for key in ("members", "dependencies") for item in CLOUD_PRODUCT_POLICY_MANIFEST[key]),
}
CLOUD_PRODUCT_POLICY_DATA = [
    (str(ROOT / relative), str(Path(relative).parent))
    for relative in sorted(CLOUD_PRODUCT_POLICY_PATHS)
    if relative not in CLOUD_CLIENT_PATHS
]
COLLABORATION_DATA = [
    (str(path), "contracts/collaboration/v1alpha1")
    for path in sorted((ROOT / "contracts" / "collaboration" / "v1alpha1").glob("*.json"))
]
BACKEND_DATA = (
    PACKAGE_DATA + PLATFORM_CONTRACT_DATA + CLOUD_CLIENT_DATA + CLOUD_PRODUCT_POLICY_DATA + COLLABORATION_DATA
)
HIDDEN_IMPORTS = collect_submodules("reasoning_ledger")

def executable(
    entry: str,
    name: str,
    *,
    binaries=(),
    datas=PACKAGE_DATA,
    hiddenimports=HIDDEN_IMPORTS,
):
    analysis = Analysis(
        [str(ROOT / "desktop" / entry)],
        pathex=[str(ROOT / "src")],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )
    archive = PYZ(analysis.pure)
    return EXE(
        archive,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )


executable("backend_entry.py", "reasoning-ledger-web", datas=BACKEND_DATA)
executable("ledger_entry.py", "reasoning-ledger")
