#!/usr/bin/env python3
"""Write launchd configuration using the interpreter selected by the installer.

  write_launch_agent.py RELAY DESTINATION                     the first Bot
  write_launch_agent.py RELAY DESTINATION --label L --env E --state S --log G --launchd-log D
                                                              an added Bot: its own label, env, state and logs
  --python PATH                                               the interpreter the relay runs on, written as given
                                                              (default: the one running this script)
"""
import argparse
import os
import plistlib
import sys
from pathlib import Path

LEGACY_LABEL = "com.local.agent-relay"


def launch_agent_bytes(home, python, relay, label=None, env=None, state=None, log=None, launchd_log=None):
    home = Path(home)
    variables = {
        "PATH": f"{home}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "REASON_NETWORK_HOME": str(home / ".reason/network"),
    }
    for key, value in (("AGENT_RELAY_ENV", env), ("AGENT_RELAY_STATE", state), ("AGENT_RELAY_LOG", log)):
        if value is not None:
            variables[key] = str(value)
    launchd_log = str(launchd_log) if launchd_log is not None else str(home / "Library/Logs/agent-relay.launchd.log")
    data = {
        "Label": label or LEGACY_LABEL,
        "ProgramArguments": [python, relay],
        "EnvironmentVariables": variables,
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
        "ProcessType": "Background",
        "StandardOutPath": launchd_log,
        "StandardErrorPath": launchd_log,
    }
    return plistlib.dumps(data)


def write_launch_agent(home, python, relay, destination, **bot):
    with open(destination, "wb") as stream:
        stream.write(launch_agent_bytes(home, python, relay, **bot))


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("relay")
    parser.add_argument("destination")
    names = ("label", "env", "state", "log", "launchd_log")
    for name in names:
        parser.add_argument("--" + name.replace("_", "-"))
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    bot = {name: getattr(args, name) for name in names}
    given = [name for name in names if bot[name] is not None]
    if given and len(given) != len(names):
        parser.error("an added Bot needs --label, --env, --state, --log and --launchd-log together")
    write_launch_agent(os.environ["HOME"], args.python, args.relay, args.destination, **bot)


if __name__ == "__main__":
    main(sys.argv[1:])
