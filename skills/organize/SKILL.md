---
name: organize
description: Tidy the user's computer (macOS, Windows or Linux) following their own preferences, collected in a short onboarding on first use and stored in ~/.organize/config.json — which folders to empty, tidy or leave alone, where sorted files go, naming style, preferred topic folders, file renaming scheme, big/old-file archiving, system cleanup (Docker, caches, leftovers of uninstalled apps), protected paths and custom rules. Asks yes/no before every action and every run can be undone. Use when the user runs /organize or asks to organize, clean up or tidy their computer, Desktop, Downloads, Documents or Trash, or to free disk space / clean Docker, caches or app leftovers, or to configure/change how /organize behaves or redo its onboarding/setup.
---

# /organize

You organize the user's files **according to their configuration**. **Talk to the user in the language they write in.**

**Names follow the computer, not the conversation.** Every folder you create and every file you rename is named in `naming_language` (from `info` / `config`). By default that's the computer's own display language (`naming.language: "system"`, detected on each machine): on an Italian Mac you create `Lavoro`, `Archivio`, `2026-11-03 - Bolletta Enel.pdf`; on an English one `Work`, `Archive`, `2026-11-03 - Electricity Bill.pdf` — even if the user chats with you in another language. Examples in this file are in English: translate them into `naming_language`. Existing folders keep their names; never propose translating them unless the user asks.

## The script

All file operations go through `scripts/organizer.py` inside this skill's base directory (the "Base directory for this skill" shown above). Never move, rename or delete the user's files with shell commands or file tools yourself.

```
python3 <base-dir>/scripts/organizer.py <command>      # macOS / Linux
python  <base-dir>\scripts\organizer.py <command>      # Windows (or: py -3 ...)
```

| Command | What it does |
|---|---|
| `info` | platform, `system_language`, `naming_language` (the one to use for names), managed folders with their policy, destination, config path, `onboarding_needed`, `onboarding_new_questions`, trash backend, whether the trash is readable |
| `config` | the effective configuration (user file merged over defaults) |
| `config --init` | write a starter `~/.organize/config.json` with the defaults (fails if one exists) |
| `onboarding` | the onboarding questions (from `onboarding.json`), `first_time`, the current config, `current_answers` (which option each question currently corresponds to) and `new_questions` |
| `onboarding --apply <answers.json> [--dry-run]` | turn onboarding answers into the config (merged over the existing one, `.bak` kept); `--dry-run` only prints the result |
| `config --write <file>` | validate `<file>` and save it as the config (previous one kept as `config.json.bak`); prints the error and changes nothing if invalid |
| `scan [paths...]` | JSON of top-level items (default: every managed folder) with `policy` of the folder they're in and `flags`: `duplicate` (+`duplicate_of`), `archive_extracted`, `code_project`, `installer`, `screenshot`, `temp`, `empty_dir`, `unclear_name` (IMG_1234, document (3)…), `large` (≥ `archive.large_mb`), `stale` (not used for `archive.stale_days`); plus `size`, `modified`, `last_used`, `kind` and `where_from` when the OS provides them |
| `report [--top N]` | read-only walk of all managed folders (also inside sub-folders; code projects count as one item; hidden, protected and cloud folders skipped): `biggest` files and `biggest_stale` (biggest not used for `stale_days`), `total_size`, `truncated` |
| `render-names <items.json> [--pattern P]` | proposed file names following `rename_files.pattern` (or `P`, for previews): input `[{"src","title","date"?,"category"?,"dir"?}]`, output `[{"src","dst","name"}]` (sanitized, unique, extension kept). Changes nothing |
| `cleanup-scan [--categories a,b]` | read-only list of reclaimable space **outside** the user's folders, per item: `id`, `category` (`docker`, `tool`, `cache`, `leftover`, `empty`), `name`, `path`, `size`, `last_used`, `action` (`trash` = recoverable / `command` = the tool's own cleanup command, permanent), `risk` (`low`/`medium`/`high`), `note`. Docker not running shows as `docker:error` |
| `cleanup-apply <ids.json>` | cleans the listed ids (JSON list). It rescans and acts only on ids the fresh scan finds — never on paths or commands you write. Prints `run_id`; trashed items can be undone, commands can't |
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
| `naming` | `{"language":"system","case":"Title Case"}` | Language of names you create — `system` (the computer's display language, see `system_language` in `info`) or a code like `en`, `it` — and their case (`Title Case`, `lowercase`, `kebab-case`, `snake_case`, `as is`; apply case rules sensibly for the language, e.g. Italian Title Case capitalizes only the first word and proper nouns: `Bollette luce`) |
| `taxonomy` | `[]` | Preferred top-level topic folders; empty = propose one from what you find |
| `max_depth` | `3` | Deepest folder level you create under the destination |
| `batch_size` | `8` | Max items per confirmation question (`1` = ask about every item) |
| `advice` | `true` | Finish with OS-specific advice |
| `archive` | `{"large_mb":500,"stale_days":365,"top":10}` | Flag items ≥ `large_mb` and items not used for `stale_days` (`0` = off); `report` lists the `top` biggest. You propose archiving them |
| `rename_files` | `{"pattern":"","scope":"unclear"}` | File renaming. `pattern` `""` = never rename; tokens `{title}` (required), `{date}` (YYYY-MM-DD), `{month}`, `{year}`, `{category}`, `{original}`. `scope`: `unclear` = only `unclear_name` files, `all` = every file you move |
| `cleanup` | `{"categories":[],"min_size_mb":50,"leftover_min_age_days":180}` | System cleanup: which categories to look at (`[]` = off), smallest item worth listing, how long an app's leftover data must be unused |
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
5. **Protected — never move, rename, trash or clean:** this skill's own folder, anything a symlink or shortcut in `~/.claude/` points into, cloud-sync roots, system/app folders, and everything in `protected` / matching `keep`.
6. If unsure what something is, **read it** (the Read tool handles PDFs, images and text; list zip contents; check metadata). Don't guess on personal documents. Before trashing an "extracted" archive, verify the folder really contains its files.

## Onboarding

- **First time: mandatory.** When `info` says `onboarding_needed: true` (never onboarded, even if a config file was written by hand), do the onboarding before anything else. Don't scan, propose or change anything until it's saved. If the user wants to skip it, explain in one line that it takes a minute and decides what /organize may touch; if they still refuse, stop — don't organize with guessed preferences.
- **Afterwards: only on request.** Never start it on your own. The user asks for it ("/organize setup", "rifai l'onboarding", "cambia le mie preferenze"). They may redo **all** of it or **only a part** ("rifai solo la parte delle cartelle", "change how files are named"): then ask only the matching round(s) or questions, plus their follow-ups. Answers you don't ask again stay as they are (the script merges over the current config and evaluates `when` against it).
- **New questions** (`onboarding_new_questions` non-empty, after an update of the skill): don't ask them. At the end of the run, mention in one line that new options exist (name them in plain words) and that the user can say "rifai l'onboarding" to set them; until then their defaults apply.

1. Say in one line that you'll ask a few quick questions so /organize works the way they like, and that nothing on their computer changes during the onboarding.
2. Run `onboarding`. It returns `rounds`, each with up to 4 `questions`. For each round, in order:
   - keep only questions whose `when` matches earlier answers (a list in `when` means any of those answers); skip the round if none is left;
   - ask them in **one** AskUserQuestion call: `header` → header, `question` → question, each option's `label`/`description` → option label/description. **Translate everything into the language the user writes in.** Replace `{system_language}` in a description with the detected language's name (from `system_language`). On a re-run (`first_time` false), put the option in `current_answers` first and add " (current)" to its label;
   - map each reply to the option `id`. If the user typed their own answer ("Other"), pick the option it means; if it's a list of names or paths, use the option with `values_to` and pass the list; if it's a description for an option with `text_to`, pass the text. If it means something no option covers, keep it to add as a free-text rule.
   - For options with `values_to` / `text_to` (e.g. "I'll name them", "My own scheme") the user may already have typed the value in "Other"; if not, ask in plain text right after the round.
   - **Preset folder names** (an option whose `set` holds a list, e.g. sections "In Progress / To Read / Archive"): show them, and pass them translated into the naming language that this onboarding sets (system language if unchanged), same order: `{"option": "work_style", "values": ["In corso", "Da leggere", "Archivio"]}`. If the naming language is English, pass the option id alone.
   - **Another language** (`text_to: naming.language`): pass the language code (`fr`, `pt-BR`), not its name.
   - **Custom rename scheme** (`text_to: rename_files.pattern`): turn the user's description into a pattern with the tokens `{title}` (required), `{date}`, `{month}`, `{year}`, `{category}`, `{original}`, e.g. "anno, categoria e titolo" → `{year} - {category} - {title}`. Show a sample name (`render-names --pattern` on any file, or just write one) and confirm it with the user.
3. Write `<scratch>/answers.json`:
   `{"desktop": "sections", "desktop_sections": {"option": "custom", "values": ["Work", "To Do"]}, "rename": {"option": "custom", "text": "{year} - {title}"}, "trash": "never", ...}`. On a partial re-run, include only the questions you asked.
   Answers that only fit as free text go into the `rules` question: `"rules": {"option": "yes", "values": [...]}`.
4. Run `onboarding --apply <scratch>/answers.json --dry-run`. If it reports `invalid`, explain the problem in plain words and ask the one question needed to fix it — e.g. a folder set to *Empty* while *Documents* is *Don't touch* leaves nowhere to put the files: offer "Documents: only remove junk" (files can still arrive there) or changing the other folder — then retry.
5. Show a short recap in the user's language (one line per folder, Trash, names, confirmations, protected paths, rules) and ask with AskUserQuestion: `Save` / `Change something`. On *Save* run `onboarding --apply <scratch>/answers.json` (no `--dry-run`). On *Change something*, ask only the questions they want to change and repeat from step 4.
6. Confirm it's saved, mention they can say "configure organize" for single changes or redo the onboarding any time, then continue with the Flow (unless they only asked for setup).

To add questions, edit `onboarding.json` (its `_comment` explains the format), give them `"since": <new version>` and bump `version`: existing users are told new options exist and get them when they ask for a new onboarding.

## Flow

The full run is steps 1–10. If the user asks for just one part ("clean Docker", "free up space", "show me my biggest files", "rename my downloads"), do step 1 (onboarding included if it's the first time), then only the steps that part needs.

### 1. Check the environment and load preferences
Run `info`. If `onboarding_needed` is true, do the **Onboarding** first (mandatory). Then run `config` and briefly tell the user which preferences are active (one or two lines: e.g. "Desktop: sections (Work, To Do) · Downloads: empty · Trash: never · 2 custom rules").
If `trash` is `ask` and `trash_readable` is false, tell the user in one line how to fix it (macOS: System Settings → Privacy & Security → Full Disk Access → enable the app running Claude Code, then restart it).

### 2. Scan
Run `scan > <scratch>/scan.json`, then summarize: counts per folder (with its policy), duplicates, extracted archives, installers, temp files, code projects, unclear names, big and long-unused items, space that can be freed. Peek inside folders whose name says nothing.

### 3. Destination structure
Look at the existing top-level folders in the destination (peek inside ambiguous ones). Propose a taxonomy using `naming_language` and `naming.case`:
- if `taxonomy` is set, use those folders as the top level and only propose additions when something clearly doesn't fit (ask);
- otherwise build one from what's there and what's coming in. A typical set (translate into `naming_language`) — adapt, don't force: `Finance`, `University` / `School`, `Work`, `Personal`, `Fitness`, `Publications`, `Books`, `Languages`, `Media` (Photos, Videos, Wallpapers, Designs, Screenshots), `Projects` (code), `Datasets`, `Archive`.

Respect `max_depth`. For `tidy` folders other than the destination, also propose their internal sub-folders. Show it as a tree, including renames/merges of existing top-level folders, and get approval **before anything else**. If the structure already exists from a previous run, only propose new folders.

### 4. Classify
For every item decide one of:
- **move** → a destination in the approved structure (not allowed for items in `keep` folders),
- **trash** → with a concrete reason: identical `duplicate` of X, archive already extracted (verified), installer for an app already installed, `temp` file, empty folder, files that clearly belong to someone else and were already delivered (ask),
- **leave** → items in `keep` folders that aren't junk, or items the user's `rules` say to leave,
- **ask** → you genuinely can't tell.

Apply the user's `rules` here. Use names, `kind`, `where_from`, dates and file contents. Group related files across folders.

**New names.** If `rename_files.pattern` is set, for every file you'll move that is in scope (`unclear` → only `unclear_name` files; `all` → every moved file):
- work out a short descriptive `title` in the configured naming language (read the file if the name doesn't say: an invoice → "Bolletta Enel Novembre"), the document's own `date` if it has one (invoice date, photo date in the name or metadata; otherwise leave it out and the file's modification date is used) and the `category` (its topic folder);
- run `render-names <scratch>/names.json` with `"dir"` set to the destination folder, and use each returned `dst` as the move destination — one `move` does both;
- in the confirmation, show `old name → new name` for each file.
Files that stay where they are (`tidy` folders) can get a `rename` in place with the same procedure; never rename in `keep` folders, inside code projects, or anything you're not sure about (ask). The script never overwrites: a taken name becomes `name (2).ext`.

### 5. Confirm in batches
Group items by destination (or by trash reason), at most `batch_size` items per group. Use AskUserQuestion, up to 4 questions per call, one question per group, listing the files and the proposed action. Options:
- `Yes` — do it
- `No, leave it` — skip this group
- `Let me choose one by one` — split the group into individual questions next round
(The user can always type a different destination — honor it. If an answer is ambiguous about part of a group, do only the clear part and ask again about the rest.)

Start with the obvious batches (duplicates, temp files, installers, extracted archives) so the count drops fast. For renames add a 4th option `Move, keep the old names` when it makes sense.

### 6. Apply
Write only the approved actions to `<scratch>/plan-N.json`, run `apply`, and report: moved / trashed / skipped / errors (explain any `refused:` using the config), renamed-on-clash items, the `run_id`, and how to undo ("say 'undo'"). Repeat 4–6 until every `empty` folder is empty (or holds only what the user chose to keep) and every `tidy` folder is sorted.

### 7. Big and forgotten files
Skip if both `archive.large_mb` and `archive.stale_days` are 0. Run `report` and show, in one short table each: the biggest files (`biggest`, with size and last use) and the biggest ones not used for `stale_days` (`biggest_stale`); also the total size. Then propose, per group of similar items, with AskUserQuestion:
- **Archive** — move to `<destination>/<Archive folder>` (named with the configured naming, e.g. `Archive`/`Archivio`; keep the original sub-path or topic inside it, e.g. `Archivio/Università/…`), for long-unused things worth keeping;
- **Trash** — only for things that are clearly junk (old installers, a downloaded ISO, a duplicate), with the reason;
- **Leave it**.
Never propose moving items in `keep` folders (only mention them), code projects that aren't `safe_to_move`, or inside topic folders the user didn't open up (rule 4) — for those just report them. `last_used` is a best guess (Spotlight on macOS; elsewhere access/modification time): say "not opened since about …", and if `last_used` looks wrong (e.g. a file the user says they use), believe the user. If `truncated` is true, say the list covers only part of the files.

### 8. System cleanup
Skip if `cleanup.categories` is empty — unless the user explicitly asked to clean Docker / caches / free disk space: then run `cleanup-scan --categories <what they asked for>` for this run only.

Run `cleanup-scan` and show a short table per category with sizes and the total. Then confirm with AskUserQuestion, grouped by category and risk:
- **`docker`** and **`tool`** (`action: command`): say it's **permanent** and name the command (e.g. `docker image prune -a`). Explain the item's `note` (images are downloaded again; stopped containers lose anything saved inside them). **Unused Docker volumes (`risk: high`) one at a time**, never in a group: they can hold databases and uploads; if the volume's name doesn't make it obvious, recommend keeping it.
- **`cache`** (to the Trash): fine as a group; suggest closing the apps involved first (browsers, Slack, IDEs).
- **`leftover`** (`risk: medium`): data of apps that **seem** uninstalled — the match is a best guess. Before proposing, check each one yourself (is the app installed under another name, a CLI tool, a Homebrew/apt/winget package, a Mac App Store app?) and peek inside: keep anything with documents, saves, databases, licenses or exports, and say so. Ask **one by one** (or in small groups of clearly-dead apps), showing size and last use.
- **`empty`**: one group.
Write the approved ids to `<scratch>/cleanup-N.json`, run `cleanup-apply`, and report per item (freed space is an estimate; trashed items free space only when the Trash is emptied — mention it if `trash` is `never`). `cleanup-apply` refuses anything that isn't in a fresh scan: if it says *not found*, rescan instead of retrying.

Never clean system locations yourself with shell commands (`rm`, `docker system prune --volumes`, `sudo apt clean`…), even if the user asks: offer what `cleanup-scan` found, and for anything else explain the command and let the user run it.

### 9. Trash
Skip entirely if `trash` is `never`. Otherwise run `trash-list`; show count, size and the biggest items. Ask a **separate** AskUserQuestion: "Empty the Trash permanently? This can't be undone." Only on yes run `empty-trash --yes-really`.

### 10. Report and advice
Short report (max ~10 bullets): what was done (numbers, space freed), what was left and why. If `advice` is true, add patterns you noticed and concrete fixes for their OS, for example:
- macOS: `defaults write com.apple.screencapture location <destination>/Media/Screenshots && killall SystemUIServer`; Finder → Settings → Advanced → "Remove items from the Trash after 30 days".
- Windows: Settings → System → Storage → Storage Sense; move the Screenshots folder via its Properties → Location.
- Linux: GNOME Settings → Privacy → File History & Trash → auto-delete; set the screenshot folder in the screenshot tool.

If `onboarding_new_questions` is non-empty, add one line about the new options (see *Onboarding*).

If during the run the user repeatedly made the same choice (e.g. always "No, leave it" for the Desktop, always a certain destination for a file type), offer in one line to save it to their config.

## Configure
For a full or partial re-setup, run the **Onboarding** again (only when the user asks). For single changes — when the user asks to configure /organize, change its rules, or says something like "never touch my Desktop", "don't empty the Trash", "use Italian folder names", "always leave .lnk files":
1. Run `config` to get the current settings.
2. Make just the change they asked for (ask a clarifying question only if it's ambiguous).
3. Write the full new config (only the keys that differ from the defaults, plus any existing ones) to `<scratch>/config.json`, show the user the diff in plain words, and on yes run `config --write <scratch>/config.json`. If it reports `invalid`, fix it and retry.
4. Confirm what changed. Never edit `~/.organize/config.json` directly with file tools.

## Undo
If the user says "undo": run `runs`, confirm which run, then `undo <run-id>` and report. Trashed items are restored too (on Windows, tell them to restore from the Recycle Bin).
