"""Reproducible source delivery with locked dependencies, not a Python distribution."""

import argparse
import io
import json
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .source_install import inspect_source


def source_bytes(root, requirements, modal_requirements):
    from .deployment.release import read_file

    package = root / "src/reasoning_ledger"
    if package.is_symlink() or not package.is_dir():
        raise ValueError("source root must contain a regular Python source tree")
    files = {}
    for path in sorted(package.rglob("*")):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("source delivery refuses symlinks")
        if path.is_dir():
            continue
        if path.suffix not in {".py", ".cpp", ".md", ".json", ".js", ".txt", ".yaml", ".toml"}:
            raise ValueError("unexpected file type in source tree")
        files[path.relative_to(root / "src").as_posix()] = read_file(path)
    scripts = tomllib.loads(read_file(root / "pyproject.toml").decode())["project"]["scripts"]
    files.update({"source-info.json": json.dumps({"format": 1, "scripts": scripts}, sort_keys=True).encode(),
                  "requirements.txt": requirements, "modal-requirements.txt": modal_requirements,
                  "LICENSE": read_file(root / "LICENSE"), "NOTICE": read_file(root / "NOTICE")})
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, body in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, body)
    with ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        inspect_source(archive)
    return buffer.getvalue()


def build_source(root, output):
    from .deployment.release import export_requirements

    body = source_bytes(root, export_requirements(root), export_requirements(root, modal=True))
    if output.exists() or output.is_symlink():
        if output.is_symlink() or not output.is_file() or output.stat().st_size != len(body) or output.read_bytes() != body:
            raise ValueError("source archive differs; retain it and choose a fresh source/state path")
        return output
    with output.open("xb") as stream:
        stream.write(body)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    build_source(args.source_root, args.output)
    print("Source archive verified; application modules, entry points, locked dependencies and notices included.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
