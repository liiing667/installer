import io
from pathlib import Path

import pytest

from installer.destinations import (
    DryRunDestination,
    SchemeDictionaryDestination,
    WheelDestination,
)
from installer.records import RecordEntry, parse_record_file
from installer.scripts import Script
from installer.utils import SCHEME_NAMES


class TestWheelDestination:
    def test_takes_no_arguments(self):
        WheelDestination()

    def test_raises_not_implemented_error(self):
        destination = WheelDestination()

        with pytest.raises(NotImplementedError):
            destination.write_script(name=None, module=None, attr=None, section=None)

        with pytest.raises(NotImplementedError):
            destination.write_file(
                scheme=None, path=None, stream=None, is_executable=False
            )

        with pytest.raises(NotImplementedError):
            destination.finalize_installation(
                scheme=None,
                record_file_path=None,
                records=None,
            )


class TestSchemeDictionaryDestination:
    @pytest.fixture()
    def destination(self, tmp_path):
        scheme_dict = {}
        for scheme in SCHEME_NAMES:
            full_path = tmp_path / scheme
            if not full_path.exists():
                full_path.mkdir()
            scheme_dict[scheme] = str(full_path)
        return SchemeDictionaryDestination(scheme_dict, "/my/python", "posix")

    @pytest.fixture()
    def destination_overwrite_existing(self, tmp_path):
        scheme_dict = {}
        for scheme in SCHEME_NAMES:
            full_path = tmp_path / scheme
            if not full_path.exists():
                full_path.mkdir()
            scheme_dict[scheme] = str(full_path)
        return SchemeDictionaryDestination(
            scheme_dict, "/my/python", "posix", overwrite_existing=True
        )

    @pytest.mark.parametrize(
        ("scheme", "path", "data", "expected"),
        [
            pytest.param(
                "data", "my_data.bin", b"my data", b"my data", id="normal file"
            ),
            pytest.param(
                "data",
                "data_folder/my_data.bin",
                b"my data",
                b"my data",
                id="normal file in subfolder",
            ),
            pytest.param(
                "scripts",
                "my_script.py",
                b"#!python\nmy script",
                b"#!/my/python\nmy script",
                id="script file",
            ),
            pytest.param(
                "scripts",
                "script_folder/my_script.py",
                b"#!python\nmy script",
                b"#!/my/python\nmy script",
                id="script file in subfolder",
            ),
        ],
    )
    def test_write_file(self, destination, scheme, path, data, expected):
        record = destination.write_file(scheme, path, io.BytesIO(data), False)
        file_data = (Path(destination.scheme_dict[scheme]) / path).read_bytes()
        assert file_data == expected
        assert record.path == path

    def test_write_record_duplicate(self, destination):
        destination.write_file("data", "my_data.bin", io.BytesIO(b"my data"), False)
        with pytest.raises(FileExistsError):
            destination.write_file("data", "my_data.bin", io.BytesIO(b"my data"), False)

    def test_write_record_duplicate_with_overwrite_existing(
        self, destination_overwrite_existing
    ):
        destination_overwrite_existing.write_file(
            "data", "my_data.bin", io.BytesIO(b"my data"), False
        )
        destination_overwrite_existing.write_file(
            "data", "my_data.bin", io.BytesIO(b"my data"), False
        )

    def test_write_script(self, destination):
        script_args = ("my_entrypoint", "my_module", "my_function", "console")
        record = destination.write_script(*script_args)
        file_path = Path(destination.scheme_dict["scripts"]) / "my_entrypoint"

        assert file_path.is_file()

        file_data = file_path.read_bytes()
        _, expected_data = Script(*script_args).generate("/my/python", "posix")

        assert file_data == expected_data
        assert record.path == "my_entrypoint"

    def test_finalize_write_record(self, destination):
        records = [
            (
                "data",
                destination.write_file(
                    "data",
                    "my_data1.bin",
                    io.BytesIO(b"my data 1"),
                    is_executable=False,
                ),
            ),
            (
                "data",
                destination.write_file(
                    "data",
                    "my_data2.bin",
                    io.BytesIO(b"my data 2"),
                    is_executable=False,
                ),
            ),
            (
                "data",
                destination.write_file(
                    "data",
                    "my_data3,my_data4.bin",
                    io.BytesIO(b"my data 3"),
                    is_executable=False,
                ),
            ),
            (
                "scripts",
                destination.write_file(
                    "scripts",
                    "my_script",
                    io.BytesIO(b"my script"),
                    is_executable=True,
                ),
            ),
            (
                "scripts",
                destination.write_file(
                    "scripts",
                    "my_script2",
                    io.BytesIO(b"#!python\nmy script"),
                    is_executable=False,
                ),
            ),
            (
                "scripts",
                destination.write_script(
                    "my_entrypoint", "my_module", "my_function", "console"
                ),
            ),
            ("purelib", RecordEntry("RECORD", None, None)),
        ]

        destination.finalize_installation("purelib", "RECORD", records)
        file_path = Path(destination.scheme_dict["purelib"]) / "RECORD"

        data = file_path.read_bytes()
        expected_data = (
            b"RECORD,,\n"
            b"data/my_data1.bin,sha256=NV0A-M4OPuqTsHjeD6Wth_-UqrpAAAdyplcustFZ8s4,9\n"
            b"data/my_data2.bin,sha256=lP7V8oWLqgyXCbdASNiPdsUogzPUZhht_7F8T5bC3eQ,9\n"
            b'data/my_data3,my_data4.bin",sha256=18krruu1gr01x-WM_9ChSASoHv0mfRAV6-B2bd9sxpo,9\n'
            b"scripts/my_entrypoint,sha256=_p_9nwmeIeoMBfQ0akhr1KbKn3laDydg0J7cy0Fs6JI,216\n"
            b"scripts/my_script,sha256=M60fWvUSMJkPtw2apUvjWWwOcnRPcVy_zO4-4lpH08o,9\n"
            b"scripts/my_script2,sha256=k9_997kTbTYQm7EXFLclVZL1m2N98rU90QX46XeMvjY,22\n"
        )
        assert all(
            real.endswith(expected)
            for real, expected in zip(
                data.split(b"\n"), expected_data.split(b"\n"), strict=True
            )
        )

    def test_blocking_path_traversal(self, destination):
        with pytest.raises(ValueError):
            destination._path_with_destdir("purelib", "subdir/../../outside.txt")


class TestDryRunDestination:
    @pytest.fixture()
    def scheme_dict(self, tmp_path):
        scheme_dict = {}
        for scheme in SCHEME_NAMES:
            full_path = tmp_path / scheme
            full_path.mkdir()
            scheme_dict[scheme] = str(full_path)
        return scheme_dict

    @pytest.mark.parametrize(
        ("scheme", "path", "data"),
        [
            ("data", "my_data.bin", b"my data"),
            ("data", "data_folder/my_data.bin", b"my data"),
            ("scripts", "my_script.py", b"#!python\nmy script"),
            ("scripts", "script_folder/my_script.py", b"#!python\nmy script"),
            ("purelib", "my_module/__init__.py", b"def x(): ...\n"),
        ],
    )
    def test_write_file_matches_real_install(self, scheme_dict, scheme, path, data):
        dry_destination = DryRunDestination("/my/python", "posix")
        real_destination = SchemeDictionaryDestination(
            scheme_dict, "/my/python", "posix"
        )

        before = sorted(Path(scheme_dict[scheme]).rglob("*"))

        dry_record = dry_destination.write_file(scheme, path, io.BytesIO(data), False)
        assert sorted(Path(scheme_dict[scheme]).rglob("*")) == before

        real_record = real_destination.write_file(scheme, path, io.BytesIO(data), False)

        assert dry_record == real_record

    def test_write_script_matches_real_install(self, scheme_dict):
        script_args = ("my_entrypoint", "my_module", "my_function", "console")
        dry_destination = DryRunDestination("/my/python", "posix")
        real_destination = SchemeDictionaryDestination(
            scheme_dict, "/my/python", "posix"
        )

        before = sorted(Path(scheme_dict["scripts"]).rglob("*"))

        dry_record = dry_destination.write_script(*script_args)
        assert sorted(Path(scheme_dict["scripts"]).rglob("*")) == before

        real_record = real_destination.write_script(*script_args)

        assert dry_record == real_record

    def test_finalize_collects_records_without_writing(self, scheme_dict):
        dry_destination = DryRunDestination("/my/python", "posix")
        real_destination = SchemeDictionaryDestination(
            scheme_dict, "/my/python", "posix"
        )

        operations = [
            ("write_file", ("data", "my_data.bin", b"my data", False)),
            ("write_file", ("scripts", "my_script", b"#!python\nmy script", False)),
            ("write_script", ("my_entrypoint", "my_module", "my_function", "console")),
        ]

        dry_records = []
        real_records = []
        for method_name, args in operations:
            if method_name == "write_file":
                scheme, path, data, is_executable = args
                dry_record = dry_destination.write_file(
                    scheme, path, io.BytesIO(data), is_executable
                )
                real_record = real_destination.write_file(
                    scheme, path, io.BytesIO(data), is_executable
                )
            else:
                dry_record = dry_destination.write_script(*args)
                real_record = real_destination.write_script(*args)
            assert dry_record == real_record
            dry_records.append(
                ("scripts" if method_name == "write_script" else args[0], dry_record)
            )
            real_records.append(
                ("scripts" if method_name == "write_script" else args[0], real_record)
            )

        dry_records.append(("purelib", RecordEntry("RECORD", None, None)))
        real_records.append(("purelib", RecordEntry("RECORD", None, None)))

        before = {
            scheme: sorted(Path(path).rglob("*"))
            for scheme, path in scheme_dict.items()
        }
        dry_destination.finalize_installation("purelib", "RECORD", dry_records)

        assert dry_destination.records == dry_records
        # The dry run does not write RECORD or bytecode anywhere.
        for scheme, path in scheme_dict.items():
            assert sorted(Path(path).rglob("*")) == before[scheme]

        real_destination.finalize_installation("purelib", "RECORD", real_records)

        written_record = (Path(scheme_dict["purelib"]) / "RECORD").read_bytes()
        written_rows = list(parse_record_file(written_record.decode().splitlines()))
        assert len(written_rows) == 4
        written_paths = {row[0] for row in written_rows}
        for expected_path in (
            "data/my_data.bin",
            "scripts/my_script",
            "scripts/my_entrypoint",
            "RECORD",
        ):
            assert any(
                written_path.endswith(expected_path) for written_path in written_paths
            )
