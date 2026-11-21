# Installing Mindboard - a guide for AI agents

You are an AI agent (Claude Code, Codex, Cursor or similar) asked to set
Mindboard up on this computer. This file is the whole procedure: what to check,
what to run, what to ask the person, and how to know it worked. Follow it in
order. People should read [README.md](README.md) instead.

Mindboard is a local-first mind-map canvas with a chat over the person's own
notes, answered by models that run on this computer. Nothing it does leaves
the machine, and nothing you do while installing it should either.

## Ground rules

- **Ask before anything large or system-wide.** Installing Node.js or Python,
  and downloading the AI models (several GB) or the voice (about 520 MB), are
  the person's decisions. Say what it is, how big, and why; wait for a yes.
- **Never run as administrator / root** except the one package-manager command
  the person has agreed to (Linux `sudo apt-get install ...`).
- **Do not edit Mindboard's files, commit, or change branches.** The installer
  and `mindboard update` refuse to work over local changes.
- **Never send the person's boards, data folder or access key anywhere.**
- **Report what happened, not what should have.** If a step fails, show its
  last lines of output and stop at that step.

## 1. Check the computer

Run these and note the answers. Stop and tell the person if a requirement is
missing - and offer the install command shown.

| What | Check with | Needed | If missing |
|---|---|---|---|
| Operating system | `uname -s` (macOS/Linux), `$PSVersionTable.OS` (Windows) | Windows 10+, macOS 12+, or a recent Linux | - |
| Python 3.10 or newer | Windows: `py -3 --version` · others: `python3 --version` | yes | Windows: `winget install Python.Python.3.12` · macOS: `brew install python@3.12` (a Mac's own `python3` is 3.9) · Linux: the package manager's `python3` |
| git | `git --version` | yes, to download and to update | Windows: `winget install Git.Git` · macOS: `brew install git` · Linux: `git` package |
| Node.js | `node --version` | to build the interface (not to run) | the installer offers to install it (step 3); or Windows: `winget install OpenJS.NodeJS.LTS` · macOS: `brew install node` · Linux: `nodejs npm` |
| uv (optional) | `uv --version` | no - makes setup faster | `pip install uv`, or see docs.astral.sh/uv |
| Free disk space | `df -h ~` / `Get-PSDrive C` | about 1 GB for Mindboard; up to about 12 GB more for the AI | - |
| Graphics card | Windows/Linux: `nvidia-smi` · macOS: Apple silicon or not | no - decides the AI model size (step 5) | - |

## 2. Get the code

Into a folder the person chooses (ask; default: their home folder):

```bash
git clone https://github.com/sinaKGT/Mindboard.git
cd Mindboard
```

`main` is the release branch. Keep the clone: Mindboard updates itself from it.

## 3. Install

The installer makes an environment inside the folder (`.venv`), installs the
Python packages into it, builds the interface, and sets up the data folder.
Nothing is installed system-wide except Node.js, and only when agreed.

Answer its questions in advance with environment variables, since you are not
at a keyboard:

- `MINDBOARD_NODE=yes|no` - install Node.js if it is missing (ask the person first)
- `MINDBOARD_SHORTCUT=yes|no` - macOS/Linux only: add a `mindboard` command to the shell profile (ask)

**Windows (PowerShell):**

```powershell
$env:MINDBOARD_NODE = 'yes'   # only if the person agreed
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**macOS / Linux:**

```bash
MINDBOARD_NODE=yes MINDBOARD_SHORTCUT=no ./install.sh
```

On Linux, installing Node.js runs `sudo <package manager>` and will ask for the
person's password - let them type it; never ask for it or enter it yourself.

It ends with `Done. Start it with: mindboard run`. Each line before it starts
with a step name (`python`, `environment`, `packages`, `interface`) - read them.

## 4. Check it works

Below, `MB` is `.venv\Scripts\mindboard.exe` on Windows and
`.venv/bin/mindboard` elsewhere.

```bash
MB version      # prints 0.1.0 or later
MB doctor       # python, storage, interface, port 7788 free, each board: "ok" (the port shows
                #   FAIL while Mindboard is already running - that is fine)
MB where        # the data folder - the person's boards live here, not in the code
```

`doctor` must report the interface as built. If it does not, see
Troubleshooting.

## 5. The AI (optional - ask first)

Without it, Mindboard works as a canvas; the chat and the experts need it.
Ask the person whether to set it up now. Then:

```bash
MB ai install          # finds the graphics card, says what it will download and how big, asks
```

It picks a model sized for the computer:

| Model | For | Choose with |
|---|---|---|
| Qwen3.5 9B | a graphics card with 12 GB | `--model full` |
| Qwen3.5 9B, compact | a graphics card with 8 GB | `--model compact` |
| Qwen3.5 4B | any computer, graphics card or not | `--model light` |

`--backend auto|cuda|vulkan|metal|cpu` overrides what it detected. Run it
without `--yes` and show the person its question; add `--yes` only after they
agreed to the size it states. It checks every file and tests the model. Then:

```bash
MB ai check            # loads the model, asks it a question, shows it a picture
MB ai status           # what is installed, and where
```

The experts' voice (speaking and listening, about 520 MB) is separate and
also optional - ask: `MB ai voice`.

## 6. Start it, and hand over

```bash
MB run                 # serves on http://127.0.0.1:7788 and opens the browser
MB run --no-open       # without opening a browser; --port to choose another port
```

Stop it with Ctrl+C. For everyday use, tell the person to double-click
`Mindboard.bat` (Windows) or `Mindboard.command` (macOS/Linux): it opens a
menu (open Mindboard, back up, set up the AI, update). Tell them where their
data folder is (`MB where`) and that `MB backup` puts everything into one zip.

## Updating later

```bash
MB update              # pulls from GitHub, reinstalls, rebuilds the interface
```

Close Mindboard first. It refuses if Mindboard's own files were changed and
names them; that is the person's work to keep (`git stash`), not yours to
discard.

## Troubleshooting

| What you see | What to do |
|---|---|
| `Python 3.10 or newer is required` | install it (step 1), open a new terminal, run the installer again |
| `The interface needs building, which needs Node.js` | install Node.js (ask), open a new terminal, run the installer again |
| `interface older than the code` | Node.js is missing; Mindboard still runs with the older interface - install Node.js and rerun to bring it up to date |
| `The interface did not build` | the lines shown say why; on macOS/Linux run `./build.sh` to see them all |
| `Mindboard installed but will not run` | delete the `.venv` folder and run the installer again |
| `Mindboard is still running from this folder` (Windows) | close the Mindboard window, or let the installer stop it - work is saved as it goes |
| Port 7788 busy | another Mindboard is running (`MB doctor` says); or `MB run --port 7790` |
| `MB update`: you have changed Mindboard's own files | stop and tell the person which files; do not discard them |
