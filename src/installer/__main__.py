"""Installer CLI."""

import argparse
import json
import os.path
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

import installer
from installer.destinations import PlanDestination, SchemeDictionaryDestination
from installer.records import RecordEntry
from installer.sources import WheelFile
from installer.utils import SCHEME_NAMES, Scheme, get_launcher_kind


def _get_main_parser() -> argparse.ArgumentParser:
    """Construct the main parser."""
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=str, nargs="+", help="wheel file to install")
    parser.add_argument(
        "--destdir",
        "-d",
        metavar="path",
        type=str,
        help="destination directory (prefix to prepend to each file)",
    )
    parser.add_argument(
        "--prefix",
        "-p",
        metavar="path",
        type=str,
        help="override prefix to install packages to",
    )
    parser.add_argument(
        "--compile-bytecode",
        action="append",
        metavar="level",
        type=int,
        choices=[0, 1, 2],
        help="generate bytecode for the specified optimization level(s) (default=0, 1)",
    )
    parser.add_argument(
        "--no-compile-bytecode",
        action="store_true",
        help="don't generate bytecode for installed modules",
    )
    parser.add_argument(
        "--validate-record",
        metavar="part",
        default="none",
        type=str,
        choices=["all", "entries", "none"],
        help="validate the wheel against certain part of its record (default=none)",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="silently overwrite existing files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "don't write any files; print the files that would be installed, "
            "grouped by scheme"
        ),
    )
    parser.add_argument(
        "--plan-json",
        metavar="path",
        type=str,
        help=(
            "write the installation plan as machine-readable JSON to this path "
            "(the only file that gets written; implies --dry-run)"
        ),
    )
    return parser


def _get_scheme_dict(
    distribution_name: str, prefix: str | None = None
) -> dict[str, str]:
    """Calculate the scheme dictionary for the current Python environment."""
    vars = {}
    if prefix is None:
        installed_base = sysconfig.get_config_var("base")
        assert installed_base
    else:
        vars["base"] = vars["platbase"] = installed_base = prefix

    scheme_dict = sysconfig.get_paths(vars=vars)

    # calculate 'headers' path, not currently in sysconfig - see
    # https://bugs.python.org/issue44445. This is based on what distutils does.
    # TODO: figure out original vs normalised distribution names
    scheme_dict["headers"] = os.path.join(  # noqa: PTH118
        sysconfig.get_path("include", vars={"installed_base": installed_base}),
        distribution_name,
    )

    return scheme_dict


def _main(cli_args: Sequence[str], program: str | None = None) -> None:
    """Process arguments and perform the install."""
    parser = _get_main_parser()
    if program:
        parser.prog = program
    args = parser.parse_args(cli_args)

    bytecode_levels = args.compile_bytecode
    if args.no_compile_bytecode:
        bytecode_levels = []
    elif not bytecode_levels:
        bytecode_levels = [0, 1]

    plan_mode = args.dry_run or args.plan_json is not None
    plan_reports: list[dict[str, object]] = []

    for wheel in args.wheel:
        with WheelFile.open(wheel) as source:
            if args.validate_record != "none":
                source.validate_record(validate_contents=args.validate_record == "all")
            destination_class = (
                PlanDestination if plan_mode else SchemeDictionaryDestination
            )
            destination = destination_class(
                scheme_dict=_get_scheme_dict(source.distribution, prefix=args.prefix),
                interpreter=sys.executable,
                script_kind=get_launcher_kind(),
                bytecode_optimization_levels=bytecode_levels,
                destdir=args.destdir,
                overwrite_existing=args.overwrite_existing,
            )
            installer.install(source, destination, {})
            if plan_mode:
                schemes = _plan_schemes(destination.plan)
                plan_reports.append({"wheel": wheel, "schemes": schemes})
                _print_plan_report(wheel, schemes)

    if args.plan_json is not None:
        Path(args.plan_json).write_text(
            json.dumps(plan_reports, indent=2) + "\n", encoding="utf-8"
        )


def _plan_schemes(
    plan: list[tuple[Scheme, RecordEntry, str]]
) -> dict[str, list[dict[str, str | int | None]]]:
    """Group the recorded plan entries by scheme, in scheme order."""
    schemes: dict[str, list[dict[str, str | int | None]]] = {
        name: [] for name in SCHEME_NAMES
    }
    for scheme, record, target_path in plan:
        schemes[scheme].append(
            {
                "path": record.path,
                "destination": target_path,
                "hash": str(record.hash_) if record.hash_ is not None else None,
                "size": record.size,
            }
        )
    return {name: schemes[name] for name in SCHEME_NAMES if schemes[name]}


def _print_plan_report(
    wheel: str, schemes: dict[str, list[dict[str, str | int | None]]]
) -> None:
    """Print the installation plan, grouped by scheme."""
    lines = [f"Would install {wheel}:"]
    for scheme, entries in schemes.items():
        lines.append(f"[{scheme}]")
        lines.extend(f"  {entry['destination']}" for entry in entries)
    print("\n".join(lines))  # noqa: T201


if __name__ == "__main__":  # pragma: no cover
    _main(sys.argv[1:], "python -m installer")
