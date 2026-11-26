# organize: a Claude Code skill that tidies your computer


Type `/organize` in [Claude Code](https://claude.com/claude-code) and it will:

- empty your **Desktop**, **Downloads** and **Trash / Recycle Bin**
- sort everything into **topic folders** in Documents (`Finance`, `Work`, `University`, `Projects`, `Media`…), named in **your computer's language** (`Finanza`, `Lavoro`… on an Italian system)
- **rename files with unclear names** (`IMG_1234.jpg`, `document (3).pdf`) with your scheme, e.g. `2026-11-03 - Electricity Bill.pdf`
- show your **biggest and long-forgotten files** and offer to archive them
- work out what to **keep and what to throw away**: identical duplicates, zips you already extracted, installers for apps you already have, Office lock files, half-finished downloads
- **ask yes/no before every action**, in batches, from the terminal
- leave your code projects working: before moving a project it checks for virtualenvs and scheduled jobs that would break
- let you **undo** every run
- finish with **advice** on staying organized on your OS

It works on **macOS, Windows and Linux**.

## First run: a quick onboarding

The first time you run `/organize`, before touching anything, Claude asks a few multiple-choice questions in the chat. This first onboarding is **required**: nothing is scanned or changed until it's saved.

```
? How do you want your Desktop?          › Empty · In sections · Only remove junk · Don't touch it
? How do you want your Downloads folder? › Empty · In sections · Only remove junk · Don't touch it
? How do you want your Documents folder? › Topic folders · Only remove junk · Don't touch it
? What should happen to the Trash?       › Offer to empty it · Never touch it
? Which sections do you want on the Desktop?  › Propose them · In progress / To read / Archive · I'll name them
? In which language should I name folders and files? › My computer's language · English · Another language  ? Group confirmations or file by file? …
? Should I rename files with unclear names?    › Keep their names · Date - Title · Title - Date · My own scheme
? Suggest archiving files you haven't opened in… › 6 months · 1 year · 2 years · Never
? Point out the files taking the most space?   › Over 100 MB · Over 500 MB · Over 1 GB · No
? Is there a folder I must never touch? …  ? Any rule of your own? …
```

It shows a recap, and on *Save* writes your answers to `~/.organize/config.json`.

After that it's **only on request**: say *"redo the organize onboarding"* to answer everything again, or *"redo just the folders part"* / *"change how files are renamed"* to answer only some questions; the rest of your settings stay as they are, and your current choices are marked *(current)*. When an update of the skill adds new questions, `/organize` just tells you new options exist; it asks them only when you request a new onboarding.

**Adding questions:** they live in [`skills/organize/onboarding.json`](skills/organize/onboarding.json). Each option says which config keys it sets (`"set": {"folders.desktop.policy": "tidy"}`) or which list receives what the user types (`"values_to": "rules"`), and a question can depend on earlier answers (`"when": {"desktop": "sections"}`). Mark new questions with `"since": <version>` and bump `version`: existing users are told about them and get them at their next requested onboarding. The tests check that every option produces a valid config.

## Make it yours: `~/.organize/config.json`

Out of the box `/organize` empties Desktop and Downloads, sorts everything into English topic folders in Documents and offers to empty the Trash. **Every one of those choices is a setting.** Put a `config.json` in `~/.organize/` (or point `ORGANIZE_CONFIG` at one) and keep only the keys you want to change:

```json
{
  "folders": { "desktop": "keep", "downloads": "tidy" },
  "trash": "never",
  "naming": { "language": "it", "case": "Title Case" },
  "keep": ["*.lnk", "Inbox"],
  "rules": ["Never touch files modified in the last 7 days."]
}
```

| Key | Default | What it does |
|---|---|---|
| `folders` | desktop `empty`, downloads `empty`, documents `tidy` | Policy per folder: `empty` (move everything out), `tidy` (sort into sub-folders in place), `keep` (only propose clear junk), `skip` (never touch). For `tidy` you can fix the sections: `{"policy": "tidy", "sections": ["In Progress", "Archive"]}` |
| `extra_folders` | `[]` | More folders to manage, e.g. `[{"path": "~/Pictures", "policy": "tidy"}]` |
| `destination` | `documents` | Where sorted items go |
| `protected` | `[]` | Paths never moved or trashed, nor anything inside them |
| `keep` | `[]` | Name patterns never moved or trashed |
| `trash` | `ask` | `ask` or `never` |
| `naming` | `system`, `Title Case` | Language of the names it creates — `system` follows each computer's display language (macOS `AppleLanguages`, Windows display language, Linux `LANGUAGE`/`LANG`), or a fixed code like `en` — and their case |
| `taxonomy` | `[]` | Your top-level topic folders (empty = Claude proposes them) |
| `max_depth` | `3` | Deepest folder level created |
| `batch_size` | `8` | Items per confirmation question (`1` = one by one) |
| `archive` | `large_mb` 500, `stale_days` 365, `top` 10 | Flag big files and files not opened for that long (`0` = off); Claude offers to archive them |
| `rename_files` | `pattern` `""`, `scope` `unclear` | Rename scheme with `{title}`, `{date}`, `{month}`, `{year}`, `{category}`, `{original}` (`""` = never rename); `scope` `unclear` (only `IMG_1234`-style names) or `all` (every moved file) |
| `advice` | `true` | End with OS tips |
| `rules` | `[]` | Free-text rules Claude follows on every run |

`folders`, `extra_folders`, `protected` and `keep` are **enforced by the script**, not just by Claude. A full example is in [`examples/config.example.json`](examples/config.example.json).

You don't have to edit JSON by hand: tell Claude *"configure organize"*, or just *"never touch my Desktop"*, and it asks a few questions, shows the change and saves it with `organizer.py config --write` (which validates the file and keeps a `.bak`).

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
- The script enforces the rules itself and doesn't trust the plan it is given. It refuses anything outside the managed folders (Desktop, Downloads, Documents unless skipped, plus your `extra_folders`), the folders themselves, your `protected` paths and `keep` patterns, hidden items (`.git`, `.ssh`…), cloud-sync roots (iCloud, OneDrive, Dropbox…), `~/.claude`, its own folder, and folders that hold installed skills. A confused or prompt-injected plan can't touch the rest of your disk.
- Symlinks are never followed. Trashing a link removes the link, never what it points to.

## Using the script directly

```bash
python3 skills/organize/scripts/organizer.py info                 # platform, managed folders, trash backend
python3 skills/organize/scripts/organizer.py config               # effective config (--init, --write FILE)
python3 skills/organize/scripts/organizer.py onboarding           # onboarding questions (--apply answers.json [--dry-run])
python3 skills/organize/scripts/organizer.py scan                 # read-only inventory as JSON
python3 skills/organize/scripts/organizer.py report               # biggest and long-unused files (read-only)
python3 skills/organize/scripts/organizer.py render-names items.json --pattern "{date} - {title}"   # preview new names
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
