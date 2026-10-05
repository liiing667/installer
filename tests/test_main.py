import json
import os
import zipfile
from pathlib import Path

import pytest

from installer.__main__ import _get_scheme_dict as get_scheme_dict
from installer.__main__ import _main as main
from installer.records import Hash


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


def test_main_dry_run(fancy_wheel, tmp_path, capsys):
    destdir = tmp_path / "dest"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--dry-run"],
        "python -m installer",
    )

    output = capsys.readouterr().out

    # Output is grouped by scheme, in the canonical scheme order.
    assert output.index("purelib:") < output.index("data:")
    assert "scripts:" in output
    for path in (
        "fancy/__init__.py",
        "fancy/__main__.py",
        "fancy-1.0.0.dist-info/METADATA",
        "fancy-1.0.0.dist-info/RECORD",
        "fancy/data.py",
    ):
        assert path in output
    # Entry-point script names (posix and Windows suffix both contain these).
    assert "    fancy (" in output or "    fancy.exe (" in output
    assert "    fancy-gui (" in output or "    fancy-gui.exe (" in output
    assert "sha256=" in output
    # Nothing was written to disk at all.
    assert not destdir.exists()


def test_main_plan_json_implies_dry_run(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"
    plan_json = tmp_path / "plan.json"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--plan-json", str(plan_json)],
        "python -m installer",
    )

    assert not destdir.exists()
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    assert plan["wheel"] == fancy_wheel.name
    assert "fancy/__init__.py" in [
        entry["path"] for entry in plan["schemes"]["purelib"]
    ]
    assert {entry["path"] for entry in plan["schemes"]["scripts"]} >= {
        "fancy",
        "fancy-gui",
    }


def test_main_plan_json_matches_real_install(fancy_wheel, tmp_path):
    destdir = tmp_path / "dest"
    plan_json = tmp_path / "plan.json"

    main(
        [str(fancy_wheel), "-d", str(destdir), "--plan-json", str(plan_json)],
        "python -m installer",
    )
    assert not destdir.exists()
    plan = json.loads(plan_json.read_text(encoding="utf-8"))

    # Now perform the actual installation with the same wheel and options.
    main([str(fancy_wheel), "-d", str(destdir)], "python -m installer")

    scheme_dict = get_scheme_dict(distribution_name="fancy")
    planned_files = set()
    for scheme, entries in plan["schemes"].items():
        anchor = Path(scheme_dict[scheme]).anchor
        relative_root = Path(scheme_dict[scheme]).relative_to(anchor)
        for entry in entries:
            installed_file = destdir / relative_root / entry["path"]
            assert installed_file.is_file(), (
                f"planned file missing from real install: {installed_file}"
            )
            data = installed_file.read_bytes()
            if entry["size"] is not None:
                assert len(data) == entry["size"]
            if entry["hash"] is not None:
                assert Hash.parse(entry["hash"]).validate(data)
            planned_files.add(installed_file)

    # Bytecode is not part of RECORD/plans; every other installed file was
    # predicted by the plan, entry-by-entry.
    installed_files = {
        path
        for path in destdir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert installed_files == planned_files


def test_main_dry_run_and_plan_json(fancy_wheel, tmp_path, capsys):
    destdir = tmp_path / "dest"
    plan_json = tmp_path / "plan.json"

    main(
        [
            str(fancy_wheel),
            "-d",
            str(destdir),
            "--dry-run",
            "--plan-json",
            str(plan_json),
        ],
        "python -m installer",
    )

    assert capsys.readouterr().out  # plan was printed as well
    assert json.loads(plan_json.read_text(encoding="utf-8"))["schemes"]
    assert not destdir.exists()


def test_main_dry_run_validate_record_then_plan(fancy_wheel, tmp_path, capsys):
    destdir = tmp_path / "dest"

    main(
        [
            str(fancy_wheel),
            "-d",
            str(destdir),
            "--dry-run",
            "--validate-record",
            "all",
        ],
        "python -m installer",
    )

    assert "purelib:" in capsys.readouterr().out
    assert not destdir.exists()


def _wheel_with_broken_record(wheel_path, target_path):
    with (
        zipfile.ZipFile(wheel_path) as source,
        zipfile.ZipFile(target_path, "w") as target,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.endswith(".dist-info/RECORD"):
                data = data.replace(b"sha256=", b"sha256=AAAA")
            target.writestr(item, data)


def test_main_dry_run_validate_record_failure(fancy_wheel, tmp_path, capsys):
    broken_wheel = tmp_path / "broken-1.0.0-py3-none-any.whl"
    _wheel_with_broken_record(fancy_wheel, broken_wheel)

    with pytest.raises(ValueError):
        main(
            [str(broken_wheel), "--dry-run", "--validate-record", "all"],
            "python -m installer",
        )

    assert capsys.readouterr().out == ""


def test_main_plan_json_multiple_wheels(
    fancy_wheel, another_fancy_wheel, tmp_path, capsys
):
    destdir = tmp_path / "dest"
    plan_json = tmp_path / "plan.json"

    main(
        [
            str(fancy_wheel),
            str(another_fancy_wheel),
            "-d",
            str(destdir),
            "--dry-run",
            "--plan-json",
            str(plan_json),
        ],
        "python -m installer",
    )

    plans = json.loads(plan_json.read_text(encoding="utf-8"))
    assert isinstance(plans, list)
    assert [plan["wheel"] for plan in plans] == [
        fancy_wheel.name,
        another_fancy_wheel.name,
    ]

    output = capsys.readouterr().out
    assert output.index(fancy_wheel.name) < output.index(another_fancy_wheel.name)
    assert not destdir.exists()
