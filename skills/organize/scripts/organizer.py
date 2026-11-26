#!/usr/bin/env python3
"""organize — safety script for the /organize Claude Code skill.

Does every filesystem operation for the skill, journals each one so it can be
undone, and never deletes anything except via `empty-trash --yes-really`.
Works on macOS, Linux (freedesktop Trash) and Windows (Recycle Bin).
Python 3.8+, standard library only.

Commands:
  info                      platform, user folders, trash backend, effective config
  config [--init|--write F] show the effective config; create a starter file; validate+save F
  onboarding [--apply A]    print the onboarding questions; turn answers A into the config
  scan [paths...]           JSON inventory of top-level items (default: every managed folder)
  cleanup-scan              reclaimable space outside your folders (Docker, caches, app leftovers)
  cleanup-apply <ids.json>  clean the given cleanup-scan item ids (rescans first)
  report [--top N]          biggest files and biggest unused ones across the managed folders
  render-names <items.json> file names following the configured rename pattern (preview only)
  check-project <path>      virtualenvs and scheduled jobs that depend on a folder's path
  apply <plan.json>         run a list of actions (mkdir / move / rename / trash)
  undo [run-id]             reverse a run (default: the last one)
  runs                      list past runs
  trash-list                JSON list of what's in the Trash
  empty-trash --yes-really  permanently empty the Trash
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import heapq
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote, unquote

HASH_LIMIT = 50 * 1024 * 1024
SIZE_WALK_LIMIT = 20000
CODE_MARKERS = {
    ".git", "pyproject.toml", "requirements.txt", "setup.py", "package.json",
    "venv", ".venv", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Gemfile", "Makefile", "CMakeLists.txt", "Package.swift", "docker-compose.yml",
    "compose.yaml", "composer.json", "*.sln", "*.csproj",
}
INSTALLER_EXT = {".dmg", ".pkg", ".mpkg", ".exe", ".msi", ".deb", ".rpm", ".appimage", ".flatpakref"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".xz", ".bz2"}
TEMP_EXT = {".crdownload", ".part", ".download", ".tmp", ".partial"}
SCREENSHOT_RE = re.compile(
    r"^(Screenshot|Screen Shot|Screen Recording|Schermata|Registrazione schermo|"
    r"Capture d.écran|Bildschirmfoto|Captura de pantalla)[ _\-]",
    re.I,
)
REPORT_WALK_LIMIT = 200000
# Names that say nothing about the content: camera/scanner/download defaults, "(1)" copies, ids.
UNCLEAR_NAME_RE = re.compile(
    r"^(?:(?:IMG|DSC|DSCN|DCIM|PXL|VID|MOV|MVIMG|WP|PANO|GOPR|Scan|Scansione|Document|Documento|"
    r"Doc|File|Download|Untitled|Untitled document|Senza titolo|Unnamed|Image|Immagine|Photo|Foto|"
    r"New|Nuovo|Copy|Copia|Attachment|Allegato)?[\s_\-.]*[\d\s_\-.()]*"
    r"|WhatsApp (?:Image|Video|Audio)[\d\s_\-.()]*(?:at[\d\s_\-.()]*)?"
    r"|[0-9a-f]{8,}(?:-[0-9a-f]{4,})*)$",
    re.I,
)
NAME_TOKENS = ("title", "date", "month", "year", "category", "original")
WIN_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM\d|LPT\d)(\..*)?$", re.I)
SKIP_NAMES = {".DS_Store", ".localized", "desktop.ini", "Thumbs.db", ".directory"}
WIN_FOLDER_KEYS = {
    "desktop": "Desktop",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "documents": "Personal",
}

_run = subprocess.run  # patched in tests


# ---------- platform & paths ----------

def platform_name() -> str:
    forced = os.environ.get("ORGANIZE_PLATFORM")
    if forced:
        return forced
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def test_mode() -> bool:
    return bool(os.environ.get("ORGANIZE_HOME"))


def home() -> Path:
    return Path(os.environ.get("ORGANIZE_HOME") or Path.home())


def _xdg_user_dirs() -> dict:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    cfg = Path(xdg) if xdg and not test_mode() else home() / ".config"
    out = {}
    f = cfg / "user-dirs.dirs"
    if not f.exists():
        return out
    keys = {"XDG_DESKTOP_DIR": "desktop", "XDG_DOWNLOAD_DIR": "downloads", "XDG_DOCUMENTS_DIR": "documents"}
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'\s*(XDG_\w+_DIR)\s*=\s*"?(.*?)"?\s*$', line)
        if m and m.group(1) in keys:
            val = m.group(2).replace("$HOME", str(home()))
            out[keys[m.group(1)]] = Path(val)
    return out


def _windows_user_dirs() -> dict:
    out = {}
    try:
        import winreg  # type: ignore
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        for name, reg in WIN_FOLDER_KEYS.items():
            try:
                val, _ = winreg.QueryValueEx(k, reg)
                out[name] = Path(os.path.expandvars(val))
            except OSError:
                pass
    except Exception:
        pass
    return out


def user_folders() -> dict:
    """Real Desktop / Downloads / Documents for this OS (handles XDG and OneDrive redirects)."""
    defaults = {"desktop": home() / "Desktop", "downloads": home() / "Downloads", "documents": home() / "Documents"}
    plat = platform_name()
    if plat == "linux":
        found = _xdg_user_dirs()
    elif plat == "windows" and not test_mode():
        found = _windows_user_dirs()
    else:
        found = {}
    return {k: found.get(k, v) for k, v in defaults.items()}


# ---------- user configuration ----------
#
# ~/.organize/config.json (or $ORGANIZE_CONFIG) customizes the skill. Every key is
# optional; missing keys fall back to DEFAULT_CONFIG, which reproduces the original
# behaviour. The script enforces the path-related keys (folders, extra_folders,
# protected, keep); the rest are preferences Claude reads via `config`.

FOLDER_POLICIES = ("empty", "tidy", "keep", "skip")
TRASH_POLICIES = ("ask", "never")
DEFAULT_CONFIG: dict = {
    # How to treat each standard folder:
    #   empty -> move everything out (original behaviour for Desktop/Downloads)
    #   tidy  -> items may stay, but sorted into sub-folders inside it
    #   keep  -> leave items where they are; only propose clear junk (duplicates, temp…)
    #   skip  -> don't scan it and refuse any operation inside it
    # A value can also be {"policy": "tidy", "sections": ["In Progress", "Archive"]}:
    # sections are the sub-folders a tidy folder is sorted into (empty = Claude proposes).
    "folders": {"desktop": "empty", "downloads": "empty", "documents": "tidy"},
    # Where sorted items go: "documents" or an absolute / ~ path inside a managed folder.
    "destination": "documents",
    # Additional folders to manage, e.g. [{"path": "~/Pictures", "policy": "tidy"}].
    # Must be inside your home folder (not the home folder itself).
    "extra_folders": [],
    # Paths (absolute or ~) the script never moves, renames or trashes, nor anything inside them.
    "protected": [],
    # Name patterns (fnmatch, case-insensitive) never moved or trashed, e.g. ["*.lnk", "Inbox"].
    "keep": [],
    # Trash: "ask" = offer to empty it at the end (separate confirmation); "never" = don't touch it.
    "trash": "ask",
    # Names of folders (and renamed files) Claude creates. language: "system" = the computer's
    # display language (detected per machine), or a language code such as "en", "it".
    "naming": {"language": "system", "case": "Title Case"},
    # Preferred top-level topic folders. Empty = Claude proposes a taxonomy from what it finds.
    "taxonomy": [],
    # Deepest folder level Claude creates under the destination.
    "max_depth": 3,
    # How many items per confirmation batch Claude may group (1 = ask about each item).
    "batch_size": 8,
    # Finish with OS-specific advice.
    "advice": True,
    # Free-text extra rules for Claude, e.g. "Never touch files modified in the last 7 days".
    "rules": [],
    # Big and forgotten files: flag items >= large_mb and items not used for stale_days
    # (0 disables either); `report` lists the `top` biggest files. Claude proposes archiving them.
    "archive": {"large_mb": 500, "stale_days": 365, "top": 10},
    # Renaming files. pattern "" = never rename. Tokens: {title} (required), {date} (YYYY-MM-DD),
    # {month} (YYYY-MM), {year}, {category}, {original} (current name without extension).
    # scope: "unclear" = only names like IMG_1234 / document (3); "all" = every file being moved.
    "rename_files": {"pattern": "", "scope": "unclear"},
    # System cleanup outside your folders (always asked item by item or by group):
    # categories: docker (unused images, stopped containers, build cache, unused volumes),
    # tool (npm/pip/yarn/go/brew caches, via their own command), cache (app caches),
    # leftover (data of apps that are no longer installed), empty (empty app-data folders).
    # [] = off. Items smaller than min_size_mb are not listed; leftovers must be unused for
    # leftover_min_age_days.
    "cleanup": {"categories": [], "min_size_mb": 50, "leftover_min_age_days": 180},
    # Version of onboarding.json the user last answered (0 = never onboarded).
    "onboarding_version": 0,
}


class ConfigError(ValueError):
    pass


LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")


def _lang_result(tag: str, source: str) -> Optional[dict]:
    tag = (tag or "").strip().strip('"').split(".")[0].split("@")[0]
    if not tag or tag.upper() in ("C", "POSIX") or not LANG_RE.match(tag):
        return None
    return {"code": re.split(r"[-_]", tag)[0].lower(), "tag": tag.replace("_", "-"), "source": source}


def system_language() -> dict:
    """The computer's display language: what the user sees in Finder / Explorer / their desktop.
    Names Claude creates follow it when naming.language is "system"."""
    forced = os.environ.get("ORGANIZE_SYSTEM_LANG")
    if forced:
        return _lang_result(forced, "ORGANIZE_SYSTEM_LANG") or {"code": "en", "tag": "en", "source": "default"}
    plat = platform_name()
    if plat == "macos":
        try:
            out = _run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)"?', out.replace("(", " "))
            r = m and _lang_result(m.group(1), "AppleLanguages")
            if r:
                return r
        except Exception:
            pass
    if plat == "windows":
        try:
            import ctypes
            import locale as _locale
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            r = _lang_result(_locale.windows_locale.get(lcid, ""), "Windows display language")
            if r:
                return r
        except Exception:
            pass
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        r = _lang_result(val.split(":")[0], var)
        if r:
            return r
    try:
        import locale as _locale
        r = _lang_result(_locale.getlocale()[0] or "", "locale")
        if r:
            return r
    except Exception:
        pass
    return {"code": "en", "tag": "en", "source": "default"}


def naming_language(cfg: dict) -> str:
    lang = cfg["naming"]["language"]
    return system_language()["code"] if lang == "system" else lang


def config_path() -> Path:
    env = os.environ.get("ORGANIZE_CONFIG")
    return Path(env).expanduser() if env else home() / ".organize" / "config.json"


def _expand(p: str) -> Path:
    s = str(p)
    if s == "~" or s.startswith("~/") or s.startswith("~\\"):
        s = str(home()) + s[1:]
    return Path(os.path.expandvars(s))


def validate_config(raw) -> dict:
    """Merge raw user config over the defaults and validate it. Raises ConfigError."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    unknown = set(raw) - set(DEFAULT_CONFIG) - {"$schema", "_comment"}
    if unknown:
        raise ConfigError(f"unknown key(s): {', '.join(sorted(unknown))}")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in raw.items():
        if k in ("$schema", "_comment"):
            continue
        if isinstance(cfg[k], dict) and isinstance(v, dict):
            cfg[k].update(v)
        else:
            cfg[k] = v

    if not isinstance(cfg["folders"], dict):
        raise ConfigError("folders must be an object")
    for name, val in list(cfg["folders"].items()):
        if name not in ("desktop", "downloads", "documents"):
            raise ConfigError(f"folders.{name}: only desktop, downloads, documents")
        cfg["folders"][name] = _folder_entry(val, f"folders.{name}")
    if cfg["trash"] not in TRASH_POLICIES:
        raise ConfigError(f"trash must be one of {TRASH_POLICIES}")
    for key in ("protected", "keep", "taxonomy", "rules", "extra_folders"):
        if not isinstance(cfg[key], list):
            raise ConfigError(f"{key} must be a list")
    for key in ("protected", "keep", "taxonomy", "rules"):
        if not all(isinstance(x, str) and x.strip() for x in cfg[key]):
            raise ConfigError(f"{key} must contain non-empty strings")
    if not isinstance(cfg["max_depth"], int) or not 1 <= cfg["max_depth"] <= 6:
        raise ConfigError("max_depth must be an integer between 1 and 6")
    if not isinstance(cfg["batch_size"], int) or not 1 <= cfg["batch_size"] <= 50:
        raise ConfigError("batch_size must be an integer between 1 and 50")
    a = cfg["archive"]
    if not isinstance(a, dict) or set(a) - {"large_mb", "stale_days", "top"}:
        raise ConfigError("archive: only large_mb, stale_days, top")
    for k in ("large_mb", "stale_days"):
        if not isinstance(a.get(k), int) or isinstance(a.get(k), bool) or a[k] < 0:
            raise ConfigError(f"archive.{k} must be an integer >= 0 (0 = off)")
    if not isinstance(a.get("top"), int) or not 1 <= a["top"] <= 100:
        raise ConfigError("archive.top must be an integer between 1 and 100")
    cl = cfg["cleanup"]
    if not isinstance(cl, dict) or set(cl) - {"categories", "min_size_mb", "leftover_min_age_days"}:
        raise ConfigError("cleanup: only categories, min_size_mb, leftover_min_age_days")
    if not isinstance(cl.get("categories"), list) or set(cl["categories"]) - set(CLEANUP_CATEGORIES):
        raise ConfigError(f"cleanup.categories: a list of {', '.join(CLEANUP_CATEGORIES)}")
    for k in ("min_size_mb", "leftover_min_age_days"):
        if not isinstance(cl.get(k), int) or isinstance(cl.get(k), bool) or cl[k] < 0:
            raise ConfigError(f"cleanup.{k} must be an integer >= 0")
    r = cfg["rename_files"]
    if not isinstance(r, dict) or set(r) - {"pattern", "scope"}:
        raise ConfigError("rename_files: only pattern, scope")
    if r.get("scope") not in ("unclear", "all"):
        raise ConfigError("rename_files.scope must be 'unclear' or 'all'")
    pat = r.get("pattern")
    if not isinstance(pat, str):
        raise ConfigError("rename_files.pattern must be a string")
    if pat:
        bad = set(re.findall(r"{(\w*)}", pat)) - set(NAME_TOKENS)
        if bad:
            raise ConfigError(f"rename_files.pattern: unknown token(s) {', '.join('{%s}' % b for b in sorted(bad))}")
        if "{title}" not in pat:
            raise ConfigError("rename_files.pattern must contain {title}")
        if "/" in pat or "\\" in pat:
            raise ConfigError("rename_files.pattern can't contain / or \\")
    if not isinstance(cfg["onboarding_version"], int) or cfg["onboarding_version"] < 0:
        raise ConfigError("onboarding_version must be a non-negative integer")
    if not isinstance(cfg["advice"], bool):
        raise ConfigError("advice must be true or false")
    if not isinstance(cfg["naming"], dict):
        raise ConfigError("naming must be an object")
    lang = cfg["naming"].get("language")
    if not isinstance(lang, str) or not (lang == "system" or LANG_RE.match(lang)):
        raise ConfigError("naming.language must be \"system\" or a language code like \"en\", \"it\", \"pt-BR\"")

    h = _abs(home()).resolve()
    extras = []
    for i, e in enumerate(cfg["extra_folders"]):
        if isinstance(e, str):
            e = {"path": e}
        if not isinstance(e, dict) or not isinstance(e.get("path"), str):
            raise ConfigError(f"extra_folders[{i}] needs a \"path\"")
        entry = _folder_entry({"policy": e.get("policy", "tidy"), "sections": e.get("sections", [])},
                              f"extra_folders[{i}]")
        p = _abs(_expand(e["path"]))
        if not _inside(p, h) or _key(p) == _key(h):
            raise ConfigError(f"extra_folders[{i}]: {e['path']} must be inside your home folder")
        rel = p.parts[len(h.parts):]
        if any(part.startswith(".") for part in rel) or (h / ".claude") == p:
            raise ConfigError(f"extra_folders[{i}]: hidden folders can't be managed")
        extras.append({"path": str(p), **entry})
    cfg["extra_folders"] = extras

    # sorted items need somewhere to go if any folder must be emptied
    if any(f["policy"] == "empty" for f in list(cfg["folders"].values()) + extras):
        dest = destination_dir(cfg)
        roots = [r for _, r, _ in managed_roots(cfg)]
        if not any(_inside(dest, _abs(r)) for r in roots):
            raise ConfigError(
                f"destination {cfg['destination']!r} is not a managed folder (skipped?), "
                "but some folder is set to 'empty': choose another destination or another policy")
    return cfg


def _folder_entry(val, where: str) -> dict:
    if isinstance(val, str):
        val = {"policy": val}
    if not isinstance(val, dict) or set(val) - {"policy", "sections"}:
        raise ConfigError(f"{where}: use a policy string or {{\"policy\", \"sections\"}}")
    pol = val.get("policy", "tidy")
    if pol not in FOLDER_POLICIES:
        raise ConfigError(f"{where}: policy must be one of {FOLDER_POLICIES}")
    sections = val.get("sections", [])
    if not isinstance(sections, list) or not all(isinstance(x, str) and x.strip() for x in sections):
        raise ConfigError(f"{where}.sections must be a list of names")
    if sections and pol != "tidy":
        raise ConfigError(f"{where}: sections only make sense with policy 'tidy'")
    for x in sections:
        if x.strip() in (".", "..") or "/" in x or "\\" in x or x.startswith("."):
            raise ConfigError(f"{where}.sections: {x!r} is not a valid folder name")
    return {"policy": pol, "sections": [x.strip() for x in sections]}


def load_config() -> dict:
    f = config_path()
    if not f.exists():
        return validate_config({})
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{f}: invalid JSON ({e})")
    return validate_config(raw)


def managed_roots(cfg: Optional[dict] = None) -> List[Tuple[str, Path, str]]:
    """(name, path, policy) of every folder the skill may operate in; skipped ones excluded."""
    cfg = cfg or load_config()
    out = [(k, v, cfg["folders"].get(k, {"policy": "tidy"})["policy"]) for k, v in user_folders().items()]
    out += [(Path(e["path"]).name, Path(e["path"]), e["policy"]) for e in cfg["extra_folders"]]
    return [r for r in out if r[2] != "skip"]


def destination_dir(cfg: Optional[dict] = None) -> Path:
    cfg = cfg or load_config()
    d = cfg["destination"]
    if not isinstance(d, str) or not d.strip():
        raise ConfigError("destination must be a folder name or path")
    if d in user_folders():
        return user_folders()[d]
    return _abs(_expand(d))


# ---------- onboarding ----------

def onboarding_file() -> Path:
    return skill_dir() / "onboarding.json"


def load_onboarding() -> dict:
    return json.loads(onboarding_file().read_text(encoding="utf-8"))


def _matches(when: Optional[dict], answers: dict) -> bool:
    for qid, want in (when or {}).items():
        got = answers.get(qid)
        got = got.get("option") if isinstance(got, dict) else got
        if got not in (want if isinstance(want, list) else [want]):
            return False
    return True


def _get_dotted(cfg: dict, dotted: str):
    d = cfg
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def current_answers(cfg: dict) -> dict:
    """Which onboarding option the (validated) config currently corresponds to, per question.
    Used to mark "(current)" options and to evaluate `when` for questions not re-asked."""
    out = {}
    for r in load_onboarding()["rounds"]:
        for q in r["questions"]:
            chosen = None
            for o in q["options"]:          # 1. options whose `set` matches the config exactly
                sets = o.get("set") or {}
                if sets and all(_get_dotted(cfg, k) == v or (isinstance(v, list) and isinstance(_get_dotted(cfg, k), list)
                                and len(v) == len(_get_dotted(cfg, k)) and v)
                                for k, v in sets.items()):
                    chosen = o["id"]
                    break
            if chosen is None:              # 2. free-text options whose target is filled
                for o in q["options"]:
                    tgt = o.get("values_to") or o.get("text_to")
                    if tgt and _get_dotted(cfg, tgt):
                        chosen = o["id"]
                        break
            if chosen is None:              # 3. an option that sets nothing ("propose", "no")
                chosen = next((o["id"] for o in q["options"]
                               if not o.get("set") and not o.get("values_to") and not o.get("text_to")), None)
            if chosen is not None:
                out[q["id"]] = chosen
    return out


def new_onboarding_questions(cfg: dict) -> List[str]:
    done = cfg["onboarding_version"]
    if done == 0:
        return []
    return [q["id"] for r in load_onboarding()["rounds"] for q in r["questions"] if q.get("since", 1) > done]


def _set_dotted(cfg: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    d = cfg
    for i, part in enumerate(parts[:-1]):
        nxt = d.get(part)
        if isinstance(nxt, str) and parts[0] == "folders" and i == 1:
            nxt = {"policy": nxt}           # "desktop": "tidy" -> {"policy": "tidy"}
        if not isinstance(nxt, dict):
            nxt = {}
        d[part] = nxt
        d = nxt
    d[parts[-1]] = value


def onboarding_to_config(answers: dict, base: Optional[dict] = None) -> dict:
    """Turn onboarding answers into a raw config (merged over `base`), validated.

    answers: {question_id: option_id | {"option": option_id, "values": [..]} | {"option", "text"}}
    For an option whose `set` holds a list of names (preset sections), "values" are the same
    names translated into the naming language; they replace the preset (same length).
    Raises ConfigError on unknown questions/options or an invalid result.
    """
    ob = load_onboarding()
    raw = json.loads(json.dumps(base or {}))
    questions = {q["id"]: q for r in ob["rounds"] for q in r["questions"]}
    unknown = set(answers) - set(questions)
    if unknown:
        raise ConfigError(f"unknown onboarding question(s): {', '.join(sorted(unknown))}")
    try:
        context = current_answers(validate_config(raw))
    except ConfigError:
        context = {}
    context.update(answers)
    for r in ob["rounds"]:
        for q in r["questions"]:
            if q["id"] not in answers or not _matches(q.get("when"), context):
                continue
            a = answers[q["id"]]
            opt_id, values = (a.get("option"), a.get("values")) if isinstance(a, dict) else (a, None)
            opt = next((o for o in q["options"] if o["id"] == opt_id), None)
            if opt is None:
                raise ConfigError(f"{q['id']}: unknown option {opt_id!r}")
            for key, val in (opt.get("set") or {}).items():
                if isinstance(val, list) and values is not None and not opt.get("values_to"):
                    if not isinstance(values, list) or len(values) != len(val) or not all(str(v).strip() for v in values):
                        raise ConfigError(f"{q['id']}: translated values must be {len(val)} names, like {val}")
                    val = [str(v).strip() for v in values]
                _set_dotted(raw, key, val)
            if opt.get("values_to"):
                if not isinstance(values, list) or not values:
                    raise ConfigError(f"{q['id']}: option {opt_id!r} needs \"values\" (a list)")
                _set_dotted(raw, opt["values_to"], [str(v).strip() for v in values if str(v).strip()])
            if opt.get("text_to"):
                text = a.get("text") if isinstance(a, dict) else None
                if not isinstance(text, str) or not text.strip():
                    raise ConfigError(f"{q['id']}: option {opt_id!r} needs \"text\" (a string)")
                _set_dotted(raw, opt["text_to"], text.strip())
    # a folder that's no longer "tidy" drops its sections
    for f in (raw.get("folders") or {}).values():
        if isinstance(f, dict) and f.get("policy") != "tidy":
            f.pop("sections", None)
    raw["onboarding_version"] = int(ob.get("version", 1))
    validate_config(raw)
    return raw


def save_config(raw: dict) -> Path:
    f = config_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        shutil.copy2(str(f), str(f.with_suffix(".json.bak")))
    f.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f


RUN_ID_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
CLOUD_ROOT_RE =re.compile(r"^(iCloud Drive|OneDrive.*|Dropbox.*|Google Drive.*|My Drive|Box|pCloud Drive|MEGA)$", re.I)


def _abs(p: Path) -> Path:
    """Absolute path WITHOUT following a symlink at the last component (parents are resolved)."""
    p = Path(os.path.abspath(str(p.expanduser())))
    return p.parent.resolve() / p.name


def skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _key(p: Path) -> str:
    """Comparable form of a path. macOS and Windows filesystems are case-insensitive and
    normalize Unicode, so compare case-folded NFC there; Linux compares exactly."""
    s = str(p).rstrip("/\\") or str(p)
    if platform_name() in ("macos", "windows"):
        s = unicodedata.normalize("NFC", s).casefold()
    return s


def _inside(child: Path, parent: Path) -> bool:
    """True if child is parent or anywhere below it."""
    c, p = _key(child), _key(parent)
    return c == p or c.startswith(p.rstrip("/\\") + os.sep) or c.startswith(p.rstrip("/\\") + "/")


def check_path(p: Path, role: str) -> Optional[str]:
    """Enforce the safety rules in code, whatever the plan says.

    Returns an error message, or None when the path is allowed. Allowed: items strictly
    inside a managed folder (Desktop / Downloads / Documents unless the config skips them,
    plus configured extra folders), not hidden, not a cloud-sync root, not this tool,
    not ~/.claude, not a configured protected path, and (as a source) not matching a
    configured keep pattern.
    """
    a = _abs(p)
    try:
        cfg = load_config()
    except ConfigError as e:
        return f"config error: {e}"
    roots = [_abs(r).resolve() for _, r, _ in managed_roots(cfg)]
    # most specific root first, so an extra folder nested in Documents gets its own rules
    roots.sort(key=lambda r: len(r.parts), reverse=True)
    root = next((r for r in roots if _inside(a, r) and _key(a) != _key(r)), None)
    if root is None:
        if any(_key(a) == _key(r) for r in roots):
            return f"{role} is a managed folder itself"
        return f"{role} must be inside a managed folder ({', '.join(r.name for r in roots) or 'none configured'})"
    for prot in cfg["protected"]:
        pp = _abs(_expand(prot))
        if _inside(a, pp) or (role == "source" and _inside(pp, a)):
            return f"{role} is or contains a protected path ({prot})"
    if role == "source":
        for pat in cfg["keep"]:
            if any(fnmatch.fnmatch(part.casefold(), pat.casefold()) for part in a.parts[len(root.parts):]):
                return f"{role} matches keep pattern {pat!r}"
    rel = a.parts[len(root.parts):]
    if any(part.startswith(".") for part in rel):
        return f"{role} is (inside) a hidden item"
    if CLOUD_ROOT_RE.match(rel[0]) and len(rel) == 1:
        return f"{role} is a cloud-sync folder"
    for protected in (skill_dir(), _abs(home()).resolve() / ".claude"):
        if _inside(protected, a) or _inside(a, protected):
            return f"{role} contains or is inside a protected folder ({protected.name})"
    try:
        for link in (_abs(home()).resolve() / ".claude" / "skills").iterdir():
            if link.is_symlink():
                target = link.resolve()
                if role == "source" and (_inside(target, a) or _inside(a, target)):
                    return f"{role} holds an installed skill ({link.name})"
    except OSError:
        pass
    return None


def journal_dir() -> Path:
    d = home() / ".organize" / "journal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def unique_dest(dest: Path, taken=lambda p: False) -> Path:
    def used(p: Path) -> bool:
        return p.exists() or p.is_symlink() or taken(p)

    if not used(dest):
        return dest
    is_dir = dest.is_dir()
    stem, suffix = (dest.name, "") if is_dir or not dest.suffix else (dest.stem, dest.suffix)
    n = 2
    while True:
        cand = dest.with_name(f"{stem} ({n}){suffix}")
        if not used(cand):
            return cand
        n += 1


# ---------- trash backends ----------

class TrashAccessError(Exception):
    pass


class MacTrash:
    name = "macos"

    def dir(self) -> Path:
        return home() / ".Trash"

    def put(self, src: Path) -> dict:
        d = self.dir()
        d.mkdir(exist_ok=True)
        final = unique_dest(d / src.name)
        shutil.move(str(src), str(final))
        return {"dst": str(final)}

    def restore(self, entry: dict) -> Tuple[str, str]:
        return _move_back(Path(entry["dst"]), Path(entry["src"]))

    def list(self) -> list:
        try:
            names = os.listdir(self.dir())
        except PermissionError as e:
            raise TrashAccessError(str(e))
        return [_describe(self.dir() / n) for n in sorted(names) if n not in SKIP_NAMES]

    def empty(self) -> Optional[int]:
        try:
            return _clear_dir(self.dir())
        except PermissionError:
            if test_mode():
                raise TrashAccessError("no access")
            _run(["osascript", "-e", 'tell application "Finder" to empty trash'], check=True,
                 capture_output=True)
            return None


class LinuxTrash:
    """freedesktop.org Trash spec (home trash): files/ + info/<name>.trashinfo."""
    name = "freedesktop"

    def base(self) -> Path:
        xdg = os.environ.get("XDG_DATA_HOME")
        root = Path(xdg) if xdg and not test_mode() else home() / ".local" / "share"
        return root / "Trash"

    def put(self, src: Path) -> dict:
        files, info = self.base() / "files", self.base() / "info"
        files.mkdir(parents=True, exist_ok=True)
        info.mkdir(parents=True, exist_ok=True)
        final = unique_dest(files / src.name, taken=lambda p: (info / f"{p.name}.trashinfo").exists())
        info_file = info / f"{final.name}.trashinfo"
        info_file.write_text(
            "[Trash Info]\n"
            f"Path={quote(str(_abs(src)), safe='/')}\n"
            f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n",
            encoding="utf-8",
        )
        try:
            shutil.move(str(src), str(final))
        except Exception:
            info_file.unlink()
            raise
        return {"dst": str(final), "info": str(info_file)}

    def restore(self, entry: dict) -> Tuple[str, str]:
        status, msg = _move_back(Path(entry["dst"]), Path(entry["src"]))
        if status == "ok":
            # Derive the .trashinfo path; never trust a path from the journal for deletion.
            (self.base() / "info" / f"{Path(entry['dst']).name}.trashinfo").unlink(missing_ok=True)
        return status, msg

    def list(self) -> list:
        files = self.base() / "files"
        if not files.exists():
            return []
        out = []
        for n in sorted(os.listdir(files)):
            item = _describe(files / n)
            info = self.base() / "info" / f"{n}.trashinfo"
            if info.exists():
                m = re.search(r"^Path=(.*)$", info.read_text(encoding="utf-8", errors="replace"), re.M)
                if m:
                    item["original_path"] = unquote(m.group(1))
            out.append(item)
        return out

    def empty(self) -> Optional[int]:
        n = _clear_dir(self.base() / "files") if (self.base() / "files").exists() else 0
        for sub in ("info", "expunged"):
            if (self.base() / sub).exists():
                _clear_dir(self.base() / sub)
        return n


class WindowsTrash:
    """Recycle Bin via PowerShell (Microsoft.VisualBasic.FileIO / Shell.Application)."""
    name = "windows"

    def _ps(self, script: str, target: Optional[str] = None) -> str:
        env = dict(os.environ)
        if target is not None:
            env["ORGANIZE_TARGET"] = target
        r = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                 capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise OSError((r.stderr or r.stdout or "powershell failed").strip())
        return r.stdout or ""

    def put(self, src: Path) -> dict:
        if src.is_symlink() or _is_junction(src):
            # Never let the Recycle Bin follow a link into its target: remove only the link
            # (no data lost) and remember its target so undo can recreate it.
            target = os.readlink(str(src))
            is_dir = src.is_dir()
            if is_dir and _is_junction(src):
                os.rmdir(str(src))
            else:
                os.unlink(str(src))
            return {"dst": None, "link_target": target, "link_is_dir": is_dir}
        fn = "DeleteDirectory" if src.is_dir() else "DeleteFile"
        self._ps(
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"[Microsoft.VisualBasic.FileIO.FileSystem]::{fn}($env:ORGANIZE_TARGET, "
            "'OnlyErrorDialogs', 'SendToRecycleBin')",
            target=str(_abs(src)),
        )
        return {"dst": None}

    def restore(self, entry: dict) -> Tuple[str, str]:
        if entry.get("link_target"):
            link = Path(entry["src"])
            if link.exists() or link.is_symlink():
                return "skipped", "the original location is taken"
            os.symlink(entry["link_target"], str(link), target_is_directory=bool(entry.get("link_is_dir")))
            return "ok", "link recreated"
        return "skipped", "in the Recycle Bin: right-click it there and choose Restore"

    def list(self) -> list:
        out = self._ps(
            "$s = New-Object -ComObject Shell.Application; "
            "@($s.Namespace(10).Items() | ForEach-Object { [pscustomobject]@{ "
            "name = $_.Name; path = $_.Path; is_dir = $_.IsFolder; size = $_.Size } }) "
            "| ConvertTo-Json -Compress"
        ).strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]

    def empty(self) -> Optional[int]:
        self._ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue")
        return None


def _is_junction(p: Path) -> bool:
    try:
        return bool(getattr(os.path, "isjunction", lambda _: False)(str(p)))
    except OSError:
        return False


def trash_backend():
    return {"macos": MacTrash, "linux": LinuxTrash, "windows": WindowsTrash}[platform_name()]()


def _backend_by_name(name: Optional[str]):
    return {"macos": MacTrash, "freedesktop": LinuxTrash, "windows": WindowsTrash}.get(name or "", MacTrash)()


def _trash_files_dir(backend: Optional[str]) -> Optional[Path]:
    """Folder where a backend keeps trashed files (None for the Windows Recycle Bin)."""
    if backend == "macos":
        return MacTrash().dir()
    if backend == "freedesktop":
        return LinuxTrash().base() / "files"
    return None


def _clear_dir(d: Path) -> int:
    n = 0
    for name in os.listdir(d):
        p = d / name
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
        n += 1
    return n


def _move_back(now: Path, orig: Path) -> Tuple[str, str]:
    if not now.exists() and not now.is_symlink():
        return "skipped", "it was moved or deleted since"
    if orig.exists():
        return "skipped", "the original location is taken"
    orig.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(now), str(orig))
    return "ok", ""


def _describe(p: Path) -> dict:
    try:
        st = p.lstat()
    except OSError:
        return {"name": p.name, "path": str(p)}
    is_dir = p.is_dir() and not p.is_symlink()
    return {"name": p.name, "path": str(p), "is_dir": is_dir,
            "size": _dir_size(p)[0] if is_dir else st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


# ---------- apply / undo ----------

def _new_run_id() -> str:
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id, n = base, 2
    while (journal_dir() / f"{run_id}.jsonl").exists() or (journal_dir() / f"{run_id}.undone").exists():
        run_id, n = f"{base}-{n}", n + 1
    return run_id


def _mkdirs(path: Path, log) -> None:
    missing = []
    p = path
    while not p.exists():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        d.mkdir()
        log({"op": "mkdir", "dst": str(d)})


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def apply_plan(actions: List[dict]) -> Tuple[str, List[dict]]:
    run_id = _new_run_id()
    jpath = journal_dir() / f"{run_id}.jsonl"
    results: List[dict] = []
    trash = trash_backend()

    with jpath.open("a", encoding="utf-8") as jf:
        def log(entry: dict) -> None:
            jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
            jf.flush()

        for a in actions:
            op = a.get("op")
            src = _abs(Path(a["src"])) if a.get("src") else None
            dst = _abs(Path(a["dst"])) if a.get("dst") else None
            res = {"op": op, "src": a.get("src"), "dst": a.get("dst"), "status": "ok", "msg": ""}
            denied = None
            if src is not None and op in ("move", "rename", "trash"):
                denied = check_path(src, "source")
            if not denied and dst is not None and op in ("move", "rename", "mkdir"):
                denied = check_path(dst, "destination")
            if denied:
                res["status"], res["msg"] = "error", f"refused: {denied}"
                results.append(res)
                continue
            try:
                if op == "mkdir":
                    if dst.exists():
                        res["status"], res["msg"] = "skipped", "already exists"
                    else:
                        _mkdirs(dst, log)
                elif src is None:
                    res["status"], res["msg"] = "error", "missing src"
                elif not src.exists() and not src.is_symlink():
                    res["status"], res["msg"] = "skipped", "source no longer exists"
                elif op == "trash":
                    info = trash.put(src)
                    log({"op": "trash", "backend": trash.name, "src": str(src), **info})
                    res["dst"] = info.get("dst")
                elif op in ("move", "rename"):
                    if dst is None:
                        res["status"], res["msg"] = "error", "missing dst"
                    elif src.is_dir() and _is_inside(dst.parent, src):
                        res["status"], res["msg"] = "error", "cannot move a folder into itself"
                    else:
                        _mkdirs(dst.parent, log)
                        final = unique_dest(dst)
                        shutil.move(str(src), str(final))
                        log({"op": "move", "src": str(src), "dst": str(final)})
                        res["dst"] = str(final)
                        if final != dst:
                            res["msg"] = f"name taken, saved as {final.name}"
                else:
                    res["status"], res["msg"] = "error", f"unknown op {op!r}"
            except Exception as e:  # report and keep going
                res["status"], res["msg"] = "error", str(e)
            results.append(res)

    return run_id, results


def list_runs() -> List[str]:
    return sorted(p.stem for p in journal_dir().glob("*.jsonl"))


def undo(run_id: Optional[str] = None) -> List[dict]:
    runs = list_runs()
    if not runs:
        return []
    run_id = run_id or runs[-1]
    if not RUN_ID_RE.match(run_id) or run_id not in runs:
        raise ValueError(f"unknown run id {run_id!r} (see `runs`)")
    jpath = journal_dir() / f"{run_id}.jsonl"
    entries = [json.loads(l) for l in jpath.read_text(encoding="utf-8").splitlines() if l.strip()]
    results = []
    for e in reversed(entries):
        res = {"op": f"undo-{e.get('op')}", "src": e.get("dst"), "dst": e.get("src"), "status": "ok", "msg": ""}
        # The journal is a file on disk: re-check every path it names before acting on it.
        if e.get("op") == "mkdir":
            denied = check_path(Path(e["dst"]), "folder")
        elif e.get("op") == "move":
            denied = check_path(Path(e["src"]), "destination") or check_path(Path(e["dst"]), "source")
        elif e.get("op") == "command":
            res["status"], res["msg"] = "skipped", f"{' '.join(e.get('command') or [])} can't be undone"
            results.append(res)
            continue
        elif e.get("op") == "trash":
            if e.get("cleanup"):
                p = _abs(Path(e["src"]))
                denied = None if _key(p.parent) in {_key(_abs(r)) for v in cleanup_roots().values() for r in v} \
                    or _key(p) in {_key(_abs(r)) for r in cleanup_roots()["cache_whole"]} \
                    or any(_key(p) == _key(_abs(Path(t["path"]))) for t in _tool_caches(load_config()) if t.get("path")) \
                    else "not a known cache or app-data location"
            else:
                denied = check_path(Path(e["src"]), "destination")
            if not denied and e.get("dst"):
                tdir = _trash_files_dir(e.get("backend"))
                if tdir is None or not _inside(_abs(Path(e["dst"])), _abs(tdir).resolve()):
                    denied = "trashed item is not in the Trash"
        else:
            denied = f"unknown journal op {e.get('op')!r}"
        if denied:
            res["status"], res["msg"] = "error", f"refused: {denied}"
            results.append(res)
            continue
        try:
            if e["op"] == "mkdir":
                d = Path(e["dst"])
                if d.exists() and not [n for n in os.listdir(d) if n not in SKIP_NAMES]:
                    for n in os.listdir(d):
                        (d / n).unlink()
                    d.rmdir()
                else:
                    res["status"], res["msg"] = "skipped", "folder not empty or gone"
            elif e["op"] == "trash":
                res["status"], res["msg"] = _backend_by_name(e.get("backend")).restore(e)
            else:
                res["status"], res["msg"] = _move_back(Path(e["dst"]), Path(e["src"]))
        except Exception as ex:
            res["status"], res["msg"] = "error", str(ex)
        results.append(res)
    jpath.rename(jpath.with_suffix(".undone"))
    return results


# ---------- scan ----------

def _sha1(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > HASH_LIMIT:
            return None
        h = hashlib.sha1()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _dir_size(path: Path) -> Tuple[int, bool]:
    size, truncated, _ = _dir_stats(path)
    return size, truncated


def _dir_stats(path: Path) -> Tuple[int, bool, float]:
    """Total size, whether the walk was cut short, and the newest file use (atime/mtime) inside."""
    total, count, newest = 0, 0, 0.0
    for root, dirs, files in os.walk(path):
        for f in files:
            count += 1
            if count > SIZE_WALK_LIMIT:
                return total, True, newest
            try:
                st = os.lstat(os.path.join(root, f))
                total += st.st_size
                newest = max(newest, st.st_mtime, st.st_atime)
            except OSError:
                pass
    return total, False, newest


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _last_used(st, inner_newest: float = 0.0) -> float:
    """Best guess of when an item was last opened: newest of atime, mtime (and contents for folders).
    atime can be coarse (relatime, or disabled on Windows), so it errs towards "recent"."""
    return max(st.st_mtime, st.st_atime, inner_newest)


def _mdls(path: Path) -> Tuple[Optional[str], List[str]]:
    kind, froms, _ = _mdls_full(path)
    return kind, froms


def _mdls_full(path: Path) -> Tuple[Optional[str], List[str], Optional[float]]:
    """kind, where-from URLs and Spotlight's last-opened date (macOS)."""
    try:
        out = _run(["mdls", "-name", "kMDItemKind", "-name", "kMDItemWhereFroms",
                    "-name", "kMDItemLastUsedDate", str(path)],
                   capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None, [], None
    kind = None
    m = re.search(r'kMDItemKind\s*=\s*"([^"]*)"', out)
    if m:
        kind = m.group(1)
    froms = []
    m = re.search(r"kMDItemWhereFroms\s*=\s*\((.*?)\)", out, re.S)
    if m:
        froms = re.findall(r'"([^"]*)"', m.group(1))
    used = None
    m = re.search(r"kMDItemLastUsedDate\s*=\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([+-]\d{4})", out)
    if m:
        try:
            used = datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            used = None
    return kind, froms, used


def _windows_zone_url(path: Path) -> List[str]:
    """Download URL from the Zone.Identifier alternate data stream (Windows)."""
    try:
        with open(str(path) + ":Zone.Identifier", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        return re.findall(r"^(?:HostUrl|ReferrerUrl)=(.*)$", txt, re.M)
    except OSError:
        return []


def _is_code_project(path: Path) -> bool:
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    if names & CODE_MARKERS:
        return True
    return any(n.endswith((".xcodeproj", ".sln", ".csproj")) for n in names)


def _is_hidden(p: Path) -> bool:
    if p.name.startswith(".") or p.name in SKIP_NAMES:
        return True
    if platform_name() == "windows" and not test_mode():
        try:
            import stat
            return bool(p.stat().st_file_attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
        except (AttributeError, OSError):
            return False
    return False


def scan(paths: List[Path], metadata: bool = True) -> List[dict]:
    items: List[dict] = []
    plat = platform_name()
    try:
        cfg = load_config()
        policies = {_key(_abs(r).resolve()): pol for _, r, pol in managed_roots(cfg)}
    except ConfigError:
        cfg, policies = validate_config({}), {}
    large = cfg["archive"]["large_mb"] * 1024 * 1024
    stale_days = cfg["archive"]["stale_days"]
    now = datetime.now().timestamp()
    for base in paths:
        if not base.exists():
            continue
        policy = policies.get(_key(_abs(base).resolve()))
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except PermissionError:
            items.append({"path": str(base), "error": "no_access"})
            continue
        sibling_names = {p.name for p in entries}
        for p in entries:
            if _is_hidden(p):
                continue
            try:
                st = p.lstat()
            except OSError:
                continue
            is_dir = p.is_dir() and not p.is_symlink()
            flags: List[str] = []
            item = {
                "path": str(p), "name": p.name, "location": base.name, "is_dir": is_dir,
                "ext": "" if is_dir else p.suffix.lower(),
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "size": st.st_size, "sha1": None, "kind": None, "where_from": [],
                "flags": flags, "duplicate_of": None, "policy": policy,
                "last_used": None,
            }
            inner_newest = 0.0
            if is_dir:
                item["size"], truncated, inner_newest = _dir_stats(p)
                if truncated:
                    item["size_truncated"] = True
                if _is_code_project(p):
                    flags.append("code_project")
                try:
                    if not [n for n in os.listdir(p) if n not in SKIP_NAMES]:
                        flags.append("empty_dir")
                except OSError:
                    pass
            else:
                if p.name.startswith("~$") or item["ext"] in TEMP_EXT:
                    flags.append("temp")
                else:
                    item["sha1"] = _sha1(p)
                if item["ext"] in INSTALLER_EXT:
                    flags.append("installer")
                if item["ext"] in ARCHIVE_EXT:
                    stem = p.name[: -len(p.suffix)]
                    if stem in sibling_names and (base / stem).is_dir():
                        flags.append("archive_extracted")
            if SCREENSHOT_RE.match(p.name):
                flags.append("screenshot")
            elif not is_dir and UNCLEAR_NAME_RE.match(p.stem if p.suffix else p.name):
                flags.append("unclear_name")
            used = _last_used(st, inner_newest)
            if metadata and not test_mode():
                if plat == "macos":
                    item["kind"], item["where_from"], spot = _mdls_full(p)
                    if spot:
                        used = max(spot, st.st_mtime)   # Spotlight knows real opens; atime may not
                elif plat == "windows" and not is_dir:
                    item["where_from"] = _windows_zone_url(p)
            item["last_used"] = _iso(used)
            if large and item["size"] >= large:
                flags.append("large")
            if stale_days and now - used >= stale_days * 86400:
                flags.append("stale")
            items.append(item)

    # identical files: keep the copy that already lives in Documents (or the first seen)
    by_hash: dict = {}
    for it in items:
        if it.get("sha1"):
            by_hash.setdefault(it["sha1"], []).append(it)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        dest = _key(_abs(destination_dir()).resolve())
        keeper = next((g for g in group if _key(_abs(Path(g["path"])).resolve()).startswith(dest)), group[0])
        for g in group:
            if g is not keeper:
                g["flags"].append("duplicate")
                g["duplicate_of"] = keeper["path"]
    return items


# ---------- system cleanup ----------
#
# Read-only `cleanup-scan` finds reclaimable space OUTSIDE the user's folders: Docker leftovers,
# package-manager caches, app caches, data of apps that are no longer installed, empty folders.
# `cleanup-apply` takes only item ids, rescans, and acts on what the fresh scan found: a plan
# can never name an arbitrary path or command. Paths go to the Trash (undoable); tool caches
# and Docker are cleaned with the tool's own command (permanent, but the data is re-creatable).

CLEANUP_CATEGORIES = ("docker", "tool", "cache", "leftover", "empty")
CLEANUP_WALK_LIMIT = 300000
DOCKER_SIZE_RE = re.compile(r"([\d.]+)\s*([kKMGT]?i?B)")
_which = shutil.which  # patched in tests

# Folders that belong to the OS / desktop environment, never reported as leftovers.
LEFTOVER_IGNORE = {
    "macos": {"addressbook", "callhistorydb", "callhistorytransactions", "clouddocs", "crashreporter",
              "dock", "fileprovider", "icloud", "knowledge", "mobilesync", "syncservices", "accounts",
              "animoji", "applepushservice", "cloudkit", "dmd", "familycircle", "homeenergyd", "networkserviceproxy",
              "quicklook", "siri", "spotlight", "contextstoreagent", "com.apple.sharedfilelist", "assistant",
              "photos", "facetime", "maps", "messages", "music", "safari", "tv", "podcasts", "books", "news",
              "stocks", "voicememos", "weather", "freeform", "notes", "reminders", "calendar", "mail", "keynote",
              "pages", "numbers", "imovie", "garageband", "xcode", "claude"},
    "linux": {"trash", "applications", "icons", "fonts", "mime", "keyrings", "gnome-shell", "dconf", "systemd",
              "autostart", "pulse", "gtk-2.0", "gtk-3.0", "gtk-4.0", "nautilus", "flatpak", "containers", "pki",
              "gvfs-metadata", "evolution", "tracker", "tracker3", "ibus", "menus", "kdeconnect", "plasma",
              "kde", "kded5", "kded6", "akonadi", "baloo", "dolphin", "konsole", "kwin", "session", "xorg",
              "recently-used.xbel", "user-dirs.dirs", "user-dirs.locale", "mimeapps.list", "monitors.xml",
              "goa-1.0", "gnome-control-center", "gnome-settings-daemon", "enchant", "sounds", "themes",
              "backgrounds", "wireplumber", "pipewire", "environment.d", "fontconfig", "xdg-desktop-portal",
              "desktop-directories", "vlc", "claude", "organize", "nvim", "vim", "git", "fish", "zsh", "bash",
              "htop", "ssh", "gnupg", "python_keyring", "keyring", "kwalletd", "nano", "less"},
    "windows": {"microsoft", "packages", "temp", "programs", "comms", "connecteddevicesplatform", "d3dscache",
                "crashdumps", "publishers", "microsoft_corporation", "windowsapps", "virtualstore", "history",
                "placeholdertilelogofolder", "peerdistrepub", "fontcache", "iconcache.db", "claude", "npm",
                "npm-cache", "pip", "python", "code", "github"},
}
# Common folder name -> words of the app that owns it, when they differ.
LEFTOVER_ALIASES = {"code": ["visual studio code", "vscode"], "google": ["chrome"], "jetbrains": ["intellij", "pycharm",
                    "webstorm", "clion", "goland", "rider", "phpstorm", "datagrip", "android studio"],
                    "mozilla": ["firefox", "thunderbird"], "chromium": ["chromium", "chrome"],
                    "microsoft edge": ["edge"], "bravesoftware": ["brave"], "slack": ["slack"]}


def _parse_size(text: str) -> int:
    m = DOCKER_SIZE_RE.search(text or "")
    if not m:
        return 0
    num, unit = float(m.group(1)), m.group(2).upper().replace("I", "")
    return int(num * {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}.get(unit, 1))


def _walk_size(path: Path, limit: int = CLEANUP_WALK_LIMIT) -> Tuple[int, bool, float]:
    """Size, truncated?, newest file modification inside (a folder's own mtime only if it has no files:
    it changes whenever an entry is added or removed, so it says little about use)."""
    total, count, newest = 0, 0, 0.0
    try:
        st = path.lstat()
        if not path.is_dir() or path.is_symlink():
            return st.st_size, False, max(st.st_mtime, st.st_atime)
    except OSError:
        return 0, False, 0.0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for f in files:
            count += 1
            if count > limit:
                return total, True, newest
            try:
                st = os.lstat(os.path.join(root, f))
                total += st.st_size
                newest = max(newest, st.st_mtime)
            except OSError:
                pass
    return total, False, newest or st.st_mtime


def _has_files(path: Path) -> bool:
    for _root, _dirs, files in os.walk(path):
        if [f for f in files if f not in SKIP_NAMES]:
            return True
    return False


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _local_appdata() -> Path:
    if platform_name() == "windows" and not test_mode() and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"])
    return home() / "AppData" / "Local"


def _roaming_appdata() -> Path:
    if platform_name() == "windows" and not test_mode() and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"])
    return home() / "AppData" / "Roaming"


def cleanup_roots() -> dict:
    """Where each kind of clutter lives on this OS: {"cache": [dirs], "leftover": [dirs]}.
    Items are the direct children of these folders."""
    h, plat = home(), platform_name()
    if plat == "macos":
        lib = h / "Library"
        return {"cache": [lib / "Caches", lib / "Developer" / "Xcode" / "iOS DeviceSupport"],
                "cache_whole": [lib / "Developer" / "Xcode" / "DerivedData"],
                "leftover": [lib / "Application Support"]}
    if plat == "windows":
        return {"cache": [_local_appdata() / "Temp"], "cache_whole": [],
                "leftover": [_roaming_appdata(), _local_appdata()]}
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    xdg_conf = os.environ.get("XDG_CONFIG_HOME")
    xdg_data = os.environ.get("XDG_DATA_HOME")
    pick = lambda env, default: Path(env) if env and not test_mode() else default
    return {"cache": [pick(xdg_cache, h / ".cache")], "cache_whole": [],
            "leftover": [pick(xdg_conf, h / ".config"), pick(xdg_data, h / ".local" / "share")]}


def _never_clean() -> List[Path]:
    h = _abs(home()).resolve()
    out = [h / ".ssh", h / ".gnupg", h / ".claude", h / ".organize", skill_dir(),
           h / "Library" / "Keychains", h / "Library" / "Mobile Documents", h / "Library" / "CloudStorage",
           h / ".local" / "share" / "keyrings", _roaming_appdata() / "Microsoft", _local_appdata() / "Microsoft"]
    return [_abs(p) for p in out]


def check_cleanup_path(p: Path, cfg: Optional[dict] = None) -> Optional[str]:
    """A cleanup target must be a direct child of a known cache/leftover folder (or a whole-cache
    folder / tool cache dir), not a symlink, not a protected or sensitive location."""
    cfg = cfg or load_config()
    a = _abs(p)
    if a.is_symlink():
        return "is a symlink"
    roots = cleanup_roots()
    parents = [_abs(r) for r in roots["cache"] + roots["leftover"]]
    wholes = [_abs(r) for r in roots["cache_whole"]] + [_abs(Path(t["path"])) for t in _tool_caches(cfg) if t.get("path")]
    ok = any(_key(a.parent) == _key(r) for r in parents) or any(_key(a) == _key(w) for w in wholes)
    if not ok:
        return "not a known cache or app-data location"
    for bad in _never_clean() + [_abs(_expand(x)) for x in cfg["protected"]]:
        if _inside(a, bad) or _inside(bad, a):
            return f"protected location ({bad.name})"
    return None


def _installed_apps() -> List[str]:
    """Normalized names (and bundle ids) of installed applications."""
    plat, names = platform_name(), set()
    if plat == "macos":
        dirs = [home() / "Applications"] if test_mode() else \
            [Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"), home() / "Applications"]
        for d in dirs:
            try:
                for app in d.iterdir():
                    if app.suffix == ".app":
                        names.add(_norm(app.stem))
                        try:
                            import plistlib
                            with open(app / "Contents" / "Info.plist", "rb") as f:
                                info = plistlib.load(f)
                            for k in ("CFBundleIdentifier", "CFBundleName"):
                                if info.get(k):
                                    names.add(_norm(info[k]))
                                    names.add(_norm(str(info[k]).split(".")[-1]))
                        except Exception:
                            pass
            except OSError:
                pass
    elif plat == "windows":
        if not test_mode():
            try:
                import winreg  # type: ignore
                keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
                for hive, path in keys:
                    try:
                        k = winreg.OpenKey(hive, path)
                    except OSError:
                        continue
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            sub = winreg.OpenKey(k, winreg.EnumKey(k, i))
                            for v in ("DisplayName", "Publisher"):
                                try:
                                    names.add(_norm(winreg.QueryValueEx(sub, v)[0]))
                                except OSError:
                                    pass
                        except OSError:
                            pass
            except Exception:
                pass
        pf = [home() / "Program Files"] if test_mode() else \
            [Path(os.environ.get(v, "")) for v in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
        for d in pf + [_local_appdata() / "Programs"]:
            try:
                names.update(_norm(x.name) for x in d.iterdir())
            except OSError:
                pass
    else:
        app_dirs = [home() / ".local" / "share" / "applications", home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications"]
        if not test_mode():
            app_dirs += [Path("/usr/share/applications"), Path("/usr/local/share/applications"),
                         Path("/var/lib/flatpak/exports/share/applications"), Path("/var/lib/snapd/desktop/applications")]
        for d in app_dirs:
            try:
                for f in d.glob("*.desktop"):
                    names.add(_norm(f.stem))
                    names.add(_norm(f.stem.split(".")[-1]))
                    try:
                        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                            if line.startswith(("Name=", "Exec=")):
                                val = line.split("=", 1)[1].split()[0] if line.startswith("Exec=") else line.split("=", 1)[1]
                                names.add(_norm(Path(val).name))
                    except OSError:
                        pass
            except OSError:
                pass
        try:
            names.update(_norm(x.name.split(".")[-1]) for x in (home() / ".var" / "app").iterdir())
        except OSError:
            pass
    names.discard("")
    return sorted(names)


def _is_installed(folder_name: str, installed: List[str]) -> bool:
    n = _norm(folder_name)
    cands = {n, _norm(folder_name.split(".")[-1])}
    for alias in LEFTOVER_ALIASES.get(folder_name.casefold(), []):
        cands.add(_norm(alias))
    for c in cands:
        if not c:
            continue
        if _which(folder_name) or _which(folder_name.casefold()):
            return True
        for app in installed:
            if c == app or (len(c) >= 4 and (c in app or app in c)):
                return True
    return False


def _tool_caches(cfg: dict) -> List[dict]:
    """Package-manager caches cleaned with the tool's own command."""
    h, plat = home(), platform_name()
    lib_caches = h / "Library" / "Caches"
    if plat == "macos":
        pip_dir, go_dir = lib_caches / "pip", lib_caches / "go-build"
    elif plat == "windows":
        pip_dir, go_dir = _local_appdata() / "pip" / "Cache", _local_appdata() / "go-build"
    else:
        pip_dir, go_dir = h / ".cache" / "pip", h / ".cache" / "go-build"
    specs = [
        ("npm", "npm cache", h / ".npm" / "_cacache" if plat != "windows" else _local_appdata() / "npm-cache",
         ["npm", "cache", "clean", "--force"]),
        ("pip", "pip cache", pip_dir, ["pip3" if _which("pip3") else "pip", "cache", "purge"]),
        ("yarn", "Yarn cache", (lib_caches / "Yarn") if plat == "macos" else (h / ".cache" / "yarn"), ["yarn", "cache", "clean"]),
        ("go", "Go build cache", go_dir, ["go", "clean", "-cache"]),
        ("brew", "Homebrew downloads", lib_caches / "Homebrew", ["brew", "cleanup", "--prune=all"]),
    ]
    out = []
    for tool, label, path, cmd in specs:
        if tool == "brew" and plat != "macos" and not _which("brew"):
            continue
        if not _which(cmd[0]):
            continue
        out.append({"tool": tool, "label": label, "path": str(path) if path.exists() else None, "command": cmd})
    return out


def _docker_items() -> List[dict]:
    if not _which("docker"):
        return []
    try:
        df = _run(["docker", "system", "df", "--format", "{{json .}}"], capture_output=True, text=True, timeout=30)
    except Exception as e:
        return [{"id": "docker:error", "category": "docker", "name": "Docker", "error": str(e)}]
    if df.returncode != 0:
        return [{"id": "docker:error", "category": "docker", "name": "Docker",
                 "error": (df.stderr or "docker is not running").strip()[:200]}]
    rows = {}
    for line in df.stdout.splitlines():
        try:
            r = json.loads(line)
            rows[r.get("Type", "")] = r
        except json.JSONDecodeError:
            pass
    items = []

    def add(id_, name, size, cmd, risk, note, count=None):
        items.append({"id": id_, "category": "docker", "name": name, "size": size, "count": count,
                      "action": "command", "command": cmd, "permanent": True, "risk": risk, "note": note})

    try:
        dangling = [l for l in _run(["docker", "images", "-f", "dangling=true", "--format", "{{.Size}}"],
                                    capture_output=True, text=True, timeout=30).stdout.splitlines() if l.strip()]
    except Exception:
        dangling = []
    if dangling:
        add("docker:dangling-images", "Untagged (dangling) images", sum(_parse_size(x) for x in dangling),
            ["docker", "image", "prune", "-f"], "low", "Leftover layers from old builds; no container uses them.", len(dangling))
    img = rows.get("Images")
    if img and _parse_size(img.get("Reclaimable", "")):
        add("docker:unused-images", "All images not used by any container", _parse_size(img.get("Reclaimable", "")),
            ["docker", "image", "prune", "-a", "-f"], "medium",
            "They will be downloaded/built again when needed (can take time and bandwidth).")
    cont = rows.get("Containers")
    if cont and _parse_size(cont.get("Reclaimable", "")) or (cont and str(cont.get("TotalCount")) != str(cont.get("Active"))):
        add("docker:stopped-containers", "Stopped containers", _parse_size(cont.get("Reclaimable", "")),
            ["docker", "container", "prune", "-f"], "medium", "Anything saved inside a stopped container is lost.")
    bc = rows.get("Build Cache")
    if bc and _parse_size(bc.get("Reclaimable", "")):
        add("docker:build-cache", "Build cache", _parse_size(bc.get("Reclaimable", "")),
            ["docker", "builder", "prune", "-f"], "low", "Next builds will be slower once.")
    try:
        vols = [v.strip() for v in _run(["docker", "volume", "ls", "-f", "dangling=true", "--format", "{{.Name}}"],
                                        capture_output=True, text=True, timeout=30).stdout.splitlines() if v.strip()]
    except Exception:
        vols = []
    for v in vols:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", v):
            add(f"docker:volume:{v}", f"Unused volume {v}", None, ["docker", "volume", "rm", v], "high",
                "Volumes hold data (databases, uploads). Only remove if you know it's not needed.")
    return items


def cleanup_scan(cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    c = cfg["cleanup"]
    cats = set(c["categories"])
    min_size = c["min_size_mb"] * 1024 * 1024
    left_age = c["leftover_min_age_days"] * 86400
    now = datetime.now().timestamp()
    items: List[dict] = []
    tools = _tool_caches(cfg)
    tool_dirs = [_abs(Path(t["path"])) for t in tools if t.get("path")]

    if "docker" in cats:
        items += [d for d in _docker_items() if d.get("error") or d.get("size") is None or d["size"] >= min_size
                  or d["id"] == "docker:dangling-images"]
    if "tool" in cats:
        for t in tools:
            size, trunc, newest = _walk_size(Path(t["path"])) if t.get("path") else (0, False, 0.0)
            if size >= min_size:
                items.append({"id": f"tool:{t['tool']}", "category": "tool", "name": t["label"], "path": t.get("path"),
                              "size": size, "size_truncated": trunc, "action": "command", "command": t["command"],
                              "permanent": True, "risk": "low", "note": "Re-downloaded automatically when needed."})
    roots = cleanup_roots()
    installed = _installed_apps() if "leftover" in cats else []
    ignore = LEFTOVER_IGNORE.get(platform_name(), set())

    def entries(root: Path):
        try:
            return sorted(root.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            return []

    if "cache" in cats:
        for root in roots["cache"]:
            for e in entries(root):
                if e.is_symlink() or e.name in SKIP_NAMES or any(_inside(_abs(e), td) or _inside(td, _abs(e)) for td in tool_dirs):
                    continue
                if platform_name() == "macos" and e.name.startswith("com.apple."):
                    continue
                if check_cleanup_path(e, cfg):
                    continue
                size, trunc, newest = _walk_size(e)
                if size < min_size:
                    continue
                if platform_name() == "windows" and now - newest < 7 * 86400:
                    continue     # Windows Temp: only items untouched for a week
                items.append({"id": f"cache:{e}", "category": "cache", "name": e.name, "path": str(e), "size": size,
                              "size_truncated": trunc, "last_used": _iso(newest) if newest else None,
                              "action": "trash", "permanent": False, "risk": "low",
                              "note": "App cache; the app rebuilds it. Close the app first."})
        for w in roots["cache_whole"]:
            if w.exists() and not check_cleanup_path(w, cfg):
                size, trunc, newest = _walk_size(w)
                if size >= min_size:
                    items.append({"id": f"cache:{w}", "category": "cache", "name": w.name, "path": str(w), "size": size,
                                  "size_truncated": trunc, "last_used": _iso(newest) if newest else None,
                                  "action": "trash", "permanent": False, "risk": "low", "note": "Build cache; rebuilt on next build."})

    if cats & {"leftover", "empty"}:
        for root in roots["leftover"]:
            for e in entries(root):
                if (not e.is_dir() or e.is_symlink() or e.name.startswith(".") or e.name in SKIP_NAMES
                        or e.name.casefold() in ignore or _norm(e.name) in {_norm(x) for x in ignore}
                        or check_cleanup_path(e, cfg)):
                    continue
                if platform_name() == "macos" and e.name.startswith("com.apple."):
                    continue
                if any(_key(_abs(e)) == _key(_abs(r)) for r in roots["cache"]):
                    continue
                if "empty" in cats and not _has_files(e):
                    items.append({"id": f"empty:{e}", "category": "empty", "name": e.name, "path": str(e), "size": 0,
                                  "action": "trash", "permanent": False, "risk": "low", "note": "Empty folder."})
                    continue
                if "leftover" not in cats or _is_installed(e.name, installed):
                    continue
                size, trunc, newest = _walk_size(e)
                if left_age and now - newest < left_age:
                    continue
                items.append({"id": f"leftover:{e}", "category": "leftover", "name": e.name, "path": str(e),
                              "size": size, "size_truncated": trunc, "last_used": _iso(newest) if newest else None,
                              "action": "trash", "permanent": False, "risk": "medium",
                              "note": "No installed app matches this name (best guess). May hold settings or data."})
    items.sort(key=lambda i: (CLEANUP_CATEGORIES.index(i["category"]), -(i.get("size") or 0)))
    return {"categories": sorted(cats, key=CLEANUP_CATEGORIES.index), "min_size_mb": c["min_size_mb"],
            "total_size": sum(i.get("size") or 0 for i in items), "items": items}


def cleanup_apply(ids: List[str]) -> Tuple[str, List[dict]]:
    """Clean the given item ids. Only ids present in a fresh scan are acted on."""
    cfg = json.loads(json.dumps(load_config()))
    # rescan exactly the categories the ids belong to (they may come from a one-off --categories scan)
    cfg["cleanup"]["categories"] = sorted({i.split(":", 1)[0] for i in ids} & set(CLEANUP_CATEGORIES))
    fresh = {i["id"]: i for i in cleanup_scan(cfg)["items"] if not i.get("error")}
    run_id = _new_run_id()
    results: List[dict] = []
    trash = trash_backend()
    with (journal_dir() / f"{run_id}.jsonl").open("a", encoding="utf-8") as jf:
        def log(entry):
            jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
            jf.flush()
        for id_ in ids:
            item = fresh.get(id_)
            res = {"id": id_, "status": "ok", "msg": ""}
            if item is None:
                res["status"], res["msg"] = "error", "not found in a fresh scan (already cleaned, or not a cleanup item)"
                results.append(res)
                continue
            try:
                if item["action"] == "trash":
                    p = Path(item["path"])
                    denied = check_cleanup_path(p, cfg)
                    if denied:
                        res["status"], res["msg"] = "error", f"refused: {denied}"
                    else:
                        info = trash.put(_abs(p))
                        log({"op": "trash", "cleanup": True, "backend": trash.name, "src": str(_abs(p)), **info})
                        res["msg"] = "moved to the Trash"
                else:
                    cp = _run(item["command"], capture_output=True, text=True, timeout=1800)
                    out = ((cp.stdout or "") + (cp.stderr or "")).strip()
                    log({"op": "command", "id": id_, "command": item["command"], "returncode": cp.returncode})
                    if cp.returncode != 0:
                        res["status"] = "error"
                    res["msg"] = out[-400:]
            except Exception as e:
                res["status"], res["msg"] = "error", str(e)
            res["freed_estimate"] = item.get("size")
            results.append(res)
    return run_id, results


# ---------- big & forgotten files ----------

def report(top: Optional[int] = None) -> dict:
    """Biggest files (code projects count as one item) across the managed folders, and the
    biggest ones not used for `stale_days`. Read-only; hidden items, cloud-sync roots and
    protected paths are not walked."""
    cfg = load_config()
    top = top or cfg["archive"]["top"]
    stale_s = cfg["archive"]["stale_days"] * 86400
    now = datetime.now().timestamp()
    protected = [_abs(_expand(x)) for x in cfg["protected"]]
    biggest: list = []
    stale: list = []
    seen = 0
    truncated = False
    total = 0

    def push(heap, entry):
        item = (entry["size"], entry["path"], entry)
        if len(heap) < top:
            heapq.heappush(heap, item)
        elif item[:2] > heap[0][:2]:
            heapq.heapreplace(heap, item)

    def add(path: Path, size: int, used: float, kind: str, root_name: str):
        nonlocal total
        total += size
        e = {"path": str(path), "size": size, "last_used": _iso(used), "kind": kind, "location": root_name}
        push(biggest, e)
        if stale_s and now - used >= stale_s:
            push(stale, e)

    for name, root, _ in managed_roots(cfg):
        if not root.exists():
            continue
        for cur, dirs, files in os.walk(root):
            curp = Path(cur)
            keep_dirs = []
            for d in sorted(dirs):
                dp = curp / d
                if (_is_hidden(dp) or dp.is_symlink() or any(_inside(dp, pp) for pp in protected)
                        or (curp == root and CLOUD_ROOT_RE.match(d))):
                    continue
                if _is_code_project(dp):
                    size, _, newest = _dir_stats(dp)
                    try:
                        add(dp, size, _last_used(dp.lstat(), newest), "code_project", root.name)
                    except OSError:
                        pass
                    continue
                keep_dirs.append(d)
            dirs[:] = keep_dirs
            for f in files:
                seen += 1
                if seen > REPORT_WALK_LIMIT:
                    truncated = True
                    break
                fp = curp / f
                if _is_hidden(fp) or any(_inside(fp, pp) for pp in protected):
                    continue
                try:
                    st = fp.lstat()
                except OSError:
                    continue
                add(fp, st.st_size, _last_used(st), "file", root.name)
            if truncated:
                break
        if truncated:
            break

    def done(heap):
        return [e for _, _, e in sorted(heap, key=lambda t: t[:2], reverse=True)]

    return {"top": top, "large_mb": cfg["archive"]["large_mb"], "stale_days": cfg["archive"]["stale_days"],
            "files_scanned": seen, "total_size": total, "truncated": truncated,
            "biggest": done(biggest), "biggest_stale": done(stale)}


# ---------- file names ----------

def _clean_name(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(?:\s*-\s*){2,}", " - ", s)          # "a -  - b" left by an empty token
    s = re.sub(r"(?:\s*_\s*){2,}", "_", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    return s.strip(" -_.")


def render_name(pattern: str, src: Path, fields: dict) -> str:
    """File name for `src` following `pattern`. fields: title (required), date
    (YYYY-MM-DD / YYYY-MM / YYYY; default: file's modification date), category."""
    title = _clean_name(str(fields.get("title") or ""))
    if not title:
        raise ValueError("missing title")
    date = str(fields.get("date") or "")
    if date and not re.fullmatch(r"\d{4}(-\d\d(-\d\d)?)?", date):
        raise ValueError(f"bad date {date!r} (use YYYY-MM-DD, YYYY-MM or YYYY)")
    if not date:
        date = datetime.fromtimestamp(src.lstat().st_mtime).strftime("%Y-%m-%d")
    values = {"title": title, "date": date, "month": date[:7] if len(date) >= 7 else date,
              "year": date[:4], "category": _clean_name(str(fields.get("category") or "")),
              "original": _clean_name(src.stem if src.suffix else src.name)}
    stem = _clean_name(re.sub(r"{(\w+)}", lambda m: values.get(m.group(1), ""), pattern))
    stem = stem[:120].rstrip(" -_.") or title[:120]
    if WIN_RESERVED.match(stem):
        stem += "_"
    return stem + (src.suffix if src.suffix else "")


def render_names(items: List[dict], pattern: Optional[str] = None) -> List[dict]:
    """Proposed renames for a batch, without touching anything. Names are unique within the
    batch and against existing files; the plan still goes through `apply` (and its checks)."""
    pattern = pattern if pattern is not None else load_config()["rename_files"]["pattern"]
    if not pattern:
        raise ConfigError("renaming is off (rename_files.pattern is empty)")
    validate_config({"rename_files": {"pattern": pattern, "scope": "unclear"}})
    out, planned = [], set()
    for it in items:
        try:
            src = _abs(Path(it["src"]))
            if src.is_dir():
                raise ValueError("only files are renamed")
            if not src.exists():
                raise ValueError("no such file")
            folder = _abs(_expand(it["dir"])) if it.get("dir") else src.parent
            name = render_name(pattern, src, it)
            dst = folder / name
            if _key(dst) != _key(src):
                dst = unique_dest(dst, taken=lambda q: _key(q) in planned)
            planned.add(_key(dst))
            out.append({"src": str(src), "dst": str(dst), "name": dst.name, "unchanged": _key(dst) == _key(src)})
        except (ValueError, KeyError, OSError) as e:
            out.append({"src": it.get("src"), "error": str(e)})
    return out


# ---------- project safety ----------

def check_project(path: Path) -> dict:
    """What breaks if this folder moves: virtualenvs (absolute paths) and scheduled jobs pointing at it."""
    path = path.expanduser()
    venvs = []
    for root, dirs, files in os.walk(path):
        if Path(root).relative_to(path).parts.__len__() >= 3:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
        if "pyvenv.cfg" in files:
            venvs.append(root)
    needle = str(path.resolve())
    jobs = []
    plat = platform_name()
    if plat == "macos":
        la = home() / "Library" / "LaunchAgents"
        if la.exists():
            for f in la.glob("*.plist"):
                try:
                    if needle in f.read_text(encoding="utf-8", errors="replace"):
                        jobs.append(f"launchd: {f}")
                except OSError:
                    pass
    elif plat == "linux":
        sd = home() / ".config" / "systemd" / "user"
        if sd.exists():
            for f in sd.iterdir():
                if f.is_file() and needle in f.read_text(encoding="utf-8", errors="replace"):
                    jobs.append(f"systemd: {f}")
    if not test_mode():
        try:
            if plat in ("macos", "linux") and shutil.which("crontab"):
                out = _run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
                jobs += [f"crontab: {l}" for l in out.splitlines() if needle in l]
            elif plat == "windows":
                out = _run(["schtasks", "/query", "/fo", "LIST", "/v"], capture_output=True, text=True,
                           timeout=30).stdout
                current = ""
                for l in out.splitlines():
                    if l.lower().startswith("taskname:"):
                        current = l.split(":", 1)[1].strip()
                    if needle.lower() in l.lower() and current:
                        jobs.append(f"task scheduler: {current}")
        except Exception:
            pass
    return {"path": str(path), "venvs": venvs, "scheduled_jobs": sorted(set(jobs)),
            "safe_to_move": not venvs and not jobs}


# ---------- CLI ----------

NO_ACCESS_HINT = {
    "macos": "Grant Full Disk Access: System Settings > Privacy & Security > Full Disk Access, "
             "enable the app running Claude Code (Terminal / iTerm / VS Code), then restart it.",
    "linux": "Check the permissions of ~/.local/share/Trash.",
    "windows": "PowerShell could not read the Recycle Bin.",
}


def _print(obj) -> None:
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    cf = sub.add_parser("config")
    g = cf.add_mutually_exclusive_group()
    g.add_argument("--init", action="store_true", help="write a starter config if none exists")
    g.add_argument("--write", metavar="FILE", help="validate FILE and save it as the config")
    ob = sub.add_parser("onboarding")
    ob.add_argument("--apply", metavar="ANSWERS", help="turn onboarding answers into the config")
    ob.add_argument("--dry-run", action="store_true", help="with --apply: print the result, don't save")
    s = sub.add_parser("scan")
    s.add_argument("paths", nargs="*")
    s.add_argument("--no-metadata", action="store_true")
    cs = sub.add_parser("cleanup-scan")
    cs.add_argument("--categories", help="comma-separated subset to scan (default: config)")
    ca = sub.add_parser("cleanup-apply")
    ca.add_argument("ids", help="JSON file: list of item ids from cleanup-scan")
    rp = sub.add_parser("report")
    rp.add_argument("--top", type=int)
    rn = sub.add_parser("render-names")
    rn.add_argument("items", help="JSON file: [{src, title, date?, category?, dir?}]")
    rn.add_argument("--pattern", help="override rename_files.pattern (for previews)")
    c = sub.add_parser("check-project")
    c.add_argument("path")
    a = sub.add_parser("apply")
    a.add_argument("plan")
    u = sub.add_parser("undo")
    u.add_argument("run_id", nargs="?")
    sub.add_parser("runs")
    sub.add_parser("trash-list")
    e = sub.add_parser("empty-trash")
    e.add_argument("--yes-really", action="store_true")
    args = ap.parse_args(argv)
    plat = platform_name()
    try:
        cfg = load_config()
    except ConfigError as ex:
        if not ((args.cmd == "config" and (args.write or args.init)) or args.cmd == "onboarding"):
            _print({"error": "config", "detail": str(ex), "config_path": str(config_path())})
            return 3
        cfg = None

    if args.cmd == "info":
        trash = trash_backend()
        try:
            trash.list()
            access = True
        except Exception:
            access = False
        _print({"platform": plat, "home": str(home()),
                "folders": {k: str(v) for k, v in user_folders().items()},
                "system_language": system_language(), "naming_language": naming_language(cfg),
                "managed": [{"name": n, "path": str(p), "policy": pol} for n, p, pol in managed_roots(cfg)],
                "destination": str(destination_dir(cfg)),
                "config_path": str(config_path()), "config_exists": config_path().exists(),
                "onboarding_needed": cfg["onboarding_version"] == 0,
                "onboarding_new_questions": new_onboarding_questions(cfg),
                "trash_backend": trash.name, "trash_readable": access,
                "journal": str(journal_dir())})
    elif args.cmd == "config":
        f = config_path()
        if args.init:
            if f.exists():
                _print({"error": "exists", "config_path": str(f)})
                return 1
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            _print({"written": str(f), "config": validate_config({})})
        elif args.write:
            try:
                raw = json.loads(Path(args.write).read_text(encoding="utf-8"))
                new = validate_config(raw)
            except (ConfigError, json.JSONDecodeError, OSError) as ex:
                _print({"error": "invalid", "detail": str(ex)})
                return 1
            save_config(raw)
            _print({"written": str(f), "config": new})
        else:
            _print({"config_path": str(f), "exists": f.exists(), "config": cfg,
                    "naming_language": naming_language(cfg), "system_language": system_language()})
    elif args.cmd == "onboarding":
        ob_data = load_onboarding()
        if not args.apply:
            _print({"version": ob_data.get("version", 1), "config_exists": config_path().exists(),
                    "system_language": system_language(),
                    "first_time": cfg["onboarding_version"] == 0,
                    "current": cfg, "current_answers": current_answers(cfg) if cfg["onboarding_version"] else {},
                    "new_questions": new_onboarding_questions(cfg), "rounds": ob_data["rounds"]})
            return 0
        base = {}
        if config_path().exists():
            try:
                base = json.loads(config_path().read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                base = {}   # broken config: onboarding replaces it (a .bak is kept)
        try:
            answers = json.loads(Path(args.apply).read_text(encoding="utf-8"))
            raw = onboarding_to_config(answers, base if isinstance(base, dict) else {})
        except (ConfigError, json.JSONDecodeError, OSError) as ex:
            _print({"error": "invalid", "detail": str(ex)})
            return 1
        if not args.dry_run:
            save_config(raw)
        _print({"written": None if args.dry_run else str(config_path()), "config": validate_config(raw)})
    elif args.cmd == "scan":
        paths = [Path(p).expanduser() for p in args.paths] or [r for _, r, _ in managed_roots(cfg)]
        _print(scan(paths, metadata=not args.no_metadata))
    elif args.cmd == "cleanup-scan":
        if args.categories:
            cats = [x.strip() for x in args.categories.split(",") if x.strip()]
            bad = set(cats) - set(CLEANUP_CATEGORIES)
            if bad:
                _print({"error": f"unknown categories: {', '.join(sorted(bad))}"})
                return 1
            cfg = json.loads(json.dumps(cfg))
            cfg["cleanup"]["categories"] = cats
        _print(cleanup_scan(cfg))
    elif args.cmd == "cleanup-apply":
        try:
            ids = json.loads(Path(args.ids).read_text(encoding="utf-8"))
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("expected a JSON list of ids")
        except (ValueError, OSError) as ex:
            _print({"error": str(ex)})
            return 1
        run_id, results = cleanup_apply(ids)
        _print({"run_id": run_id, "results": results})
    elif args.cmd == "report":
        _print(report(args.top))
    elif args.cmd == "render-names":
        try:
            items = json.loads(Path(args.items).read_text(encoding="utf-8"))
            _print(render_names(items, args.pattern))
        except (ConfigError, json.JSONDecodeError, OSError) as ex:
            _print({"error": str(ex)})
            return 1
    elif args.cmd == "check-project":
        _print(check_project(Path(args.path)))
    elif args.cmd == "apply":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        actions = plan["actions"] if isinstance(plan, dict) else plan
        run_id, results = apply_plan(actions)
        _print({"run_id": run_id, "results": results})
    elif args.cmd == "undo":
        try:
            _print(undo(args.run_id))
        except ValueError as ex:
            _print({"error": str(ex)})
            return 1
    elif args.cmd == "runs":
        _print(list_runs())
    elif args.cmd == "trash-list":
        try:
            _print(trash_backend().list())
        except (TrashAccessError, OSError):
            _print({"error": "no_access", "hint": NO_ACCESS_HINT[plat]})
            return 2
    elif args.cmd == "empty-trash":
        if not args.yes_really:
            print("Refusing: emptying the Trash is permanent. Re-run with --yes-really.", file=sys.stderr)
            return 1
        try:
            _print({"emptied": True, "removed": trash_backend().empty()})
        except (TrashAccessError, OSError) as ex:
            _print({"error": "no_access", "hint": NO_ACCESS_HINT[plat], "detail": str(ex)})
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
