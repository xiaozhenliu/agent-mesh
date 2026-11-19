import re
import shlex
import subprocess

CLOUD_ROOT = "/workspace/agent-mesh"
OK = "agent-mesh-instructions OK {n}"


def stub_commands(stub):
    lines = stub.split("\n")
    step1 = [l.strip() for l in lines if l.strip().startswith(("bash ", "RN_BOT=")) and " instructions " in l]
    step3 = [l.strip() for l in lines if l.strip().startswith(("bash -c '", "RN_BOT=")) and "--stdin" in l]
    version = re.search(r"agent-mesh routine stub v(\d+)\.", stub)
    if len(step1) != 1 or len(step3) != 1 or not version:
        raise ValueError("the stub must have exactly one step 1 command, one step 3 command and a version")
    if not step3[0].endswith(" _ '<id>' '<id>' ..."):
        raise ValueError("the step 3 command must end with the id placeholders")
    return step1[0], step3[0][: -len(" '<id>' '<id>' ...")], int(version[1])


def full_command(text):
    found = [l.strip() for l in text.split("\n") if "--stdin 0 - " in l]
    if len(found) != 1 or not found[0].endswith(" _ '<id>'"):
        raise ValueError("expected exactly one fixed command ending with _ '<id>'")
    return found[0][: -len(" '<id>'")]


def last_line(text):
    lines = [l for l in text.split("\n") if l.strip()]
    return lines[-1] if lines else ""


class StubRunner:
    def __init__(self, stub, root, env, timeout=300):
        self.step1, self.step3, self.version = stub_commands(stub)
        self.root, self.env, self.timeout = str(root), env, timeout
        self.log = []

    def shell(self, command):
        command = command.replace(CLOUD_ROOT, self.root)
        p = subprocess.run(["bash", "-c", command], env=self.env, capture_output=True, text=True,
                           timeout=self.timeout, stdin=subprocess.DEVNULL)
        self.log.append((command, p.returncode, p.stdout, p.stderr))
        return p

    def loaded(self, p):
        return p.returncode == 0 and last_line(p.stdout) == OK.format(n=self.version)

    def run(self, ids):
        first = self.shell(self.step1)
        if self.loaded(first):
            return "loaded", first.stdout, None
        boot = self.shell(self.step3 + "".join(" " + shlex.quote(i) for i in ids))
        tail = last_line(boot.stdout)
        if tail == "agent-mesh-bootstrap RELOAD":
            again = self.shell(self.step1)
            return ("reloaded" if self.loaded(again) else "reload-failed"), again.stdout, boot.stdout
        if tail == "agent-mesh-bootstrap SKIP":
            return "skipped", first.stdout, boot.stdout
        return "answered", first.stdout, boot.stdout
