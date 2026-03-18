# R.E.M — Remote Environment Manager

A browser-based terminal UI for managing your Gong local development environment. Run commands, manage modules, and control your k8s pods — all from a clean web interface.

## Requirements

- Python 3 (macOS ships with it; verify with `python3 --version`)
- `kubectl` configured with your active cluster context
- `gong-module-runner` available in your PATH
- `gong-modules-base.yaml` at `~/develop/code/gong-build-commons/dev/gong-module-runner/conf/gong-modules-base.yaml`

## Starting the App

```bash
./start_app.sh
```

The script will:
1. Create a Python virtual environment (first run only)
2. Install Flask and PyYAML (first run only)
3. Start the server at `http://localhost:5000`
4. Open your browser automatically

To stop the server, press `Ctrl+C` in the terminal.

## Interface Overview

### Terminal (main area)

Type any shell command in the input box and press **Enter** or click **Run**.

- Output streams in real-time
- Interactive prompts (e.g. sudo password) are supported — type your response in the input bar that appears at the bottom and press Enter
- Use the **Clear** button to wipe the terminal output

### Quick Actions (top bar)

| Button | Command | Description |
|--------|---------|-------------|
| Status\Connect | `gong-module-run remote --status` | Check remote connection status; automatically opens Pod Watch if the environment is sleeping |
| Get Latest | `gong-module-run remote --refresh-modules` | Pull latest module versions |
| Refetch DB | `gong-module-run synthetic-data --synthetic-data-image-tag latest --remote` | Refresh the database |
| FF and DB Refresh | `gong-module-run remote --init` | Refresh feature flags and database |
| Pod Version | `kubectl get pods` | Show running pods with image tags and status |

### Sidebar Tabs

#### Groups
Modules organized by their functional group (as defined in `gong-modules-base.yaml`).

- Click a **module name** to insert it into the command input
- Hover a module to reveal **up** / **down** action buttons to start or stop it
- Hover a **group title** to reveal bulk **Up** / **Down** buttons for the whole group
- Use the search box to filter modules by name

#### Subsystems
Same module list, organized by subsystem instead of group.

- Same hover actions as Groups
- Click a subsystem name to copy it to clipboard
- Use the search box to filter by subsystem name or module name — searching "engage" shows the full `gong-engage` subsystem even if individual module names don't contain the term

#### My Groups
Saved custom pod selections — your personal shortlist of modules that persist across sessions.

- Create a group: switch to **🟢 Live Pods**, click **☑️ Create**, pick pods, then click **⭐ Create Group**
- Groups are saved to `custom_groups.json` next to the app — they survive browser cache clears
- **⬆️ Up** / **⬇️ Down** buttons on the group header run all modules in one command
- Click **➕** on the group header to add a pod/module — autocomplete shows currently running pods not already in the group; type any name and press Enter
- Hover a pod row and click **✕** to remove that pod from the group
- Individual pod hover actions (Up · Down · Intercept · Branch · Logs) work the same as in Live Pods
- Taking a pod down does **not** remove it from the group — the group stores module names, not pod instances
- Click 🗑️ to delete the entire group

#### Running Pods
Live view of pods currently running in your active kubectl namespace.

- Grouped by subsystem
- Refreshes on demand — click the refresh icon or switch to the tab
- Hover a pod to reveal quick action buttons: **⬆️ Up** · **⬇️ Down** · **🔌 Intercept** · **🌿 Branch** · **📋 Logs**
- Click **☑️ Create** to enter select mode — check pods and click **⭐ Create Group** to save them as a custom group
- Click **👁 Watch** to open a live `kubectl get pods -w` stream with color-coded pod status (yellow = starting, green = ready, red = error, gray = terminating); each pod occupies a single row that updates in place as its status changes

#### Pod Log Viewer
Click the **📋** button on any running pod to open a full-screen live log stream.

| Action | Result |
|--------|--------|
| Click 📋 on a pod | Opens log viewer, streams last 300 lines by default |
| **Last 300 / 1K / 5K / 10K / All lines** dropdown | Re-streams with the selected number of lines (All lines = from the very beginning) |
| Scroll up | Auto-pauses scrolling (yellow border = paused) |
| Scroll to bottom | Auto-resumes |
| Click ⏸ Pause | Locks scroll; new lines still append |
| Click ▶ Resume | Scrolls to bottom and re-enables auto-scroll |
| Select text + Ctrl+C | Normal browser copy (text is selectable) |
| Click **Copy All** | Copies entire log contents to clipboard |
| Click **↺ Restart stream** button | Re-opens the stream after the pod restarts or the stream drops |
| Press **Esc** or click **✕** | Closes the log viewer |

The viewer automatically picks the main app container, skipping sidecars like `linkerd-proxy`.

### Branch-based Actions

On any module or group **Up** button, you can optionally specify a git branch to run from.
A modal will appear where you can type a branch name or select from recently used branches.

### Intercept Banner

When modules are intercepted (traffic routed to your local process), a banner appears at the top showing which modules are active. Click **Check Now** to refresh the intercept status.

## Keyboard Shortcuts

Open the shortcuts panel with the **?** button in the header.

## Troubleshooting

**Port 5000 already in use:** The startup script detects this and opens the existing instance in your browser instead of starting a new one.

**`kubectl` not found:** Make sure `kubectl` is installed and in your PATH. The pods tab and namespace detection will be unavailable without it.

**YAML file not found:** The Groups and Subsystems tabs require `gong-modules-base.yaml` to be present at the expected path. The terminal and Quick Actions still work without it.

**Interactive command not responding:** Make sure you are typing in the input bar at the bottom of the screen (it appears automatically when a command is running and waiting for input).
