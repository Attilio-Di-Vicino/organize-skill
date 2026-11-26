---
name: organize
description: Tidy the user's computer (macOS, Windows or Linux) following their own preferences, collected in a short onboarding on first use and stored in ~/.organize/config.json — which folders to empty, tidy or leave alone, where sorted files go, naming style, preferred topic folders, protected paths and custom rules. Asks yes/no before every action and every run can be undone. Use when the user runs /organize or asks to organize, clean up or tidy their computer, Desktop, Downloads, Documents or Trash, or to configure/change how /organize behaves or redo its onboarding/setup.
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
| `onboarding` | the onboarding questions (from `onboarding.json`) and the current config |
| `onboarding --apply <answers.json> [--dry-run]` | turn onboarding answers into the config (merged over the existing one, `.bak` kept); `--dry-run` only prints the result |
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
| `folders` | `{"desktop":"empty","downloads":"empty","documents":"tidy"}` | Policy per standard folder (see below). A value can be `{"policy":"tidy","sections":["In Progress","Archive"]}` |
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
| `onboarding_version` | `0` | Version of `onboarding.json` last answered; set by the onboarding |
| `rules` | `[]` | Free-text rules from the user. **Follow them** as if they were written here; they override the defaults in *Flow* but never the *Hard rules* |

**Folder policies:**
- `empty` — everything should leave this folder (moved to the destination, or trashed if junk). Goal: folder empty or holding only what the user chose to keep.
- `tidy` — items may stay in this folder but get sorted into sub-folders inside it (or moved to the destination if that's where they belong). If the folder has `sections`, those are its sub-folders: sort into them (create the missing ones after asking) and don't invent others without asking; with no sections, propose them.
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

## Onboarding

Runs **before the first organization** (when `info` says `onboarding_needed: true`), or whenever the user asks to set up / redo the onboarding ("/organize setup", "rifai l'onboarding", "change my preferences from scratch").

1. Say in one line that you'll ask a few quick questions so /organize works the way they like, and that nothing on their computer changes during the onboarding.
2. Run `onboarding`. It returns `rounds`, each with up to 4 `questions`. For each round, in order:
   - keep only questions whose `when` matches earlier answers (a list in `when` means any of those answers); skip the round if none is left;
   - ask them in **one** AskUserQuestion call: `header` → header, `question` → question, each option's `label`/`description` → option label/description. **Translate everything into the user's language.** If the user already has a config (re-run), put the option matching their current setting first and add " (current)" to its label;
   - map each reply to the option `id`. If the user typed their own answer ("Other"), pick the option it means; if it's a list of names or paths, use the option with `values_to` and pass the list. If it means something no option covers, keep it to add as a free-text rule.
   - For options with `values_to` (e.g. "I'll name them") the user may already have typed the values in "Other"; if not, ask them in plain text right after the round.
3. Write `<scratch>/answers.json`:
   `{"desktop": "sections", "desktop_sections": {"option": "custom", "values": ["Work", "To Do"]}, "trash": "never", ..., "_user_language": "<ISO code of the language you're speaking>"}`.
   Answers that only fit as free text go into the `rules` question: `"rules": {"option": "yes", "values": [...]}`.
4. Run `onboarding --apply <scratch>/answers.json --dry-run`. If it reports `invalid`, explain the problem in plain words and ask the one question needed to fix it — e.g. a folder set to *Empty* while *Documents* is *Don't touch* leaves nowhere to put the files: offer "Documents: only remove junk" (files can still arrive there) or changing the other folder — then retry.
5. Show a short recap in the user's language (one line per folder, Trash, names, confirmations, protected paths, rules) and ask with AskUserQuestion: `Save` / `Change something`. On *Save* run `onboarding --apply <scratch>/answers.json` (no `--dry-run`). On *Change something*, ask only the questions they want to change and repeat from step 4.
6. Confirm it's saved, mention they can say "configure organize" for single changes or redo the onboarding any time, then continue with the Flow (unless they only asked for setup).

If `onboarding_needed` is true but `config_exists` is also true, the onboarding file got new questions since their last run: ask only the questions whose `id` isn't reflected in their config (in doubt, ask the whole onboarding and mark current values).

To add questions, edit `onboarding.json` (its `_comment` explains the format) and bump its `version`: users get asked again at their next run.

## Flow

### 1. Check the environment and load preferences
Run `info`. If `onboarding_needed` is true, do the **Onboarding** first. Then run `config` and briefly tell the user which preferences are active (one or two lines: e.g. "Desktop: sections (Work, To Do) · Downloads: empty · Trash: never · 2 custom rules").
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
For a full re-setup, run the **Onboarding** again. For single changes — when the user asks to configure /organize, change its rules, or says something like "never touch my Desktop", "don't empty the Trash", "use Italian folder names", "always leave .lnk files":
1. Run `config` to get the current settings.
2. Make just the change they asked for (ask a clarifying question only if it's ambiguous).
3. Write the full new config (only the keys that differ from the defaults, plus any existing ones) to `<scratch>/config.json`, show the user the diff in plain words, and on yes run `config --write <scratch>/config.json`. If it reports `invalid`, fix it and retry.
4. Confirm what changed. Never edit `~/.organize/config.json` directly with file tools.

## Undo
If the user says "undo": run `runs`, confirm which run, then `undo <run-id>` and report. Trashed items are restored too (on Windows, tell them to restore from the Recycle Bin).
