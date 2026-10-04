import base64
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from installer.__main__ import _get_scheme_dict as get_scheme_dict
from installer.__main__ import _main as main


def test_get_scheme_dict():
    d = get_scheme_dict(distribution_name="foo")
    assert set(d.keys()) >= {"purelib", "platlib", "headers", "scripts", "data"}


def test_get_scheme_dict_prefix():
    d = get_scheme_dict(distribution_name="foo", prefix="/foo")
    for key in ("purelib", "platlib", "headers", "scripts", "data"):
        assert d[key].startswith(f"{os.sep}foo"), (
            f"{key} does not start with /foo: {d[key]}"
        )


def test_main(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main([str(fancy_wheel), "-d", str(destdir)], "python -m installer")

    installed_py_files = destdir.rglob("*.py")

    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert {f.name.split(".")[0] for f in installed_pyc_files} == {
        "__init__",
        "__main__",
    }


def test_main_multiple_wheels(fancy_wheel, another_fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), str(another_fancy_wheel), "-d", str(destdir)],
        "python -m installer",
    )

    for wheel_name in ("fancy", "another_fancy"):
        installed_py_files = destdir.rglob(f"**/{wheel_name}/**/*.py")
        assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

        installed_pyc_files = destdir.rglob(f"**/{wheel_name}/**/*.pyc")
        assert {f.name.split(".")[0] for f in installed_pyc_files} == {
            "__init__",
            "__main__",
        }


def test_main_prefix(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main([str(fancy_wheel), "-d", str(destdir), "-p", "/foo"], "python -m installer")

    installed_py_files = list(destdir.rglob("*.py"))

    for f in installed_py_files:
        assert str(f.parent).startswith(str(destdir / "foo")), (
            f"path does not respect destdir+prefix: {f}"
        )
    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert {f.name.split(".")[0] for f in installed_pyc_files} == {
        "__init__",
        "__main__",
    }


def test_main_no_pyc(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--no-compile-bytecode"],
        "python -m installer",
    )

    installed_py_files = destdir.rglob("*.py")

    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert set(installed_pyc_files) == set()


@pytest.mark.parametrize(
    "validation_part",
    ["all", "entries", "none"],
)
def test_main_validate_record_all_pass(fancy_wheel, tmp_path, validation_part):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--validate-record", validation_part],
        "python -m installer",
    )

    installed_py_files = destdir.rglob("*.py")

    assert {f.stem for f in installed_py_files} == {"__init__", "__main__", "data"}

    installed_pyc_files = destdir.rglob("*.pyc")
    assert {f.name.split(".")[0] for f in installed_pyc_files} == {
        "__init__",
        "__main__",
    }


def test_main_dry_run_writes_nothing(fancy_wheel, tmp_path, capsys):
    destdir = tmp_path / "dest"

    main(
        [
            str(fancy_wheel),
            "-d",
            str(destdir),
            "--dry-run",
            "--compile-bytecode",
            "2",
        ],
        "python -m installer",
    )

    assert not destdir.exists()

    out = capsys.readouterr().out
    assert "Would install" in out
    assert "[purelib]" in out
    assert "[scripts]" in out
    assert "[data]" in out
    assert "fancy-gui" in out


def test_main_plan_json_only_writes_the_json(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"
    plan_json = tmp_path / "plan.json"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--plan-json", str(plan_json)],
        "python -m installer",
    )

    assert not destdir.exists()

    reports = json.loads(plan_json.read_text(encoding="utf-8"))
    assert len(reports) == 1
    report = reports[0]
    assert report["wheel"].endswith("fancy-1.0.0-py2.py3-none-any.whl")
    schemes = report["schemes"]
    assert set(schemes) == {"purelib", "scripts", "data"}

    paths_by_scheme = {
        scheme: [entry["path"] for entry in entries]
        for scheme, entries in schemes.items()
    }
    assert "fancy/__init__.py" in paths_by_scheme["purelib"]
    assert "fancy-1.0.0.dist-info/RECORD" in paths_by_scheme["purelib"]
    assert paths_by_scheme["scripts"] == ["fancy", "fancy-gui"]
    assert paths_by_scheme["data"] == ["fancy/data.py"]

    for entries in schemes.values():
        for entry in entries:
            assert entry["destination"].startswith(str(destdir))
            assert isinstance(entry["size"], int)


def test_main_dry_run_matches_real_install_entry_by_entry(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"
    plan_json = tmp_path / "plan.json"
    common_args = [
        str(fancy_wheel),
        "-d",
        str(destdir),
        "--no-compile-bytecode",
    ]

    main(
        [*common_args, "--plan-json", str(plan_json)],
        "python -m installer",
    )
    assert not destdir.exists()

    main(common_args, "python -m installer")

    report = json.loads(plan_json.read_text(encoding="utf-8"))[0]
    planned_entries = [
        entry for entries in report["schemes"].values() for entry in entries
    ]
    planned_destinations = {entry["destination"] for entry in planned_entries}
    installed_files = {
        str(path) for path in destdir.rglob("*") if path.is_file()
    }
    assert planned_destinations == installed_files

    for entry in planned_entries:
        data = Path(entry["destination"]).read_bytes()
        assert entry["size"] == len(data)
        algorithm, expected_hash = str(entry["hash"]).split("=", 1)
        digest = base64.urlsafe_b64encode(
            hashlib.new(algorithm, data).digest()
        ).decode("ascii").rstrip("=")
        assert digest == expected_hash


def test_main_dry_run_validate_record_runs_first(fancy_wheel, tmp_path, capsys):
    broken_wheel = tmp_path / "broken-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(fancy_wheel) as source_zip:
        with zipfile.ZipFile(broken_wheel, "w") as target_zip:
            for item in source_zip.infolist():
                data = source_zip.read(item.filename)
                if item.filename == "fancy/__init__.py":
                    data = b"def main(): pass\n"
                target_zip.writestr(item, data)

    destdir = tmp_path / "dest"
    with pytest.raises(ValueError):
        main(
            [
                str(broken_wheel),
                "-d",
                str(destdir),
                "--dry-run",
                "--validate-record",
                "all",
            ],
            "python -m installer",
        )

    assert not destdir.exists()
    assert capsys.readouterr().out == ""
