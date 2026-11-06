#!/usr/bin/env python3
"""Write launchd configuration using the interpreter selected by the installer."""
import os
import plistlib
import sys
from pathlib import Path


def launch_agent_bytes(home, python, relay):
    home = Path(home)
    data = {
        "Label": "com.local.agent-relay",
        "ProgramArguments": [python, relay],
        "EnvironmentVariables": {
            "PATH": f"{home}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "REASON_NETWORK_HOME": str(home / ".reason/network"),
        },
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
        "ProcessType": "Background",
        "StandardOutPath": str(home / "Library/Logs/agent-relay.launchd.log"),
        "StandardErrorPath": str(home / "Library/Logs/agent-relay.launchd.log"),
    }
    return plistlib.dumps(data)


def write_launch_agent(home, python, relay, destination):
    with open(destination, "wb") as stream:
        stream.write(launch_agent_bytes(home, python, relay))


if __name__ == "__main__":
    write_launch_agent(os.environ["HOME"], sys.executable, *sys.argv[1:])
