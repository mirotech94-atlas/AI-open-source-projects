"""guardian-sensor command-line entry point.

Every subcommand but `selftest` is implemented. Each handler still imports its
module lazily, so the one that is missing (today only `selftest`) reports a
clear "not implemented yet" message instead of crashing with an ImportError.
"""
import argparse
import importlib
import sys

from . import VERSION

NOT_IMPLEMENTED = "guardian-sensor: '{cmd}' is not implemented yet in this build."


def _build_parser():
    parser = argparse.ArgumentParser(prog="guardian-sensor")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the collector loop")
    run_p.add_argument("--once", action="store_true", help="run a single cycle and exit")
    run_p.add_argument("--no-lan", action="store_true", help="skip LAN discovery checks")

    enrol_p = sub.add_parser("enrol", help="enrol this sensor with a hub")
    enrol_p.add_argument("code", help="enrolment code")
    enrol_p.add_argument("--hub", required=True, help="hub base URL")

    install_p = sub.add_parser("install", help="install the sensor as a service")
    install_group = install_p.add_mutually_exclusive_group(required=True)
    install_group.add_argument("--launchd", action="store_true", help="install as a launchd agent (macOS)")
    install_group.add_argument("--systemd", action="store_true", help="install as a systemd service (Linux)")

    sub.add_parser("dns", help="run the DNS filter")
    sub.add_parser("selftest", help="run local self-checks")
    sub.add_parser("version", help="print the sensor version")

    return parser


def _not_implemented(command):
    print(NOT_IMPLEMENTED.format(cmd=command))
    return 1


def _import(name):
    """Import a guardian_sensor submodule by name, letting ModuleNotFoundError
    propagate for the caller to translate into a "not implemented" message."""
    return importlib.import_module(f".{name}", __package__)


def _cmd_run(args):
    try:
        runner = _import("runner")
    except ModuleNotFoundError:
        return _not_implemented("run")
    return runner.main(args)


def _cmd_enrol(args):
    try:
        sync = _import("sync")
    except ModuleNotFoundError:
        return _not_implemented("enrol")
    return sync.enrol(args.code, args.hub)


def _cmd_install(args):
    try:
        install = _import("install")
    except ModuleNotFoundError:
        return _not_implemented("install")
    if args.launchd:
        return install.launchd()
    return install.systemd()


def _cmd_dns(args):
    try:
        dnsfilter = _import("dnsfilter")
    except ModuleNotFoundError:
        return _not_implemented("dns")
    return dnsfilter.main(args)


def _cmd_selftest(args):
    try:
        selftest = _import("selftest")
    except ModuleNotFoundError:
        return _not_implemented("selftest")
    return selftest.main(args)


def _cmd_version(_args):
    print(f"guardian-sensor {VERSION}")
    return 0


_HANDLERS = {
    "run": _cmd_run,
    "enrol": _cmd_enrol,
    "install": _cmd_install,
    "dns": _cmd_dns,
    "selftest": _cmd_selftest,
    "version": _cmd_version,
}


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS[args.command]
    rc = handler(args)
    return rc if rc is not None else 0


if __name__ == "__main__":
    sys.exit(main())
