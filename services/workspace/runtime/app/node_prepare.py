from __future__ import annotations

import json
import os
from pathlib import Path
import sys

CATALOG_ROOT = Path(os.getenv("INZOZI_NODE_CATALOG_ROOT", "/opt/inzozi-node"))
CATALOG_MODULES = CATALOG_ROOT / "node_modules"


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return value


def _requested_packages(manifest: dict) -> dict[str, str]:
    requested: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        values = manifest.get(section, {})
        if values is None:
            continue
        if not isinstance(values, dict):
            raise RuntimeError(f"package.json {section} must be an object")
        for name, version in values.items():
            if not isinstance(name, str) or not isinstance(version, str):
                raise RuntimeError(f"package.json {section} entries must be string pairs")
            requested[name] = version
    return requested


def prepare(cwd: Path) -> dict:
    manifest_path = cwd / "package.json"
    if not manifest_path.is_file():
        raise RuntimeError("package.json is required before node_prepare")
    catalog_manifest = _load_json(CATALOG_ROOT / "package.json")
    catalog = _requested_packages(catalog_manifest)
    requested = _requested_packages(_load_json(manifest_path))

    unsupported = {
        name: version
        for name, version in requested.items()
        if catalog.get(name) != version
    }
    if unsupported:
        rendered = ", ".join(f"{name}@{version}" for name, version in sorted(unsupported.items()))
        raise RuntimeError(f"Dependencies are outside the approved guarded-runtime catalog: {rendered}")
    if not CATALOG_MODULES.is_dir():
        raise RuntimeError("Guarded-runtime Node catalog is unavailable")

    target = cwd / "node_modules"
    marker = target / ".inzozi-catalog.json"
    if target.is_symlink():
        raise RuntimeError("Existing node_modules symlink must be removed before node_prepare")
    if target.exists() and not target.is_dir():
        raise RuntimeError("Existing node_modules is not a directory")
    if target.exists() and not marker.is_file():
        raise RuntimeError("Existing node_modules was not prepared by the guarded runtime")
    target.mkdir(exist_ok=True)

    # Keep the project-level node_modules directory writable for tool caches
    # (for example Vitest's node_modules/.vite) while exposing only the fixed,
    # build-time approved catalog through symlinks. Node's resolution follows
    # those symlinks back into the immutable catalog for transitive packages.
    for entry in CATALOG_MODULES.iterdir():
        if entry.name.startswith(".vite") or entry.name == ".cache":
            continue
        link = target / entry.name
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or link.resolve() != entry.resolve():
                raise RuntimeError(f"Existing node_modules entry is not approved: {entry.name}")
            continue
        link.symlink_to(entry, target_is_directory=entry.is_dir())

    marker.write_text(
        json.dumps({"catalog_root": str(CATALOG_ROOT)}, separators=(",", ":")),
        encoding="utf-8",
    )

    return {
        "status": "prepared",
        "packages": len(requested),
        "catalog_root": str(CATALOG_ROOT),
        "network_used": False,
    }


def main() -> int:
    try:
        result = prepare(Path.cwd())
    except RuntimeError as exc:
        print(str(exc))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
