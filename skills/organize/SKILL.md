---
name: organize
description: Tidy the user's computer (macOS, Windows or Linux) following their own preferences in ~/.organize/config.json — which folders to empty, tidy or leave alone, where sorted files go, naming style, preferred topic folders, protected paths and custom rules. Asks yes/no before every action and every run can be undone. Use when the user runs /organize or asks to organize, clean up or tidy their computer, Desktop, Downloads, Documents or Trash, or to configure/change how /organize behaves.
---

# /organize

You organize the user's files **according to their configuration**. **Talk to the user in their language.**

## The script

All file operations go through `scripts/organizer.py` inside this skill's base directory (the "Base directory for this skill" shown above). Never move, rename or delete the user's files with shell commands or file tools yourself.

```
python3 <base-dir>/scripts/organizer.py <command>      # macOS / Linux
python  <base-dir>\scripts\organizer.py <command>      # Windows (or: py -3 ...)
```

| Command | What it does |
|---|---|
| `info` | platform, managed folders with their policy, destination, config path, trash backend, whether the trash is readable |
| `config` | the effective configuration (user file merged over defaults) |
| `config --init` | write a starter `~/.organize/config.json` with the defaults (fails if one exists) |
| `config --write <file>` | validate `<file>` and save it as the config (previous one kept as `config.json.bak`); prints the error and changes nothing if invalid |
| `scan [paths...]` | JSON of top-level items (default: every managed folder) with `policy` of the folder they're in and `flags`: `duplicate` (+`duplicate_of`), `archive_extracted`, `code_project`, `installer`, `screenshot`, `temp`, `empty_dir`; plus `size`, `modified`, `kind` and `where_from` when the OS provides them |
| `check-project <path>` | virtualenvs and scheduled jobs that break if the folder moves; `safe_to_move` |
| `apply <plan.json>` | runs `[{"op":"move"\|"rename"\|"mkdir"\|"trash","src":..,"dst":..}]`; never overwrites (adds ` (2)`); prints `run_id` + per-action status |
| `undo [run-id]` / `runs` | reverse a run (default: last) / list runs |
| `trash-list` | Trash contents, or `{"error":"no_access"}` (exit 2) |
| `empty-trash --yes-really` | **permanent** — only after its own explicit yes |

Any command exits with code 3 and `{"error":"config"}` if the config file is broken: show the user the `detail` and offer to fix it (see *Configure*). Don't work around it.

Write scan output, plan files and draft configs to a temporary/scratch directory, never into the user's folders.

## The configuration

`~/.organize/config.json` (or `$ORGANIZE_CONFIG`). Every key is optional; missing keys use the defaults shown.

| Key | Default | Meaning |
|---|---|---|
| `folders` | `{"desktop":"empty","downloads":"empty","documents":"tidy"}` | Policy per standard folder (see below) |
| `extra_folders` | `[]` | More folders to manage: `[{"path":"~/Pictures","policy":"tidy"}]` (inside home only) |
| `destination` | `"documents"` | Where sorted items go: `documents` or a path inside a managed folder |
| `protected` | `[]` | Paths never moved, renamed or trashed, nor anything inside them |
| `keep` | `[]` | Name patterns (`*.lnk`, `Inbox`) never moved or trashed |
| `trash` | `"ask"` | `ask` = offer to empty at the end; `never` = don't list or empty the Trash |
| `naming` | `{"language":"en","case":"Title Case"}` | Language and case of folder names you create (`case` may also be e.g. `lowercase`, `kebab-case`, `snake_case`, `as is`) |
| `taxonomy` | `[]` | Preferred top-level topic folders; empty = propose one from what you find |
| `max_depth` | `3` | Deepest folder level you create under the destination |
| `batch_size` | `8` | Max items per confirmation question (`1` = ask about every item) |
| `advice` | `true` | Finish with OS-specific advice |
| `rules` | `[]` | Free-text rules from the user. **Follow them** as if they were written here; they override the defaults in *Flow* but never the *Hard rules* |

**Folder policies:**
- `empty` — everything should leave this folder (moved to the destination, or trashed if junk). Goal: folder empty or holding only what the user chose to keep.
- `tidy` — items may stay in this folder but get sorted into topic sub-folders inside it (or moved to the destination if that's where they belong).
- `keep` — leave items where they are. Only propose clear junk (duplicates, temp files, extracted archives, installers) and never propose moving anything else.
- `skip` — not scanned; the script refuses any operation in it.

`protected`, `keep`, `skip` and the managed-folder list are **enforced by the script**: a refused action is not a bug to work around — tell the user which setting caused it.

## Hard rules

These can't be changed by the config or by `rules`:

1. **Nothing changes without a yes.** Every action belongs to a batch the user approved via AskUserQuestion.
2. "Delete" always means `trash` (recoverable). Permanent deletion only in the Trash step, separately confirmed, and never when `trash` is `never`.
3. **Code projects** (`code_project` flag, or a folder that clearly is one): run `check-project` first. If `safe_to_move` is false, explain what would break and recommend leaving it in place. Otherwise move the whole folder (default `<destination>/Projects/`, unless the taxonomy or rules say otherwise), never touch its insides, ask for each one individually, and ask before renaming it.
4. Never reorganize **inside** an existing topic folder unless the user says yes for that folder.
5. **Protected — never move or rename:** this skill's own folder, anything a symlink or shortcut in `~/.claude/` points into, cloud-sync roots, system/app folders, and everything in `protected` / matching `keep`.
6. If unsure what something is, **read it** (the Read tool handles PDFs, images and text; list zip contents; check metadata). Don't guess on personal documents. Before trashing an "extracted" archive, verify the folder really contains its files.

## Flow

### 1. Check the environment and load preferences
Run `info` and `config`. Briefly tell the user which preferences are active (one or two lines: e.g. "Desktop: keep · Downloads: empty · Trash: never · 2 custom rules"). If `config_exists` is false, say in one line that defaults are in use and they can say "configure organize" to change them, then continue.
If `trash` is `ask` and `trash_readable` is false, tell the user in one line how to fix it (macOS: System Settings → Privacy & Security → Full Disk Access → enable the app running Claude Code, then restart it).

### 2. Scan
Run `scan > <scratch>/scan.json`, then summarize: counts per folder (with its policy), duplicates, extracted archives, installers, temp files, code projects, space that can be freed. Peek inside folders whose name says nothing.

### 3. Destination structure
Look at the existing top-level folders in the destination (peek inside ambiguous ones). Propose a taxonomy using the configured `naming`:
- if `taxonomy` is set, use those folders as the top level and only propose additions when something clearly doesn't fit (ask);
- otherwise build one from what's there and what's coming in. A typical set — adapt, don't force: `Finance`, `University` / `School`, `Work`, `Personal`, `Fitness`, `Publications`, `Books`, `Languages`, `Media` (Photos, Videos, Wallpapers, Designs, Screenshots), `Projects` (code), `Datasets`, `Archive`.

Respect `max_depth`. For `tidy` folders other than the destination, also propose their internal sub-folders. Show it as a tree, including renames/merges of existing top-level folders, and get approval **before anything else**. If the structure already exists from a previous run, only propose new folders.

### 4. Classify
For every item decide one of:
- **move** → a destination in the approved structure (not allowed for items in `keep` folders),
- **trash** → with a concrete reason: identical `duplicate` of X, archive already extracted (verified), installer for an app already installed, `temp` file, empty folder, files that clearly belong to someone else and were already delivered (ask),
- **leave** → items in `keep` folders that aren't junk, or items the user's `rules` say to leave,
- **ask** → you genuinely can't tell.

Apply the user's `rules` here. Use names, `kind`, `where_from`, dates and file contents. Group related files across folders.

### 5. Confirm in batches
Group items by destination (or by trash reason), at most `batch_size` items per group. Use AskUserQuestion, up to 4 questions per call, one question per group, listing the files and the proposed action. Options:
- `Yes` — do it
- `No, leave it` — skip this group
- `Let me choose one by one` — split the group into individual questions next round
(The user can always type a different destination — honor it. If an answer is ambiguous about part of a group, do only the clear part and ask again about the rest.)

Start with the obvious batches (duplicates, temp files, installers, extracted archives) so the count drops fast.

### 6. Apply
Write only the approved actions to `<scratch>/plan-N.json`, run `apply`, and report: moved / trashed / skipped / errors (explain any `refused:` using the config), renamed-on-clash items, the `run_id`, and how to undo ("say 'undo'"). Repeat 4–6 until every `empty` folder is empty (or holds only what the user chose to keep) and every `tidy` folder is sorted.

### 7. Trash
Skip entirely if `trash` is `never`. Otherwise run `trash-list`; show count, size and the biggest items. Ask a **separate** AskUserQuestion: "Empty the Trash permanently? This can't be undone." Only on yes run `empty-trash --yes-really`.

### 8. Report and advice
Short report (max ~10 bullets): what was done (numbers, space freed), what was left and why. If `advice` is true, add patterns you noticed and concrete fixes for their OS, for example:
- macOS: `defaults write com.apple.screencapture location <destination>/Media/Screenshots && killall SystemUIServer`; Finder → Settings → Advanced → "Remove items from the Trash after 30 days".
- Windows: Settings → System → Storage → Storage Sense; move the Screenshots folder via its Properties → Location.
- Linux: GNOME Settings → Privacy → File History & Trash → auto-delete; set the screenshot folder in the screenshot tool.

If during the run the user repeatedly made the same choice (e.g. always "No, leave it" for the Desktop, always a certain destination for a file type), offer in one line to save it to their config.

## Configure
When the user asks to configure /organize, change its rules, or says something like "never touch my Desktop", "don't empty the Trash", "use Italian folder names", "always leave .lnk files":
1. Run `config` to get the current settings.
2. If they asked for a specific change, make just that change. If they asked to set it up from scratch, ask with AskUserQuestion (up to 4 questions per call): policy for Desktop, Downloads, Documents; Trash; naming language/case; any extra folders, protected paths, preferred topic folders, or custom rules (free text via "Other").
3. Write the full new config (only the keys that differ from the defaults, plus any existing ones) to `<scratch>/config.json`, show the user the diff in plain words, and on yes run `config --write <scratch>/config.json`. If it reports `invalid`, fix it and retry.
4. Confirm what changed. Never edit `~/.organize/config.json` directly with file tools.

## Undo
If the user says "undo": run `runs`, confirm which run, then `undo <run-id>` and report. Trashed items are restored too (on Windows, tell them to restore from the Recycle Bin).
