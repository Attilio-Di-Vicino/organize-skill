# organize: a Claude Code skill that tidies your computer


Type `/organize` in [Claude Code](https://claude.com/claude-code) and it will:

- empty your **Desktop**, **Downloads** and **Trash / Recycle Bin**
- sort everything into **topic folders** in Documents (`Finance`, `Work`, `University`, `Projects`, `Media`…) with English, Title Case names
- work out what to **keep and what to throw away**: identical duplicates, zips you already extracted, installers for apps you already have, Office lock files, half-finished downloads
- **ask yes/no before every action**, in batches, from the terminal
- leave your code projects working: before moving a project it checks for virtualenvs and scheduled jobs that would break
- let you **undo** every run
- finish with **advice** on staying organized on your OS

It works on **macOS, Windows and Linux**.

## How it works

The skill has two parts:

| Part | Job |
|---|---|
| `skills/organize/SKILL.md` | Tells Claude how to scan, classify (it reads PDFs and images when the name isn't enough), group, ask and advise |
| `skills/organize/scripts/organizer.py` | A small Python script (standard library only) that does **all** file operations. It never overwrites a file, keeps an undo journal for every run, and only deletes anything permanently through `empty-trash --yes-really` |

Claude decides what goes where. The script is the only thing that touches your files, and it only does what you approved.

```
$ /organize
Found 285 items: Desktop 17, Downloads 246, Documents 22
14 identical duplicates · 7 extracted zips · 2 installers · 8 code projects

? Move these 9 identical copies to the Trash? (the other copy is kept)
  Thesis_v2.pdf, ResidenceCertificate (1).pdf, decoder.png, … +6 more
  › Yes   No, leave it   Let me choose one by one

Run 20261125-203926: 26 trashed, 13 moved, 0 errors. Say "undo" to revert.
```

### Platform support

| | macOS | Linux | Windows |
|---|---|---|---|
| User folders | `~/Desktop`… | XDG user dirs (`~/.config/user-dirs.dirs`, so localized names such as `Scrivania` work) | Registry *User Shell Folders* (OneDrive-redirected folders work) |
| Trash | `~/.Trash` | freedesktop.org Trash with `.trashinfo`, so "Restore" in your file manager works | Recycle Bin, via PowerShell |
| Undo of trashed items | ✅ | ✅ | restore from the Recycle Bin |
| Download source (`where_from`) | Spotlight metadata | – | `Zone.Identifier` |
| Scheduled-job check | launchd, cron | systemd user units, cron | Task Scheduler |

## Install

You need Claude Code and Python 3.8+ (`python3`, or `python` / `py` on Windows).

**As a plugin (recommended):** run these inside Claude Code:

```
/plugin marketplace add Attilio-Di-Vicino/organize-skill
/plugin install organize@organize-skill
```

**Or as a personal skill:**

```bash
# macOS / Linux
git clone https://github.com/Attilio-Di-Vicino/organize-skill.git
mkdir -p ~/.claude/skills
cp -R organize-skill/skills/organize ~/.claude/skills/organize
```

```powershell
# Windows (PowerShell)
git clone https://github.com/Attilio-Di-Vicino/organize-skill.git
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
Copy-Item -Recurse organize-skill\skills\organize "$HOME\.claude\skills\organize"
```

Then type `/organize` in Claude Code, or just ask it to "tidy up my Downloads".

### macOS: let it see the Trash

macOS hides the Trash from apps that don't have **Full Disk Access**. Without it, `/organize` still works, but it can't list the Trash before emptying it. To turn it on, go to System Settings → Privacy & Security → Full Disk Access, enable your terminal (or VS Code) and restart it.

## Safety

- Nothing moves until you answer **Yes** to the batch it belongs to.
- "Delete" always means *move to the Trash*. Emptying the Trash is a separate step with its own confirmation.
- Name clashes never overwrite: the incoming file becomes `name (2).ext`.
- Every run is written to `~/.organize/journal/<run-id>.jsonl` before it's reported. `undo` replays it backwards.
- Code projects are moved as a whole or not at all. Projects with virtualenvs or scheduled jobs that would break are flagged, and Claude recommends leaving them in place.
- The script enforces the rules itself and doesn't trust the plan it is given. It refuses anything outside Desktop, Downloads and Documents, the folders themselves, hidden items (`.git`, `.ssh`…), cloud-sync roots (iCloud, OneDrive, Dropbox…), `~/.claude`, its own folder, and folders that hold installed skills. A confused or prompt-injected plan can't touch the rest of your disk.
- Symlinks are never followed. Trashing a link removes the link, never what it points to.

## Using the script directly

```bash
python3 skills/organize/scripts/organizer.py info                 # platform, folders, trash backend
python3 skills/organize/scripts/organizer.py scan                 # read-only inventory as JSON
python3 skills/organize/scripts/organizer.py check-project ~/Documents/my-app
python3 skills/organize/scripts/organizer.py apply plan.json      # [{"op": "move", "src": "...", "dst": "..."}, {"op": "trash", "src": "..."}]
python3 skills/organize/scripts/organizer.py runs
python3 skills/organize/scripts/organizer.py undo [run-id]
python3 skills/organize/scripts/organizer.py trash-list
python3 skills/organize/scripts/organizer.py empty-trash --yes-really
```

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests run against a temporary home folder (`ORGANIZE_HOME`) with a forced platform (`ORGANIZE_PLATFORM=macos|linux|windows`), so the whole suite runs on any OS. Two extra tests use the real Linux Trash and Windows Recycle Bin when run with `CI=1` on those systems.

## License

MIT
