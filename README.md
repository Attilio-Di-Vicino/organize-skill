# organize: a Claude Code skill that tidies your computer

Type `/organize` in [Claude Code](https://claude.com/claude-code) and it tidies your computer **the way you want it**:

- **asks how you like things** the first time: Desktop empty or in sections, Downloads emptied or sorted, Documents in topic folders, Trash emptied or left alone, and more
- sorts files into **topic folders** (`Finance`, `Work`, `University`, `Projects`, `Media`…), named in **your computer's language** (`Finanza`, `Lavoro`… on an Italian system)
- **renames files with unclear names** (`IMG_1234.jpg`, `document (3).pdf`) with your scheme, e.g. `2026-11-03 - Electricity Bill.pdf`
- shows your **biggest and long-forgotten files** and offers to archive them
- **frees disk space outside your folders**: unused Docker images and build cache, npm / pip / Homebrew caches, app caches, leftovers of apps you uninstalled
- works out what to **keep and what to throw away**: identical duplicates, zips you already extracted, installers for apps you already have, Office lock files, half-finished downloads
- **asks which folders to work on** every time you run it: Desktop, Downloads, Documents, Trash, or all of them
- **asks yes/no before every action**, in batches, from the terminal
- leaves your code projects working: before moving a project it checks for virtualenvs and scheduled jobs that would break
- lets you **undo** every run
- finishes with **advice** on staying organized on your OS

It works on **macOS, Windows and Linux**.

```
$ /organize
Desktop: sections (Work, To Do) · Downloads: empty · Trash: never · names in Italian

Found 285 items: Desktop 17, Downloads 246, Documents 22
14 identical duplicates · 7 extracted zips · 2 installers · 8 code projects · 31 unclear names

? Move these 6 invoices to Documents/Finanza/Bollette and rename them?
  scan0001.pdf → 2026-10-02 - Bolletta Enel.pdf
  document (3).pdf → 2026-11-03 - Bolletta Gas.pdf   … +4 more
  › Yes   Move, keep the old names   No, leave it   Let me choose one by one

? Clean Docker? Unused images 6.1 GB, build cache 2 GB (permanent, re-downloaded when needed)
  › Yes   Only the build cache   No

Run 20261125-203926: 26 trashed, 13 moved, 9 renamed, 8.1 GB freed. Say "undo" to revert.
```

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

Then type `/organize` in Claude Code, or just ask it to "tidy up my Downloads", "clean up Docker" or "show me what's eating my disk".

### macOS: let it see the Trash

macOS hides the Trash from apps that don't have **Full Disk Access**. Without it, `/organize` still works, but it can't list the Trash before emptying it. To turn it on, go to System Settings → Privacy & Security → Full Disk Access, enable your terminal (or VS Code) and restart it.

## First run: a quick onboarding

The first time you run `/organize`, Claude asks a few multiple-choice questions in the chat. This first onboarding is **required**: nothing is scanned or changed until your answers are saved.

```
? How do you want your Desktop?          › Empty · In sections · Only remove junk · Don't touch it
? How do you want your Downloads folder? › Empty · In sections · Only remove junk · Don't touch it
? How do you want your Documents folder? › Topic folders · Only remove junk · Don't touch it
? What should happen to the Trash?       › Offer to empty it · Never touch it
? Which sections do you want on the Desktop?    › Propose them · In progress / To read / Archive · I'll name them
? In which language should I name folders and files? › My computer's language · English · Another language
? How should folder names be written?          › Title Case · lowercase · kebab-case
? How should I ask before acting?              › In groups · File by file
? Should I rename files with unclear names?    › Keep their names · Date - Title · Title - Date · My own scheme
? Suggest archiving files you haven't opened in… › 6 months · 1 year · 2 years · Never
? Point out the files taking the most space?   › Over 100 MB · Over 500 MB · Over 1 GB · No
? Also look for clutter outside your folders?  › Yes, everything · Only Docker and caches · No
? Is there a folder I must never touch? …   ? Any rule of your own? …
```

Follow-up questions only appear when they apply (sections only if you chose *In sections*). Claude shows a recap and, on *Save*, writes `~/.organize/config.json`.

After that, the onboarding runs **only when you ask**:

- *"redo the organize onboarding"*: answer everything again, with your current choices marked *(current)*;
- *"redo just the folders part"*, *"change how files are renamed"*: answer only those questions; everything else stays as it is.

When an update of the skill adds new questions, `/organize` tells you in one line that new options exist and asks them only when you request a new onboarding.

## What it does

### Folders, the way you want them

Each folder gets a **policy**:

| Policy | Meaning |
|---|---|
| `empty` | Everything leaves the folder: sorted into the destination (Documents by default) or trashed if it's junk |
| `tidy` | Files stay, sorted into sub-folders: your own **sections** (`In corso`, `Da leggere`…) or ones Claude proposes |
| `keep` | Files stay where they are; only clear junk is proposed (duplicates, temp files, extracted zips, installers) |
| `skip` | Never scanned, and the script refuses any change inside it |

You can add folders beyond Desktop, Downloads and Documents (e.g. `~/Pictures`), protect paths and name patterns, and write rules in plain words ("never touch files from the last 7 days").

### Names in your computer's language

Every folder Claude creates and every file it renames is named in **your computer's display language**, detected on each machine (macOS language preferences, Windows display language, Linux `LANGUAGE` / `LANG`), whatever language you chat in. The same skill creates `Lavoro/` on an Italian Mac and `Work/` on an English one. You can also fix a language (`en`, `it`, `pt-BR`…). Existing folders are never translated unless you ask.

### Renaming unclear file names

Files like `IMG_1234.jpg`, `scan0001.pdf`, `document (3).pdf`, `WhatsApp Image 2024-…` are flagged as unclear. If you choose a scheme, Claude reads each file to work out a title and its real date (the invoice date, not the download date) and proposes `old name → new name` in the same question as the move. Schemes use `{title}`, `{date}`, `{month}`, `{year}`, `{category}` and `{original}`; describe your own in words during the onboarding. You can also rename every file it moves, not only unclear ones. The script removes characters that aren't allowed in file names, never overwrites an existing file, and every rename can be undone.

### Big and forgotten files

After sorting, Claude shows your **biggest files** and the **biggest ones you haven't opened in a long time**, including files inside sub-folders (a code project counts as one item). For each group you choose: archive it (`Documents/Archive/…`, in your language), trash it (only for clear junk), or leave it. The last-opened date comes from Spotlight on macOS; on Windows and Linux it's estimated from file times, and the estimate leans towards "recently used".

### System cleanup

If you enable it, `/organize` also looks **outside your folders**:

| Category | What | How it's cleaned |
|---|---|---|
| `docker` | untagged images, images no container uses, stopped containers, build cache, unused volumes | Docker's own `prune` / `rm` commands. **Permanent**; volumes are asked one at a time |
| `tool` | npm, pip, Yarn, Go and Homebrew caches | the tool's own command (`npm cache clean --force`, `pip cache purge`…). Permanent, re-downloaded when needed |
| `cache` | app caches (`~/Library/Caches`, `~/.cache`, Windows `Temp` older than a week), Xcode DerivedData | moved to the Trash (undoable) |
| `leftover` | data of apps that are no longer installed (`Application Support`, `~/.config`, `~/.local/share`, `AppData`) | moved to the Trash, asked one by one: the "uninstalled" check is a best guess, and Claude looks inside before proposing |
| `empty` | empty app-data folders | moved to the Trash |

The script itself decides what counts as cleanable. Claude can only pass item ids from a fresh scan, never a path or a command, so nothing outside these locations can be removed, even by a confused plan. SSH keys, GPG, keychains, cloud folders, `~/.claude` and your protected paths are always off-limits.

## Configuration

Your answers live in `~/.organize/config.json` (or wherever `ORGANIZE_CONFIG` points). You can also edit it by hand, or just tell Claude *"never touch my Desktop"* / *"don't empty the Trash"*: it shows the change and saves it with `organizer.py config --write` (which validates the file and keeps a `.bak`).

```json
{
  "folders": {
    "desktop": { "policy": "tidy", "sections": ["In corso", "Da leggere", "Archivio"] },
    "downloads": "empty"
  },
  "trash": "never",
  "naming": { "language": "system", "case": "Title Case" },
  "rename_files": { "pattern": "{date} - {title}", "scope": "unclear" },
  "archive": { "large_mb": 500, "stale_days": 365 },
  "cleanup": { "categories": ["docker", "tool", "cache", "empty"] },
  "keep": ["*.lnk", "Inbox"],
  "rules": ["Never touch files modified in the last 7 days."]
}
```

| Key | Default | What it does |
|---|---|---|
| `folders` | desktop `empty`, downloads `empty`, documents `tidy` | Policy per folder (see above); `tidy` folders can list their `sections` |
| `extra_folders` | `[]` | More folders to manage, e.g. `[{"path": "~/Pictures", "policy": "tidy"}]` |
| `destination` | `documents` | Where sorted items go |
| `protected` | `[]` | Paths never moved, renamed, trashed or cleaned, nor anything inside them |
| `keep` | `[]` | Name patterns never moved or trashed |
| `trash` | `ask` | `ask` (offer to empty it at the end) or `never` |
| `naming` | `system`, `Title Case` | Language of the names it creates (`system` = this computer's language, or a code like `en`) and their case |
| `taxonomy` | `[]` | Your top-level topic folders (empty = Claude proposes them) |
| `max_depth` | `3` | Deepest folder level created |
| `batch_size` | `8` | Items per confirmation question (`1` = one by one) |
| `rename_files` | pattern `""`, scope `unclear` | Rename scheme (`""` = never rename); `scope` `unclear` or `all` (every moved file) |
| `archive` | `large_mb` 500, `stale_days` 365, `top` 10 | Big / unused file thresholds (`0` = off) and how many to list |
| `cleanup` | categories `[]`, `min_size_mb` 50, `leftover_min_age_days` 180 | System cleanup categories (`[]` = off), smallest item worth listing, how long leftovers must be unused |
| `advice` | `true` | End with OS tips |
| `rules` | `[]` | Free-text rules Claude follows on every run |

A full example is in [`examples/config.example.json`](examples/config.example.json). `folders`, `extra_folders`, `protected`, `keep` and the cleanup locations are **enforced by the script**, not just by Claude.

## How it works

| Part | Job |
|---|---|
| `skills/organize/SKILL.md` | Tells Claude how to run the onboarding, scan, classify (it reads PDFs and images when the name isn't enough), name, group, ask, clean and advise |
| `skills/organize/onboarding.json` | The onboarding questions and which settings each answer sets |
| `skills/organize/scripts/organizer.py` | A Python script (standard library only) that does **all** file operations and enforces the safety rules |

Claude decides what goes where. The script is the only thing that touches your files, and it only does what you approved.

### Platform support

| | macOS | Linux | Windows |
|---|---|---|---|
| User folders | `~/Desktop`… | XDG user dirs (localized names such as `Scrivania` work) | Registry *User Shell Folders* (OneDrive-redirected folders work) |
| System language | `AppleLanguages` | `LANGUAGE` / `LC_ALL` / `LC_MESSAGES` / `LANG` | Windows display language |
| Trash | `~/.Trash` | freedesktop.org Trash with `.trashinfo`, so "Restore" in your file manager works | Recycle Bin, via PowerShell |
| Undo of trashed items | ✅ | ✅ | restore from the Recycle Bin |
| Last opened | Spotlight | file times | file times |
| Download source (`where_from`) | Spotlight metadata | – | `Zone.Identifier` |
| Installed apps (for leftovers) | `/Applications` bundles | `.desktop` files, Flatpak, `PATH` | Uninstall registry, Program Files |
| Scheduled-job check | launchd, cron | systemd user units, cron | Task Scheduler |

## Safety

- Nothing changes until you answer **Yes** to the batch it belongs to.
- "Delete" means *move to the Trash*. Emptying the Trash is a separate step with its own confirmation. The only permanent operations are emptying the Trash and the Docker/package-manager cleanup commands, which say so in the question.
- Name clashes never overwrite: the incoming file becomes `name (2).ext`.
- Every run is written to `~/.organize/journal/<run-id>.jsonl` before it's reported. `undo` replays it backwards.
- Code projects are moved as a whole or not at all. Projects with virtualenvs or scheduled jobs that would break are flagged, and Claude recommends leaving them in place.
- The script enforces the rules itself and doesn't trust the plan it is given. File operations are refused outside the managed folders (Desktop, Downloads, Documents unless skipped, plus your `extra_folders`), on the folders themselves, on your `protected` paths and `keep` patterns, hidden items (`.git`, `.ssh`…), cloud-sync roots (iCloud, OneDrive, Dropbox…), `~/.claude`, its own folder, and folders that hold installed skills. Cleanup only acts on items its own fresh scan found. A confused or prompt-injected plan can't touch the rest of your disk.
- Symlinks are never followed. Trashing a link removes the link, never what it points to.

## Using the script directly

```bash
S=skills/organize/scripts/organizer.py
python3 $S info                        # platform, language, managed folders, onboarding status, trash backend
python3 $S onboarding                  # onboarding questions (--apply answers.json [--dry-run])
python3 $S config                      # effective config (--init, --write FILE)
python3 $S scan                        # read-only inventory as JSON
python3 $S report                      # biggest and long-unused files (read-only)
python3 $S render-names items.json --pattern "{date} - {title}"   # preview new names
python3 $S cleanup-scan --categories docker,tool                  # reclaimable space (read-only)
python3 $S cleanup-apply ids.json      # clean items from a fresh scan, by id
python3 $S check-project ~/Documents/my-app
python3 $S apply plan.json             # [{"op": "move", "src": "...", "dst": "..."}, {"op": "trash", "src": "..."}]
python3 $S runs
python3 $S undo [run-id]
python3 $S trash-list
python3 $S empty-trash --yes-really
```

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests run against a temporary home folder (`ORGANIZE_HOME`) with a forced platform (`ORGANIZE_PLATFORM=macos|linux|windows`) and, where needed, a forced system language (`ORGANIZE_SYSTEM_LANG`), so the whole suite runs on any OS. Docker and package managers are mocked. Two extra tests use the real Linux Trash and Windows Recycle Bin when run with `CI=1` on those systems.

**Adding onboarding questions:** they live in [`skills/organize/onboarding.json`](skills/organize/onboarding.json). Each option says which settings it sets (`"set": {"folders.desktop.policy": "tidy"}`) or where the user's typed answer goes (`"values_to"` for a list, `"text_to"` for a string), and a question can depend on other answers (`"when": {"desktop": "sections"}`). Mark new questions with `"since": <version>` and bump `version`. The tests check that every option produces a valid config.

## License

MIT
