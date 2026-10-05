"""Installer CLI."""

import argparse
import json
import os.path
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, TypedDict

import installer
from installer.destinations import (
    DryRunDestination,
    SchemeDictionaryDestination,
    WheelDestination,
)
from installer.records import RecordEntry
from installer.sources import WheelFile
from installer.utils import SCHEME_NAMES, Scheme, get_launcher_kind


class _PlanEntry(TypedDict):
    """JSON-serializable representation of a single planned file."""

    path: str
    size: int | None
    hash: str | None


class _Plan(TypedDict):
    """JSON-serializable installation plan for a single wheel."""

    wheel: str
    schemes: dict[str, list[_PlanEntry]]


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
            "don't install anything; print the files that would be written,"
            " grouped by scheme"
        ),
    )
    parser.add_argument(
        "--plan-json",
        metavar="path",
        type=str,
        help=(
            "write the installation plan as machine-readable JSON to this file"
            " (the only file written); implies --dry-run"
        ),
    )
    return parser


def _build_plan(wheel: str, records: list[tuple[Scheme, RecordEntry]]) -> _Plan:
    """Group the planned records by scheme."""
    schemes: dict[str, list[_PlanEntry]] = {scheme: [] for scheme in SCHEME_NAMES}
    for scheme, record in records:
        schemes[scheme].append(
            {
                "path": record.path,
                "size": record.size,
                "hash": str(record.hash_) if record.hash_ is not None else None,
            }
        )

    return {
        "wheel": Path(wheel).name,
        "schemes": {scheme: entries for scheme, entries in schemes.items() if entries},
    }


def _print_plan(plan: _Plan, stream: TextIO) -> None:
    """Print the plan, grouped by scheme, in a human-readable form."""
    stream.write(f"{plan['wheel']}:\n")
    for scheme, entries in plan["schemes"].items():
        stream.write(f"  {scheme}:\n")
        for entry in entries:
            if entry["hash"]:
                stream.write(
                    f"    {entry['path']} ({entry['hash']}, {entry['size']} bytes)\n"
                )
            else:
                stream.write(f"    {entry['path']}\n")


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
    plans: list[_Plan] = []

    for wheel in args.wheel:
        with WheelFile.open(wheel) as source:
            if args.validate_record != "none":
                source.validate_record(validate_contents=args.validate_record == "all")
            destination: WheelDestination
            if plan_mode:
                destination = DryRunDestination(
                    interpreter=sys.executable,
                    script_kind=get_launcher_kind(),
                )
                installer.install(source, destination, {})
                plans.append(_build_plan(wheel, destination.records))
            else:
                destination = SchemeDictionaryDestination(
                    scheme_dict=_get_scheme_dict(
                        source.distribution, prefix=args.prefix
                    ),
                    interpreter=sys.executable,
                    script_kind=get_launcher_kind(),
                    bytecode_optimization_levels=bytecode_levels,
                    destdir=args.destdir,
                    overwrite_existing=args.overwrite_existing,
                )
                installer.install(source, destination, {})

    if plan_mode:
        if args.dry_run:
            for index, plan in enumerate(plans):
                if index:
                    sys.stdout.write("\n")
                _print_plan(plan, sys.stdout)

        if args.plan_json is not None:
            report = plans[0] if len(plans) == 1 else plans
            Path(args.plan_json).write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":  # pragma: no cover
    _main(sys.argv[1:], "python -m installer")
