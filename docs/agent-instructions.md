# Agent Instructions

The `screenshot-info` payload is intentionally small:

```text
screenshot-info:
  machine: work-mac
  path: /Users/alex/Pictures/Screenshots/example.png
```

It works because the receiving coding agent has global instructions for turning
that payload into a durable local artifact.

Reference implementation: [botfiles PR #23](https://github.com/ma08/botfiles/pull/23)
adds the behavior to global Codex and Claude instructions.

## Recommended Global Instruction

Add a version of this to your agent instructions:

```text
When the user pastes a screenshot payload like:

screenshot-info:
  machine: <ssh-alias-or-local-machine-token>
  path: <absolute-source-path>

treat it as user-provided screenshot input.

If the path is local to the current machine, copy it into the active task's
user_inputs/input_artifacts/ folder before inspecting it.

If the path is on another machine, treat machine as an SSH host alias and copy
the file with scp or sftp:

scp '<machine>:<path>' user_inputs/input_artifacts/

After copying, update user_inputs/input_artifacts/index.md with the local path,
original machine/path, capture time, and a short note. Prefer the local copied
artifact in future task notes instead of relying on the external path.
```

## Machine Alias Convention

Set a machine-specific alias in the environment used by the LaunchAgent:

```bash
export BOT_MACHINE_SSH_ALIAS=work-mac
```

Every other machine where agents run should have an SSH config entry that can
resolve that alias:

```sshconfig
Host work-mac
  HostName <reachable-hostname-or-tailscale-name>
  User alex
```

You can also pass the alias directly:

```bash
uv run omnishot menubar --ssh-host-hint work-mac
```

## Why Copy Into Task Artifacts

Do not leave the screenshot only at the source path. Copy it into the task
folder so the work remains reproducible after clipboard state, local screenshots,
or short-lived URLs disappear.
