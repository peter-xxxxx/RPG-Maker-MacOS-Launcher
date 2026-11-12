"""Standard-library backend for RPG Maker MV/MZ runtime translation.

This module owns engine detection, text extraction, dictionary validation,
per-game install/disable/remove, the path-only cleanup index and the launch
preparation/export helpers used by the launcher UI.

Design notes
------------
* All per-game translation state lives in ``<js>/plugins`` inside the game.
* The launcher Application Support folder only contains a path-only cleanup
  index; no dictionary text is ever cached there.
* Files and plugins.js edits are staged in the destination directory and
  swapped with ``os.replace``.  Previous bytes are kept in memory for rollback.
* Only standard library modules are used.  ``chardet`` is used when it happens
  to be installed, but it is never required.
"""

from __future__ import annotations

import base64
import datetime as _datetime
import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

try:  # optional legacy fallback used by the existing launcher
    import chardet as _chardet  # type: ignore
except Exception:  # pragma: no cover - chardet is optional
    _chardet = None


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

PLUGIN_NAME = "RPGM_Launcher_Translate"
PLUGIN_JS_NAME = "RPGM_Launcher_Translate.js"
DICTIONARY_NAME = "RPGM_Launcher_Translate.json"
CONFIG_NAME = "RPGM_Launcher_Translate.config.json"
STATE_NAME = "RPGM_Launcher_Translate.state.json"
TXN_NAME = "RPGM_Launcher_Translate.txn.json"

DEFAULT_EXPORT_NAME = "ManualTransFile.json"
RESERVED_EXPORT_NAMES = {
    "manualtransfile_result.json",
    "manualtransfile_skipped.json",
    "manualtransfile_unsured_result.json",
}
PLUGINS_BACKUP_NAME = "plugins.js.rpgm-launcher.bak"
_PROTECTED_EXPORT_NAMES = RESERVED_EXPORT_NAMES | {
    DICTIONARY_NAME.casefold(),
    CONFIG_NAME.casefold(),
    STATE_NAME.casefold(),
    PLUGIN_JS_NAME.casefold(),
    TXN_NAME.casefold(),
    (TXN_NAME + ".corrupt").casefold(),
    PLUGINS_BACKUP_NAME.casefold(),
    "plugins.js",
    "package.json",
    "index.html",
    "game.ini",
}

APP_SUPPORT_ENV = "RPGM_LAUNCHER_APP_SUPPORT_DIR"
CLEANUP_INDEX_NAME = "translation_cleanup_index.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "fontFace": "",
    "wrap": True,
    "debug": False,
}

PLUGIN_DESCRIPTION = "RPGM Launcher runtime translation injector"
MAX_WARNING_DETAILS = 40
MAX_UNKNOWN_PREVIEW = 8

# The packaged plugin is normally next to translation.py.
PLUGIN_JS_SOURCE = Path(__file__).resolve().with_name(PLUGIN_JS_NAME)


class TranslationError(Exception):
    """Raised for recoverable translation backend failures."""


# ---------------------------------------------------------------------------
# Data objects
# ---------------------------------------------------------------------------


@dataclass
class GameLayout:
    game_path: Path
    engine: str  # "MV" or "MZ"
    js_dir: Path
    data_dir: Path

    @property
    def plugin_dir(self) -> Path:
        return self.js_dir / "plugins"

    @property
    def index_dir(self) -> Path:
        return self.js_dir.parent

    @property
    def plugins_js(self) -> Path:
        return self.js_dir / "plugins.js"


@dataclass
class ScanResult:
    layout: Optional[GameLayout]
    strings: List[str] = field(default_factory=list)
    block_keys: Set[str] = field(default_factory=set)
    line_keys: Set[str] = field(default_factory=set)
    all_source_keys: Set[str] = field(default_factory=set)
    warnings: List[str] = field(default_factory=list)
    files_scanned: int = 0
    # Extra metadata used to decide whether a block is covered by a line-key
    # (MTool style) dictionary.
    block_lines: Dict[str, List[str]] = field(default_factory=dict)
    # Display-name keys grouped by database/system category, used by the
    # read-only coverage report.  Values are normalized exact strings.
    category_keys: Dict[str, Set[str]] = field(default_factory=dict)


@dataclass
class TranslationData:
    path: Path
    entries: Dict[str, str] = field(default_factory=dict)
    raw_entries: Dict[str, Any] = field(default_factory=dict)
    empty_keys: List[str] = field(default_factory=list)
    identity_keys: List[str] = field(default_factory=list)
    invalid_count: int = 0
    warnings: List[str] = field(default_factory=list)
    ok: bool = True
    error: str = ""

    @property
    def entry_count(self) -> int:
        return len(self.entries)


@dataclass
class ValidationResult:
    matched_count: int = 0
    unknown_count: int = 0
    empty_count: int = 0
    missing_block_count: int = 0
    total_block_count: int = 0
    line_matched_count: int = 0
    code_warnings: List[str] = field(default_factory=list)
    unknown_keys: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_block_keys: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return a readable multi-line validation summary."""

        covered_blocks = max(0, self.total_block_count - self.missing_block_count)
        lines = [
            f"Matched translations: {self.matched_count}",
            f"Unknown keys: {self.unknown_count}",
            f"Empty values ignored: {self.empty_count}",
            (
                "Dialogue blocks covered: "
                f"{covered_blocks}/{self.total_block_count} "
                f"(missing {self.missing_block_count})"
            ),
            f"Line-key matches: {self.line_matched_count}",
        ]
        if self.code_warnings:
            lines.append(
                f"Control-code / placeholder warnings: {len(self.code_warnings)}"
            )
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings:
                lines.append(f"  - {warning}")
        else:
            lines.append("No warnings.")
        return "\n".join(lines)


@dataclass
class InstallResult:
    ok: bool
    message: str = ""
    warnings: List[str] = field(default_factory=list)
    status: str = ""
    installed: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    backup_path: Optional[str] = None
    changed: bool = False
    error: str = ""


@dataclass
class ExportTargetInfo:
    path: Path
    exists: bool = False
    key_count: int = 0
    non_empty_count: int = 0
    size: int = 0
    modified_ns: int = 0
    is_reserved: bool = False
    blocked_reason: str = ""
    error: str = ""


@dataclass
class ExportResult:
    count: int
    path: Path
    backup_path: Optional[Path] = None


@dataclass
class CoverageRow:
    category: str
    total: int = 0
    translated: int = 0
    identity: int = 0
    empty: int = 0
    missing: int = 0
    separators: int = 0
    missing_keys: List[str] = field(default_factory=list)

    @property
    def untranslated(self) -> int:
        return self.identity + self.empty + self.missing


@dataclass
class CompilationResult:
    entries: Dict[str, str] = field(default_factory=dict)
    total_source_count: int = 0
    compiled_source_count: int = 0
    clean_key_count: int = 0
    provided_key_count: int = 0
    matched_key_count: int = 0
    unknown_keys: List[str] = field(default_factory=list)
    unsafe_override_keys: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Clean export keys: {self.clean_key_count}",
            f"Provided translations: {self.provided_key_count}",
            f"Matched to this game: {self.matched_key_count}",
            (
                "Compiled exact strings: "
                f"{self.compiled_source_count}/{self.total_source_count}"
            ),
        ]
        if self.unknown_keys:
            preview = ", ".join(
                _shorten(key, 50) for key in self.unknown_keys[:MAX_UNKNOWN_PREVIEW]
            )
            suffix = "" if len(self.unknown_keys) <= MAX_UNKNOWN_PREVIEW else ", ..."
            lines.append(f"Unknown keys: {len(self.unknown_keys)}: {preview}{suffix}")
        if self.unsafe_override_keys:
            lines.append(
                "Exact entries with code/tag/placeholder mismatches: "
                f"{len(self.unsafe_override_keys)} (compiled from fragments instead)."
            )
        if self.warnings:
            lines.extend(f"Warning: {warning}" for warning in self.warnings)
        return "\n".join(lines)


@dataclass
class LaunchResult:
    status: str  # none | active | disabled | unsupported | error
    active: bool
    message: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class UninstallResult:
    ok: bool
    missing_index: bool = False
    index_error: str = ""
    pruned_count: int = 0
    failures: List[str] = field(default_factory=list)
    cleaned_count: int = 0


@dataclass
class _JsonFile:
    value: Any = None
    exists: bool = False
    error: str = ""


@dataclass
class _ConfigInfo:
    config: Dict[str, Any]
    exists: bool
    valid: bool
    warnings: List[str] = field(default_factory=list)


@dataclass
class _JsDocument:
    path: Path
    raw: bytes
    text: str
    encoding: str
    bom: bytes
    newline: str


@dataclass
class _PluginEntry:
    start: int
    end: int
    name: Optional[str]
    comma_after: Optional[int] = None


@dataclass
class _PluginsAnalysis:
    text: str
    mask: str
    array_start: int
    array_end: int
    entries: List[_PluginEntry]


# ---------------------------------------------------------------------------
# Small text/path helpers
# ---------------------------------------------------------------------------


def _absolute_path(path: Any) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _normalize_text(value: str) -> str:
    """Normalize line endings and Unicode for exact dictionary matching."""

    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", value)


def _is_blank(value: str) -> bool:
    return not value.strip()


def _shorten(value: str, limit: int = 90) -> str:
    text = value.replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _now_iso() -> str:
    return (
        _datetime.datetime.now(_datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf > 0:
        return "\r\n"
    if cr > lf and cr > 0:
        return "\r"
    return "\n"


def app_support_dir() -> Path:
    """Return the launcher Application Support directory.

    ``RPGM_LAUNCHER_APP_SUPPORT_DIR`` is honoured for tests and portable runs.
    """

    override = os.environ.get(APP_SUPPORT_ENV)
    if override:
        return _absolute_path(override)
    return Path.home() / "Library" / "Application Support" / "RPGM-Launcher"


def get_app_support_dir() -> Path:  # convenience alias for UI code
    return app_support_dir()


def cleanup_index_path() -> Path:
    return app_support_dir() / CLEANUP_INDEX_NAME


# ---------------------------------------------------------------------------
# Encoding / JSON / atomic file helpers
# ---------------------------------------------------------------------------

_BOMS: Tuple[Tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

_FALLBACK_ENCODINGS = (
    "utf-8",
    "shift_jis",
    "cp932",
    "gb18030",
    "big5",
    "utf-16",
    "latin-1",
)


def _iter_decodings(raw: bytes) -> Iterator[Tuple[str, str, bytes]]:
    """Yield plausible decodings in preference order.

    A BOM is authoritative.  Without one, UTF-8 is tried first, then optional
    chardet detection, then common CJK encodings.  UTF-16 without a BOM comes
    after the single-byte/double-byte encodings because ASCII-heavy UTF-16
    bytes are often valid UTF-8 (with NULs).
    """

    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            body = raw[len(bom) :]
            try:
                yield body.decode(encoding), encoding, bom
            except UnicodeDecodeError:
                pass
            return

    candidates: List[str] = []
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        candidates.append("utf-8")

    if _chardet is not None:
        try:
            detected = _chardet.detect(raw).get("encoding")
            if detected:
                candidates.append(str(detected))
        except Exception:
            pass
    candidates.extend(_FALLBACK_ENCODINGS)

    seen: Set[str] = set()
    for encoding in candidates:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            yield raw.decode(encoding), encoding, b""
        except (UnicodeDecodeError, LookupError):
            continue

    yield raw.decode("utf-8", errors="replace"), "utf-8", b""


def _decode_bytes(raw: bytes) -> Tuple[str, str, bytes]:
    """Decode bytes, returning ``(text, encoding, bom)``."""

    for text, encoding, bom in _iter_decodings(raw):
        return text, encoding, bom
    return raw.decode("utf-8", errors="replace"), "utf-8", b""


def _load_json_file(path: Path) -> _JsonFile:
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return _JsonFile(exists=False, error="")
    except OSError as exc:
        return _JsonFile(exists=True, error=f"Could not read {path}: {exc}")

    first_error: Optional[Exception] = None
    for text, _encoding, _bom in _iter_decodings(raw):
        try:
            return _JsonFile(value=json.loads(text), exists=True, error="")
        except (json.JSONDecodeError, ValueError) as exc:
            if first_error is None:
                first_error = exc
    return _JsonFile(exists=True, error=f"Invalid JSON in {path}: {first_error}")


def _json_bytes(value: Any) -> bytes:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return (text + "\n").encode("utf-8")


def _write_temp_bytes(target: Path, data: bytes) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except OSError:
            mode = 0o644
        os.chmod(tmp_path, mode)
        return tmp_path
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target = Path(target)
    tmp_path = _write_temp_bytes(target, data)
    try:
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _atomic_write_json(target: Path, value: Any) -> None:
    _atomic_write_bytes(target, _json_bytes(value))


def _restore_bytes(path: Path, old: Optional[bytes]) -> None:
    """Best-effort rollback write that does not depend on os.replace."""

    path = Path(path)
    if old is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Remove first so a previously read-only file mode cannot block rollback.
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    with open(path, "wb") as handle:
        handle.write(old)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def _unlink_file(path: Path) -> bool:
    """Delete a file, treating a missing file as already removed."""

    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False


def _is_regular_file(path: Path) -> bool:
    """Like Path.is_file(), but distinguishes permission errors from absence."""

    try:
        info = Path(path).stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TranslationError(f"Could not inspect {path}: {exc}") from exc
    return stat.S_ISREG(info.st_mode)


def _read_target_bytes(paths: Sequence[Path]) -> Dict[Path, Optional[bytes]]:
    previous: Dict[Path, Optional[bytes]] = {}
    for path in paths:
        try:
            previous[path] = Path(path).read_bytes()
        except FileNotFoundError:
            previous[path] = None
        except OSError as exc:
            raise TranslationError(f"Could not read {path}: {exc}") from exc
    return previous


# ---------------------------------------------------------------------------
# Crash-recoverable install transaction
# ---------------------------------------------------------------------------


def _txn_path(plugin_dir: Path) -> Path:
    return Path(plugin_dir) / TXN_NAME


def _corrupt_txn_path(plugin_dir: Path) -> Path:
    return Path(plugin_dir) / (TXN_NAME + ".corrupt")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lexical_abs(path: Any) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path))))


def _transaction_allowed_targets(layout: GameLayout) -> Set[Path]:
    plugin_dir = layout.plugin_dir
    return {
        _lexical_abs(plugin_dir / PLUGIN_JS_NAME),
        _lexical_abs(plugin_dir / DICTIONARY_NAME),
        _lexical_abs(plugin_dir / CONFIG_NAME),
        _lexical_abs(plugin_dir / STATE_NAME),
        _lexical_abs(layout.plugins_js),
    }


def _is_allowed_transaction_target(
    layout: GameLayout, target: Any, allowed: Optional[Set[Path]] = None
) -> bool:
    """Allow only exact translation artifacts with no symlinked path components.

    The realpath comparison rejects a translation artifact (or any parent)
    that is a symlink to another file, including ``data/*.json`` or a path
    outside the game folder.
    """

    lexical = _lexical_abs(target)
    if allowed is None:
        allowed = _transaction_allowed_targets(layout)
    if lexical not in allowed:
        return False
    try:
        game = _lexical_abs(layout.game_path)
        relative = os.path.relpath(str(lexical), str(game))
    except (OSError, ValueError):
        return False
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return False
    expected = os.path.normpath(os.path.join(os.path.realpath(str(game)), relative))
    actual = os.path.realpath(str(lexical))
    return os.path.normcase(actual) == os.path.normcase(expected)


def _validate_transaction_manifest(layout: GameLayout, manifest: Any) -> List[str]:
    """Validate a transaction manifest before touching any file."""

    errors: List[str] = []
    if not isinstance(manifest, dict):
        return ["Pending translation transaction is not a JSON object."]
    if manifest.get("schema_version") != 1:
        errors.append(
            "Pending translation transaction has an unsupported schema version."
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("Pending translation transaction has no file list.")
        return errors

    allowed = _transaction_allowed_targets(layout)
    seen: Set[Path] = set()
    for index, entry in enumerate(files):
        label = f"transaction file #{index + 1}"
        if not isinstance(entry, dict):
            errors.append(f"{label} is not an object.")
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{label} has no path.")
            continue
        target = _lexical_abs(raw_path)
        if not _is_allowed_transaction_target(layout, target, allowed):
            errors.append(f"{label} is not an allowed translation artifact: {target}")
            continue
        if target in seen:
            errors.append(f"{label} duplicates another target: {target}")
            continue
        seen.add(target)

        existed = entry.get("existed")
        if not isinstance(existed, bool):
            errors.append(f"{label} has an invalid 'existed' flag.")
            continue
        old_data = entry.get("old_data", "")
        if existed:
            if not isinstance(old_data, str):
                errors.append(f"{label} has invalid old data.")
            else:
                try:
                    base64.b64decode(old_data.encode("ascii"), validate=True)
                except Exception:
                    errors.append(f"{label} has invalid base64 old data.")
        elif old_data not in ("", None):
            errors.append(f"{label} has old data for a target that did not exist.")

        expected = entry.get("new_sha256")
        if not isinstance(expected, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", expected
        ):
            errors.append(f"{label} has an invalid new hash.")

    if not errors and seen != allowed:
        missing = ", ".join(sorted(str(path) for path in allowed - seen))
        errors.append(
            f"Pending transaction does not cover all translation artifacts: {missing}"
        )
    return errors


def _write_transaction(
    layout: GameLayout,
    previous: Mapping[Path, Optional[bytes]],
    new_contents: Mapping[Path, bytes],
) -> Path:
    """Write a manifest with old bytes and new hashes before swapping files."""

    allowed = _transaction_allowed_targets(layout)
    files: List[Dict[str, Any]] = []
    for target, old in previous.items():
        lexical = _lexical_abs(target)
        if not _is_allowed_transaction_target(layout, lexical, allowed):
            raise TranslationError(
                f"Refusing to write a transaction for a non-artifact or symlinked path: {target}"
            )
        new_data = new_contents.get(target)
        if new_data is None:
            raise TranslationError(
                f"Missing staged content for transaction target: {target}"
            )
        files.append(
            {
                "path": str(lexical),
                "existed": old is not None,
                "old_data": base64.b64encode(old).decode("ascii")
                if old is not None
                else "",
                "new_sha256": _sha256_bytes(new_data),
            }
        )
    manifest = {"schema_version": 1, "files": files}
    path = _txn_path(layout.plugin_dir)
    _atomic_write_json(path, manifest)
    return path


def _delete_transaction(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _transaction_targets_match_new(
    layout: GameLayout, manifest: Mapping[str, Any]
) -> bool:
    if _validate_transaction_manifest(layout, manifest):
        return False
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return False
    for entry in files:
        target = _lexical_abs(entry.get("path", ""))
        expected = entry.get("new_sha256")
        if not target.is_file():
            return False
        try:
            if _sha256_bytes(target.read_bytes()) != str(expected).lower():
                return False
        except OSError:
            return False
    return True


def _rollback_transaction_manifest(
    layout: GameLayout, manifest: Mapping[str, Any]
) -> List[str]:
    errors = _validate_transaction_manifest(layout, manifest)
    if errors:
        return ["Refusing to restore an invalid translation transaction."] + errors

    warnings: List[str] = []
    files = manifest["files"]
    for entry in files:
        target = _lexical_abs(entry["path"])
        if entry["existed"]:
            try:
                old_data = base64.b64decode(entry["old_data"].encode("ascii"))
                _restore_bytes(target, old_data)
            except Exception as exc:
                warnings.append(f"Could not restore {target}: {exc}")
        else:
            try:
                _unlink_file(target)
            except OSError as exc:
                warnings.append(f"Could not remove {target}: {exc}")
    return warnings


def _recover_pending_install(layout: GameLayout) -> List[str]:
    """Recover a partially completed install before reading/using state."""

    path = _txn_path(layout.plugin_dir)
    if not path.is_file():
        return []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors = [f"Could not read pending translation transaction: {exc}"]
        manifest = None
    else:
        errors = _validate_transaction_manifest(layout, manifest)

    if errors:
        quarantine = _corrupt_txn_path(layout.plugin_dir)
        try:
            os.replace(str(path), str(quarantine))
            return [
                "Invalid pending translation transaction was quarantined; "
                "re-load the translation to repair it."
            ] + errors[:5]
        except OSError as exc:
            return [
                "Invalid pending translation transaction could not be quarantined; "
                f"no files were changed. ({exc})"
            ] + errors[:5]

    # If every file already has the new hash, the previous install completed
    # and only the manifest cleanup was interrupted.
    if _transaction_targets_match_new(layout, manifest):
        try:
            _delete_transaction(path)
            return ["Completed cleanup from an interrupted translation install."]
        except OSError as exc:
            return [f"Could not remove the completed transaction manifest: {exc}"]

    warnings = _rollback_transaction_manifest(layout, manifest)
    if warnings:
        return warnings
    try:
        _delete_transaction(path)
    except OSError as exc:
        warnings.append(f"Could not remove the rolled-back transaction manifest: {exc}")
    warnings.append("Rolled back an interrupted translation install.")
    return warnings


# ---------------------------------------------------------------------------
# Engine detection
# ---------------------------------------------------------------------------


def _read_package_main(
    base: Path, warnings: Optional[List[str]] = None
) -> Optional[str]:
    package_path = base / "package.json"
    info = _load_json_file(package_path)
    if info.error:
        if warnings is not None:
            warnings.append(info.error)
        return None
    if not info.exists or not isinstance(info.value, dict):
        return None
    main = info.value.get("main")
    return main if isinstance(main, str) and main.strip() else None


def detect_game_layout(game_path: Any) -> Optional[GameLayout]:
    """Detect a standard MV/MZ game layout.

    Search order is ``<game>/www`` then ``<game>``.  When both contain a core
    JS file, ``package.json``'s ``main`` is used first; otherwise ``www`` wins.
    A sibling ``data`` directory is required.
    """

    base = _absolute_path(game_path)
    if not base.is_dir():
        return None

    candidates: List[Tuple[Path, Path, str]] = []
    for root in (base / "www", base):
        js_dir = root / "js"
        if not js_dir.is_dir() or not (root / "data").is_dir():
            continue
        if (js_dir / "rmmz_core.js").is_file():
            engine = "MZ"
        elif (js_dir / "rpg_core.js").is_file():
            engine = "MV"
        else:
            continue

        data_dir = root / "data"
        plugin_dir = js_dir / "plugins"
        plugins_js = js_dir / "plugins.js"
        # A malicious or accidental symlink below the selected game root could
        # point the whole layout at another game (or at data/*.json).  Reject
        # such layouts; the game root itself may be a symlink.
        for target in (root, js_dir, data_dir, plugin_dir, plugins_js):
            if not _path_within_root(base, target):
                break
            if _has_symlink_below(base, target):
                break
        else:
            # De-duplicate when game_path itself is ".../www" and root == www.
            if any(existing[0] == root for existing in candidates):
                continue
            candidates.append((root, js_dir, engine))
            continue
        continue

    if not candidates:
        return None
    if len(candidates) == 1:
        return GameLayout(
            game_path=base,
            engine=candidates[0][2],
            js_dir=candidates[0][1],
            data_dir=candidates[0][0] / "data",
        )

    # Ambiguous.  Prefer the folder containing package.json's main file.
    preferred: List[Tuple[Path, Path, str]] = []
    for package_base in (base, base / "www"):
        main = _read_package_main(package_base)
        if not main:
            continue
        main_parent = os.path.normpath(
            os.path.abspath(os.path.join(str(package_base), os.path.dirname(main)))
        )
        for candidate in candidates:
            if os.path.normpath(os.path.abspath(str(candidate[0]))) == main_parent:
                if candidate not in preferred:
                    preferred.append(candidate)
        # Some MV package.json files point at "www/index.html" while the
        # package itself lives inside www.  Fall through to component check.
        main_parts = Path(main).parts
        if "www" in main_parts:
            for candidate in candidates:
                if candidate[0] == base / "www" and candidate not in preferred:
                    preferred.append(candidate)

    if preferred:
        root, js_dir, engine = preferred[0]
    else:
        www_candidate = next((c for c in candidates if c[0] == base / "www"), None)
        root, js_dir, engine = www_candidate or candidates[0]

    return GameLayout(
        game_path=base,
        engine=engine,
        js_dir=js_dir,
        data_dir=root / "data",
    )


# ---------------------------------------------------------------------------
# Game data scanning
# ---------------------------------------------------------------------------

_DATABASE_FIELDS: Tuple[Tuple[str, Tuple[str, ...], bool], ...] = (
    ("Actors.json", ("name", "nickname", "profile"), False),
    ("Classes.json", ("name",), False),
    ("Skills.json", ("name", "description", "message1", "message2"), False),
    ("Items.json", ("name", "description"), False),
    ("Weapons.json", ("name", "description"), False),
    ("Armors.json", ("name", "description"), False),
    ("Enemies.json", ("name",), False),
    ("States.json", ("name", "message1", "message2", "message3", "message4"), False),
)

_MAP_RE = re.compile(r"^Map\d+\.json$", re.IGNORECASE)

_PLUGIN_DISPLAY_KEY_TOKENS = (
    "name",
    "help",
    "label",
    "text",
    "command",
    "menu",
    "title",
)
_PLUGIN_CODE_MARKERS = (
    "function",
    "=>",
    "$game",
    "this.",
    "window.",
    "document.",
    "SceneManager",
    "Math.",
    "return ",
    "var ",
    "const ",
    "let ",
    "eval(",
    "if (",
    "if(",
    "for (",
    "for(",
    "while (",
    "while(",
    "console.",
)


def _looks_like_plugin_label(value: str, force: bool = False) -> bool:
    text = value.strip()
    if len(text) < 2 or len(text) > 240:
        return False
    lowered = text.casefold()
    if lowered in {"true", "false", "null", "undefined", "none"}:
        return False
    if text.startswith("#") or re.match(r"^(?:rgb|rgba|hsl|hsla)\(", text, re.I):
        return False
    if any(marker.casefold() in lowered for marker in _PLUGIN_CODE_MARKERS):
        return False
    if ";" in text:
        return False
    if "://" in text:
        return False
    if re.fullmatch(r"[\d\s.,:;+\-*/%=()\[\]{}#]+", text):
        return False
    if re.fullmatch(r"[A-Za-z0-9_.\\/-]+", text) and re.search(r"[_./\\-]", text):
        return False
    has_japanese = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text))
    if not has_japanese:
        if not force:
            return False
        if text in {
            "HP",
            "MP",
            "TP",
            "EXP",
            "AP",
            "Lv",
            "ON",
            "OFF",
            "BGM",
            "BGS",
            "ME",
            "SE",
        }:
            return True
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", text):
            return bool(text[0].isupper() and not text[-1].isdigit() and len(text) > 2)
        return " " in text
    return True


def _collect_plugin_labels(
    value: Any,
    output: List[str],
    seen: Set[str],
    *,
    force: bool = False,
    depth: int = 0,
) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ('"', "{", "["):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if parsed is not None and parsed != text:
                _collect_plugin_labels(
                    parsed, output, seen, force=True, depth=depth + 1
                )
                return
        if _looks_like_plugin_label(text, force=force):
            normalized = _normalize_text(text)
            if normalized not in seen:
                seen.add(normalized)
                output.append(normalized)
        return
    if isinstance(value, list):
        for item in value:
            _collect_plugin_labels(item, output, seen, force=force, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).casefold()
            display_key = any(token in key_text for token in _PLUGIN_DISPLAY_KEY_TOKENS)
            _collect_plugin_labels(
                item,
                output,
                seen,
                force=force or display_key,
                depth=depth + 1,
            )


def _plugin_parameter_labels(
    plugins_js: Path,
) -> Tuple[List[str], List[str]]:
    """Extract display-like strings from plugins.js parameters.

    This is intentionally limited to parameter objects and JSON-encoded
    parameter values.  It does not scan plugin source code or descriptions.
    """

    try:
        raw = Path(plugins_js).read_bytes()
        text, _encoding, _bom = _decode_bytes(raw)
    except OSError as exc:
        return [], [f"Could not read plugins.js for UI labels: {exc}"]

    try:
        analysis = _analyze_plugins(text)
        raw_array = text[analysis.array_start : analysis.array_end + 1]
        try:
            plugins = json.loads(raw_array)
        except (json.JSONDecodeError, ValueError):
            plugins = json.loads(re.sub(r",(\s*[}\]])", r"\1", raw_array))
    except Exception as exc:
        return [], [f"Could not parse plugins.js UI labels: {exc}"]

    labels: List[str] = []
    seen: Set[str] = set()
    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        if plugin.get("status") is False:
            continue
        parameters = plugin.get("parameters")
        if isinstance(parameters, dict):
            _collect_plugin_labels(parameters, labels, seen)
    return labels, []


def _path_within_root(root: Path, target: Path) -> bool:
    try:
        root_real = os.path.realpath(str(root))
        target_real = os.path.realpath(str(target))
    except OSError:
        return False
    return target_real == root_real or target_real.startswith(root_real + os.sep)


def _has_symlink_below(root: Path, target: Path) -> bool:
    """Reject symlink components below a game root (the root itself may be a symlink)."""

    try:
        relative = os.path.relpath(str(target), str(root))
    except ValueError:
        return True
    if relative == ".":
        return False
    current = Path(root)
    for part in Path(relative).parts:
        if part in ("", "."):
            continue
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _inside_app_bundle(path: Path) -> bool:
    return any(part.lower().endswith(".app") for part in Path(path).parts)


def _directory_state(path: Path) -> Tuple[bool, Optional[str]]:
    """Return (is_directory, error) without treating permission errors as missing."""

    try:
        info = Path(path).stat()
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        return False, str(exc)
    if not stat.S_ISDIR(info.st_mode):
        return False, f"Not a directory: {path}"
    return True, None


def _iter_dicts(value: Any) -> Iterator[dict]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
    elif isinstance(value, dict):
        for item in value.values():
            if isinstance(item, dict):
                yield item


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)


class _Extractor:
    def __init__(self) -> None:
        self.strings: List[str] = []
        self.block_keys: Set[str] = set()
        self.line_keys: Set[str] = set()
        self.block_lines: Dict[str, List[str]] = {}
        self._seen: Set[str] = set()

    def add(self, value: Any) -> None:
        if not isinstance(value, str):
            return
        normalized = _normalize_text(value)
        if _is_blank(normalized):
            return
        if normalized not in self._seen:
            self._seen.add(normalized)
            self.strings.append(normalized)

    def add_block(self, lines: Sequence[Any]) -> None:
        normalized_lines: List[str] = []
        for line in lines:
            if isinstance(line, str):
                normalized_lines.append(_normalize_text(line))
        if not normalized_lines:
            return
        block = "\n".join(normalized_lines)
        if _is_blank(block):
            return
        if block not in self._seen:
            self._seen.add(block)
            self.strings.append(block)
        self.block_keys.add(block)

        line_list = self.block_lines.setdefault(block, [])
        for line in normalized_lines:
            if _is_blank(line):
                continue
            self.line_keys.add(line)
            if line not in line_list:
                line_list.append(line)

    def collect_commands(self, commands: Any, engine: str) -> None:
        if not isinstance(commands, list):
            return
        index = 0
        length = len(commands)
        while index < length:
            command = commands[index]
            if not isinstance(command, dict):
                index += 1
                continue
            code = command.get("code")
            parameters = command.get("parameters")
            if not isinstance(parameters, list):
                parameters = []

            if code in (401, 405):
                group_code = code
                lines: List[str] = []
                scan = index
                while scan < length:
                    item = commands[scan]
                    if not isinstance(item, dict) or item.get("code") != group_code:
                        break
                    params = item.get("parameters")
                    if (
                        isinstance(params, list)
                        and params
                        and isinstance(params[0], str)
                    ):
                        lines.append(params[0])
                    scan += 1
                self.add_block(lines)
                index = scan
                continue

            if code == 101:
                # MZ speaker name lives in parameters[4].  MV's 101 fields are
                # face/background/position data and must not be exported.
                if (
                    engine == "MZ"
                    and len(parameters) > 4
                    and isinstance(parameters[4], str)
                ):
                    self.add(parameters[4])
            elif code == 102:
                choices = parameters[0] if parameters else None
                if isinstance(choices, list):
                    for choice in choices:
                        if isinstance(choice, str):
                            self.add(choice)
            elif code in (320, 324, 325):
                if len(parameters) > 1 and isinstance(parameters[1], str):
                    self.add(parameters[1])
            # 402 labels and 105/355/655/356/357/108/408 are intentionally
            # ignored.  402 is editor-only; the others are script/comment data.
            index += 1

    def collect_event_container(self, container: Any, engine: str) -> None:
        if not isinstance(container, dict):
            return
        pages = container.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    self.collect_commands(page.get("list"), engine)
        self.collect_commands(container.get("list"), engine)


def _scan_command_lists(
    extractor: _Extractor,
    data: Any,
    engine: str,
    *,
    common_events: bool = False,
    map_object: bool = False,
) -> None:
    if map_object:
        if not isinstance(data, dict):
            return
        display_name = data.get("displayName")
        if isinstance(display_name, str):
            extractor.add(display_name)
        events = data.get("events")
        if isinstance(events, dict):
            event_values: Iterable[Any] = events.values()
        elif isinstance(events, list):
            event_values = events
        else:
            event_values = ()
        for event in event_values:
            extractor.collect_event_container(event, engine)
        return

    for record in _iter_dicts(data):
        if common_events:
            extractor.collect_commands(record.get("list"), engine)
        else:
            extractor.collect_event_container(record, engine)


def _scan_system(extractor: _Extractor, data: Any) -> None:
    if not isinstance(data, dict):
        return
    for key in ("gameTitle", "currencyUnit"):
        value = data.get(key)
        if isinstance(value, str):
            extractor.add(value)

    for key in (
        "elements",
        "skillTypes",
        "weaponTypes",
        "armorTypes",
        "equipTypes",
    ):
        for value in _iter_strings(data.get(key)):
            extractor.add(value)

    terms = data.get("terms")
    if isinstance(terms, dict):
        for key in ("basic", "commands", "params", "messages"):
            for value in _iter_strings(terms.get(key)):
                extractor.add(value)


def scan_game(game_path: Any) -> ScanResult:
    """Scan all standard translatable text in an MV/MZ game."""

    layout = detect_game_layout(game_path)
    if layout is None:
        return ScanResult(
            layout=None,
            warnings=[
                "Unsupported game: no standard MV/MZ core JS and sibling data folder found."
            ],
        )

    extractor = _Extractor()
    warnings: List[str] = []
    files_scanned = 0
    categories: Dict[str, Set[str]] = {}

    def add_category(category: str, value: Any) -> None:
        if not isinstance(value, str):
            return
        normalized = _normalize_text(value)
        if _is_blank(normalized):
            return
        categories.setdefault(category, set()).add(normalized)

    def process(filename: str, handler) -> None:
        nonlocal files_scanned
        path = layout.data_dir / filename
        if not path.is_file():
            return
        files_scanned += 1
        info = _load_json_file(path)
        if info.error:
            warnings.append(info.error)
            return
        try:
            handler(info.value)
        except Exception as exc:  # defensive: malformed structures never crash
            warnings.append(f"Could not scan {path}: {exc}")

    for filename, fields, _unused in _DATABASE_FIELDS:
        category = Path(filename).stem

        def handler(
            value: Any,
            fields: Tuple[str, ...] = fields,
            category: str = category,
        ) -> None:
            for record in _iter_dicts(value):
                for field_name in fields:
                    extractor.add(record.get(field_name))
                add_category(category, record.get("name"))

        process(filename, handler)

    process(
        "Troops.json",
        lambda value: _scan_command_lists(extractor, value, layout.engine),
    )
    process(
        "CommonEvents.json",
        lambda value: _scan_command_lists(
            extractor, value, layout.engine, common_events=True
        ),
    )

    def map_info_handler(value: Any) -> None:
        for record in _iter_dicts(value):
            name = record.get("name")
            extractor.add(name)
            add_category("Maps", name)

    process("MapInfos.json", map_info_handler)

    try:
        map_files = sorted(
            (
                path
                for path in layout.data_dir.iterdir()
                if path.is_file()
                and _MAP_RE.match(path.name)
                and path.name.lower() != "mapinfos.json"
            ),
            key=lambda path: path.name.lower(),
        )
    except OSError as exc:
        warnings.append(f"Could not list map data files in {layout.data_dir}: {exc}")
        map_files = []
    for map_path in map_files:
        files_scanned += 1
        info = _load_json_file(map_path)
        if info.error:
            warnings.append(info.error)
            continue
        try:
            _scan_command_lists(extractor, info.value, layout.engine, map_object=True)
        except Exception as exc:
            warnings.append(f"Could not scan {map_path}: {exc}")

    def system_handler(value: Any) -> None:
        _scan_system(extractor, value)
        if not isinstance(value, dict):
            return
        add_category("System", value.get("gameTitle"))
        add_category("System", value.get("currencyUnit"))
        for key in (
            "elements",
            "skillTypes",
            "weaponTypes",
            "armorTypes",
            "equipTypes",
        ):
            for text in _iter_strings(value.get(key)):
                add_category("System", text)
        terms = value.get("terms")
        if isinstance(terms, dict):
            for key in ("basic", "commands", "params", "messages"):
                for text in _iter_strings(terms.get(key)):
                    add_category("System", text)

    process("System.json", system_handler)

    try:
        plugin_labels, plugin_warnings = _plugin_parameter_labels(layout.plugins_js)
        warnings.extend(plugin_warnings)
        for label in plugin_labels:
            extractor.add(label)
            add_category("Plugins", label)
    except Exception as exc:
        warnings.append(f"Could not scan plugin UI labels: {exc}")

    all_source_keys = set(extractor.strings) | set(extractor.line_keys)
    return ScanResult(
        layout=layout,
        strings=extractor.strings,
        block_keys=set(extractor.block_keys),
        line_keys=set(extractor.line_keys),
        all_source_keys=all_source_keys,
        warnings=warnings,
        files_scanned=files_scanned,
        block_lines={key: list(value) for key, value in extractor.block_lines.items()},
        category_keys={key: set(value) for key, value in categories.items()},
    )


_NAME_TAG_PREFIXES = ("\\n<", "\\nc<", "\\nr<")
_CODE_PUNCTUATION = "!.|^<>${}\\"
_MULTI_LETTER_CODES = {
    "CE",
    "FS",
    "PX",
    "PY",
    "SE",
    "SW",
    "ME",
    "BGM",
    "BGS",
    "FN",
    "FR",
    "OC",
    "OW",
    "NC",
    "NR",
}


def _tokenize_clean(value: str) -> List[Tuple[str, str, str]]:
    """Split a source string into text, control, break, and name-tag segments.

    Segment tuples are ``(kind, text, prefix)`` where ``kind`` is one of
    ``text``, ``code``, ``break``, or ``name``.  Name segments store the inner
    tag text and the original ``\\n<``/``\\nc<``/``\\nr<`` prefix.
    """

    segments: List[Tuple[str, str, str]] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "\\":
            matched_tag = False
            for prefix in _NAME_TAG_PREFIXES:
                if value.startswith(prefix, index):
                    end = value.find(">", index + len(prefix))
                    if end != -1:
                        segments.append(
                            ("name", value[index + len(prefix) : end], prefix)
                        )
                        index = end + 1
                        matched_tag = True
                        break
            if matched_tag:
                continue

            start = index
            index += 1
            if index >= length:
                segments.append(("code", "\\", ""))
                continue
            following = value[index]
            if following == "\\":
                index += 1
            elif following in _CODE_PUNCTUATION:
                index += 1
            elif following.isascii() and following.isalpha():
                letters_start = index
                index += 1
                while (
                    index < length
                    and value[index].isascii()
                    and value[index].isalpha()
                    and index - letters_start < 3
                ):
                    index += 1
                letters = value[letters_start:index]
                chosen_length = 1
                upper_letters = letters.upper()
                for size in range(len(letters), 1, -1):
                    if upper_letters[:size] in _MULTI_LETTER_CODES:
                        chosen_length = size
                        break
                index = letters_start + chosen_length
                if index < length and value[index] == "[":
                    end = value.find("]", index + 1)
                    if end != -1:
                        index = end + 1
                elif index < length and value[index] == "<":
                    end = value.find(">", index + 1)
                    if end != -1:
                        index = end + 1
            else:
                index += 1
            segments.append(("code", value[start:index], ""))
            continue

        if char in "\n\f":
            segments.append(("break", char, ""))
            index += 1
            continue

        end = index
        while end < length and value[end] not in "\\\n\f":
            end += 1
        segments.append(("text", value[index:end], ""))
        index = end
    return segments


def _is_useful_clean_key(key: str, allow_single: bool = False) -> bool:
    if not key or not key.strip():
        return False
    if len(key.strip()) == 1 and not allow_single:
        return bool(key.strip().isascii() and key.strip().isalpha())
    if any(char.isalpha() for char in key):
        return True
    return "?" in key or "？" in key


def _clean_keys_for_source(
    source: str,
    allowed_single: Optional[Set[str]] = None,
) -> List[str]:
    keys: List[str] = []
    allowed_single = allowed_single or set()
    for kind, text, _prefix in _tokenize_clean(source):
        if kind == "text":
            cleaned = _normalize_text(text).strip()
            if _is_useful_clean_key(
                cleaned,
                allow_single=cleaned in allowed_single,
            ):
                keys.append(cleaned)
        elif kind == "name":
            keys.extend(_clean_keys_for_source(text, allowed_single))
    return keys


def clean_export_keys(scan: ScanResult) -> List[str]:
    """Return MTool-style cleaned keys for every display string in a scan."""

    result: List[str] = []
    seen: Set[str] = set()
    allowed_single: Set[str] = {
        key for values in scan.category_keys.values() for key in values if len(key) == 1
    }

    def add(key: str, allow_single: bool = False) -> None:
        normalized = _normalize_text(key).strip()
        if _is_separator_text(normalized):
            return
        if not _is_useful_clean_key(
            normalized,
            allow_single=allow_single or normalized in allowed_single,
        ):
            return
        if normalized in seen:
            return
        seen.add(normalized)
        result.append(normalized)

    for source in scan.strings:
        if not isinstance(source, str) or _is_blank(source):
            continue
        segments = _tokenize_clean(source)
        # Preserve code-free full blocks as context keys.  Code-bearing blocks
        # are represented by their text runs only.
        if not any(kind in ("code", "name") for kind, _text, _prefix in segments):
            if "\f" not in source:
                add(source)
        for kind, text, _prefix in segments:
            if kind == "text":
                add(text)
            elif kind == "name":
                for key in _clean_keys_for_source(text, allowed_single):
                    add(key)
    return result


def _clean_translation_lookup(
    data: Optional[TranslationData], key: str
) -> Optional[str]:
    if data is None:
        return None
    normalized_key = _normalize_text(key).strip()
    if not normalized_key:
        return None
    value = data.entries.get(normalized_key)
    if value is None:
        return None
    normalized_value = _normalize_text(value).strip()
    if not normalized_value or normalized_value == normalized_key:
        return None
    if any(
        kind in ("code", "name")
        for kind, _text, _prefix in _tokenize_clean(normalized_value)
    ):
        return None
    if "\f" in normalized_value:
        return None
    return normalized_value


def _exact_translation_lookup(
    data: Optional[TranslationData], source: str
) -> Optional[str]:
    if data is None:
        return None
    normalized_source = _normalize_text(source)
    value = data.entries.get(normalized_source)
    if value is None:
        return None
    normalized_value = _normalize_text(value).strip()
    if not normalized_value or normalized_value == normalized_source:
        return None
    return normalized_value


def _compile_name_tag(
    inner: str,
    data: Optional[TranslationData],
) -> Tuple[str, bool]:
    output: List[str] = []
    changed = False
    for kind, text, _prefix in _tokenize_clean(inner):
        if kind == "text":
            translated = _clean_translation_lookup(data, text)
            if translated is not None:
                output.append(translated)
                changed = True
            else:
                output.append(text)
        else:
            output.append(text)
    return "".join(output), changed


def _structural_signature(value: str) -> Tuple[Any, ...]:
    """Return ordered code/tag/break structure without translatable text."""

    signature: List[Any] = []
    for kind, text, prefix in _tokenize_clean(value):
        if kind == "code":
            if (
                len(text) >= 2
                and text[1] not in ("\\",) + tuple(_CODE_PUNCTUATION)
                and not (text[1].isascii() and text[1].isalpha())
            ):
                continue
            signature.append(("code", text))
        elif kind == "break":
            if text == "\f":
                signature.append(("page", "\f"))
        elif kind == "name":
            signature.append(("name", prefix, _structural_signature(text)))
    return tuple(signature)


def _safe_exact_override(source: str, translation: str) -> bool:
    return _structural_signature(source) == _structural_signature(
        translation
    ) and _placeholder_tokens(source) == _placeholder_tokens(translation)


def _compile_source(
    source: str,
    data: Optional[TranslationData],
    cache: Dict[str, str],
    unsafe_overrides: Optional[List[str]] = None,
) -> str:
    if source in cache:
        return cache[source]

    exact = _exact_translation_lookup(data, source)
    unsafe_exact = False
    if exact is not None:
        if _safe_exact_override(source, exact):
            cache[source] = exact
            return exact
        unsafe_exact = True
        if unsafe_overrides is not None:
            unsafe_overrides.append(source)

    segments = _tokenize_clean(source)
    if not any(kind in ("code", "name") for kind, _text, _prefix in segments):
        if unsafe_exact:
            cache[source] = source
            return source
        translated = _clean_translation_lookup(data, source)
        if translated is not None:
            cache[source] = translated
            return translated

    output: List[str] = []
    changed = False
    for kind, text, prefix in segments:
        if kind == "text":
            translated = _clean_translation_lookup(data, text)
            if translated is not None:
                output.append(translated)
                changed = True
            else:
                output.append(text)
        elif kind == "name":
            inner, inner_changed = _compile_name_tag(text, data)
            if inner_changed:
                output.append(prefix + inner + ">")
                changed = True
            else:
                output.append(prefix + text + ">")
        else:
            output.append(text)

    result = "".join(output) if changed else source
    cache[source] = result
    return result


def _text_requires_translation(text: str) -> bool:
    normalized = _normalize_text(text).strip()
    if not normalized or _is_separator_text(normalized):
        return False
    if len(normalized) == 1:
        return bool(normalized.isascii() and normalized.isalpha())
    if not any(char.isalpha() for char in normalized):
        return "?" in normalized or "？" in normalized
    return True


def _source_fully_translated(
    source: str,
    data: Optional[TranslationData],
) -> bool:
    exact = _exact_translation_lookup(data, source)
    if exact is not None and _safe_exact_override(source, exact):
        return True
    segments = _tokenize_clean(source)
    if not any(kind in ("code", "name") for kind, _text, _prefix in segments):
        if _clean_translation_lookup(data, source) is not None:
            return True
    for kind, text, _prefix in segments:
        if kind == "text":
            if (
                _text_requires_translation(text)
                and _clean_translation_lookup(data, text) is None
            ):
                return False
        elif kind == "name":
            if not _source_fully_translated(text, data):
                return False
    return True


def compile_translation(
    scan: ScanResult,
    data: Optional[TranslationData],
) -> CompilationResult:
    """Compile cleaned fragment translations back into exact source keys.

    Exact source-key translations always win.  Otherwise, the compiler
    tokenizes each source, translates its text runs and name-tag contents, and
    reinserts the original control tokens in order.
    """

    if data is None:
        return CompilationResult(
            total_source_count=len(scan.all_source_keys),
            warnings=["No translation data was loaded."],
        )

    cache: Dict[str, str] = {}
    entries: Dict[str, str] = {}
    unsafe_overrides: List[str] = []
    for source in sorted(scan.all_source_keys):
        compiled = _compile_source(source, data, cache, unsafe_overrides)
        if compiled != source and not _is_blank(compiled):
            entries[source] = compiled

    partial_blocks: List[str] = []
    for block in scan.block_keys:
        if block not in entries:
            continue
        exact_block = _exact_translation_lookup(data, block)
        if exact_block is not None and _safe_exact_override(block, exact_block):
            continue
        if not _source_fully_translated(block, data):
            entries.pop(block, None)
            partial_blocks.append(block)

    clean_keys = set(clean_export_keys(scan))
    exact_keys = set(scan.all_source_keys)
    provided_keys = [
        key
        for key, value in data.entries.items()
        if isinstance(value, str) and not _is_blank(value) and value != key
    ]
    unknown_keys = sorted(
        key for key in provided_keys if key not in clean_keys and key not in exact_keys
    )
    matched_count = len(provided_keys) - len(unknown_keys)
    unsafe_overrides = sorted(set(unsafe_overrides))

    warnings: List[str] = []
    if not entries:
        warnings.append(
            "No translations matched this game's source text. "
            "Check the game version/language or use a current export."
        )
    if unknown_keys:
        warnings.append(
            f"{len(unknown_keys)} provided key(s) do not belong to this game's "
            "exported text; they were ignored."
        )
    if partial_blocks:
        warnings.append(
            f"{len(partial_blocks)} partially translated dialogue block(s) were "
            "left uncompiled so missing-line export can still find them."
        )

    return CompilationResult(
        entries=entries,
        total_source_count=len(scan.all_source_keys),
        compiled_source_count=len(entries),
        clean_key_count=len(clean_keys),
        provided_key_count=len(provided_keys),
        matched_key_count=matched_count,
        unknown_keys=unknown_keys,
        unsafe_override_keys=unsafe_overrides,
        warnings=warnings,
    )


def _is_reserved_export_name(name: str) -> bool:
    return name.casefold() in _PROTECTED_EXPORT_NAMES


def is_export_path_allowed(
    output_path: Any,
    layout: Optional[GameLayout] = None,
) -> Tuple[bool, str]:
    path = _absolute_path(output_path)
    if _is_reserved_export_name(path.name):
        return False, f"{path.name} is reserved or launcher-managed."
    if layout is not None:
        for root, label in (
            (layout.data_dir, "data"),
            (layout.js_dir, "js"),
            (layout.plugin_dir, "plugins"),
        ):
            if _path_within_root(root, path):
                return (
                    False,
                    f"Exports cannot be written inside the game's {label} folder.",
                )
        if _path_within_root(layout.game_path, path):
            try:
                relative = path.relative_to(layout.game_path)
            except ValueError:
                relative = Path("")
            name = path.name
            if (
                len(relative.parts) != 1
                or not name.endswith(".json")
                or not (
                    name == DEFAULT_EXPORT_NAME
                    or (name.startswith("ManualTransFile") and name.endswith(".json"))
                )
            ):
                return (
                    False,
                    "Inside the game folder, exports are limited to top-level "
                    "ManualTransFile*.json files.",
                )
    return True, ""


def inspect_export_target(
    output_path: Any,
    layout: Optional[GameLayout] = None,
) -> ExportTargetInfo:
    """Inspect an export destination without modifying it."""

    path = _absolute_path(output_path)
    _allowed, reason = is_export_path_allowed(path, layout)
    info = ExportTargetInfo(
        path=path,
        is_reserved=_is_reserved_export_name(path.name),
        blocked_reason=reason,
    )
    try:
        stat_info = path.stat()
    except FileNotFoundError:
        return info
    except OSError as exc:
        info.error = f"Could not inspect {path}: {exc}"
        return info

    info.exists = True
    info.size = stat_info.st_size
    info.modified_ns = stat_info.st_mtime_ns
    loaded = _load_json_file(path)
    if loaded.error:
        info.error = loaded.error
    elif isinstance(loaded.value, dict):
        info.key_count = len(loaded.value)
        info.non_empty_count = sum(
            1
            for value in loaded.value.values()
            if isinstance(value, str) and not _is_blank(value)
        )
    else:
        info.error = f"Existing export is not a flat JSON object: {path}"
    return info


def create_export_backup(output_path: Any) -> Path:
    """Create a uniquely named backup of an existing export file."""

    path = _absolute_path(output_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TranslationError(f"Could not read {path} for backup: {exc}") from exc

    stamp = _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    for index in range(1000):
        suffix = "" if index == 0 else f"-{index}"
        candidate = path.with_name(f"{path.stem}.{stamp}{suffix}{path.suffix}.bak")
        try:
            fd = os.open(
                str(candidate),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise TranslationError(
                f"Could not create export backup {candidate}: {exc}"
            ) from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            return candidate
        except Exception:
            try:
                candidate.unlink()
            except OSError:
                pass
            raise
    raise TranslationError(f"Could not choose a unique backup name for {path}")


def _write_export_payload(
    output_path: Any,
    strings: Sequence[str],
    *,
    backup_existing: bool = True,
    identity_values: bool = False,
    layout: Optional[GameLayout] = None,
) -> ExportResult:
    output = _absolute_path(output_path)
    allowed, reason = is_export_path_allowed(output, layout)
    if not allowed:
        raise TranslationError(f"Refusing to write export: {reason}")

    info = inspect_export_target(output, layout)
    backup_path: Optional[Path] = None
    if info.exists and backup_existing:
        backup_path = create_export_backup(output)

    payload = {source: (source if identity_values else "") for source in strings}
    _atomic_write_json(output, payload)
    return ExportResult(count=len(strings), path=output, backup_path=backup_path)


def export_raw_text(scan: ScanResult, output_path: Any) -> int:
    """Write the MTool-compatible flat export and return the entry count."""

    return export_raw_text_result(scan, output_path).count


def export_raw_text_result(
    scan: ScanResult,
    output_path: Any,
    *,
    backup_existing: bool = True,
    identity_values: bool = False,
    layout: Optional[GameLayout] = None,
) -> ExportResult:
    """Write all scanned source strings and return export metadata."""

    return _write_export_payload(
        output_path,
        scan.strings,
        backup_existing=backup_existing,
        identity_values=identity_values,
        layout=layout or scan.layout,
    )


def export_clean_text_result(
    scan: ScanResult,
    output_path: Any,
    *,
    backup_existing: bool = True,
    identity_values: bool = True,
    layout: Optional[GameLayout] = None,
) -> ExportResult:
    """Write the MTool-style cleaned fragment export."""

    return _export_text_keys_result(
        output_path,
        clean_export_keys(scan),
        backup_existing=backup_existing,
        identity_values=identity_values,
        layout=layout or scan.layout,
    )


def metadata_export_keys(layout: GameLayout) -> List[str]:
    """Collect editor-only metadata strings for an optional reference export."""

    keys: List[str] = []
    seen: Set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        normalized = _normalize_text(value).strip()
        if _is_blank(normalized) or normalized in seen:
            return
        seen.add(normalized)
        keys.append(normalized)

    data_dir = layout.data_dir
    for filename in (
        "CommonEvents.json",
        "Troops.json",
        "Animations.json",
        "Tilesets.json",
        "MapInfos.json",
    ):
        info = _load_json_file(data_dir / filename)
        if not info.exists or info.error:
            continue
        for record in _iter_dicts(info.value):
            add(record.get("name"))

    system = _load_json_file(data_dir / "System.json")
    if system.exists and not system.error and isinstance(system.value, dict):
        for key in ("switches", "variables"):
            for value in _iter_strings(system.value.get(key)):
                add(value)

    try:
        map_files = sorted(
            (
                path
                for path in data_dir.iterdir()
                if path.is_file()
                and _MAP_RE.match(path.name)
                and path.name.lower() != "mapinfos.json"
            ),
            key=lambda path: path.name.lower(),
        )
    except OSError:
        map_files = []
    for map_path in map_files:
        info = _load_json_file(map_path)
        if not info.exists or info.error or not isinstance(info.value, dict):
            continue
        events = info.value.get("events")
        if isinstance(events, dict):
            event_values = events.values()
        elif isinstance(events, list):
            event_values = events
        else:
            event_values = ()
        for event in event_values:
            if isinstance(event, dict):
                add(event.get("name"))

    return keys


def export_metadata_result(
    layout: GameLayout,
    output_path: Any,
    *,
    backup_existing: bool = True,
) -> ExportResult:
    """Write editor-only metadata as an identity-value reference JSON."""

    return _export_text_keys_result(
        output_path,
        metadata_export_keys(layout),
        backup_existing=backup_existing,
        identity_values=True,
        layout=layout,
    )


def _clean_keys_for_sources(
    sources: Iterable[str],
    allowed_single: Optional[Set[str]] = None,
) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    allowed_single = allowed_single or set()

    def add(key: str) -> None:
        normalized = _normalize_text(key).strip()
        if _is_separator_text(normalized):
            return
        if not _is_useful_clean_key(
            normalized,
            allow_single=normalized in allowed_single,
        ):
            return
        if normalized in seen:
            return
        seen.add(normalized)
        result.append(normalized)

    for source in sources:
        if not isinstance(source, str) or _is_blank(source):
            continue
        segments = _tokenize_clean(source)
        if not any(kind in ("code", "name") for kind, _text, _prefix in segments):
            if "\f" not in source:
                add(source)
        for kind, text, _prefix in segments:
            if kind == "text":
                add(text)
            elif kind == "name":
                for key in _clean_keys_for_source(text, allowed_single):
                    add(key)
    return result


def _export_text_keys_result(
    output_path: Any,
    strings: Sequence[str],
    *,
    backup_existing: bool = True,
    identity_values: bool = False,
    layout: Optional[GameLayout] = None,
) -> ExportResult:
    """Write a flat export for a caller-selected set of source strings."""

    return _write_export_payload(
        output_path,
        strings,
        backup_existing=backup_existing,
        identity_values=identity_values,
        layout=layout,
    )


_SEPARATOR_ONLY_RE = re.compile(r"^[-=*_~\s.\u00b7\u2026\u2014\u2015\u3000]+$")


def _is_separator_text(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    if _SEPARATOR_ONLY_RE.fullmatch(stripped):
        return True
    # Labels such as "---武器---" are visual separators.
    return (
        len(stripped) >= 6 and stripped.startswith("---") and stripped.endswith("---")
    )


def _key_translation_state(key: str, data: Optional[TranslationData]) -> str:
    if data is None:
        return "missing"
    if key in data.entries:
        value = data.entries[key]
        if value == key:
            return "identity"
        return "translated"
    empty_keys = set(data.empty_keys)
    if key in empty_keys:
        return "empty"
    if key in data.raw_entries:
        raw_value = data.raw_entries[key]
        if not isinstance(raw_value, str) or _is_blank(raw_value):
            return "empty"
        return "identity"
    return "missing"


def coverage_report(
    scan: ScanResult,
    data: Optional[TranslationData],
) -> List[CoverageRow]:
    """Return display-name coverage grouped by database/system category."""

    order = {
        "Actors": 0,
        "Classes": 1,
        "Skills": 2,
        "Items": 3,
        "Weapons": 4,
        "Armors": 5,
        "Enemies": 6,
        "States": 7,
        "System": 8,
        "Maps": 9,
        "Plugins": 10,
    }
    rows: List[CoverageRow] = []
    for category in sorted(
        scan.category_keys,
        key=lambda name: (order.get(name, 99), name),
    ):
        row = CoverageRow(category=category)
        for key in sorted(scan.category_keys[category]):
            row.total += 1
            if _is_separator_text(key):
                row.separators += 1
                continue
            state = _key_translation_state(key, data)
            if state == "translated":
                row.translated += 1
            elif state == "identity":
                row.identity += 1
                row.missing_keys.append(key)
            elif state == "empty":
                row.empty += 1
                row.missing_keys.append(key)
            else:
                row.missing += 1
                row.missing_keys.append(key)
        rows.append(row)
    return rows


def missing_text_keys(
    scan: ScanResult,
    data: Optional[TranslationData],
) -> List[str]:
    """Return scanned source strings without a non-empty, non-identity translation."""

    return [
        source
        for source in scan.strings
        if _key_translation_state(source, data) != "translated"
    ]


def export_missing_text_result(
    scan: ScanResult,
    output_path: Any,
    data: Optional[TranslationData],
    *,
    backup_existing: bool = True,
) -> ExportResult:
    """Export only source strings that still need a translation."""

    return _export_text_keys_result(
        output_path,
        missing_text_keys(scan, data),
        backup_existing=backup_existing,
        layout=scan.layout,
    )


def export_clean_missing_text_result(
    scan: ScanResult,
    output_path: Any,
    data: Optional[TranslationData],
    *,
    backup_existing: bool = True,
    identity_values: bool = True,
) -> ExportResult:
    """Export cleaned fragment keys for sources that still need translation."""

    allowed_single = {
        key for values in scan.category_keys.values() for key in values if len(key) == 1
    }
    keys = _clean_keys_for_sources(
        missing_text_keys(scan, data),
        allowed_single=allowed_single,
    )
    if data is not None:
        keys = [key for key in keys if _clean_translation_lookup(data, key) is None]
    return _export_text_keys_result(
        output_path,
        keys,
        backup_existing=backup_existing,
        identity_values=identity_values,
        layout=scan.layout,
    )


def coverage_summary(
    scan: ScanResult,
    data: Optional[TranslationData],
    *,
    preview_limit: int = 8,
) -> str:
    """Return a readable display-name coverage report."""

    rows = coverage_report(scan, data)
    if not rows:
        return "No display-name categories were found in this game."

    lines = ["Translation coverage (display names):"]
    total = translated = untranslated = separators = 0
    for row in rows:
        display_total = max(0, row.total - row.separators)
        total += display_total
        translated += row.translated
        untranslated += row.untranslated
        separators += row.separators
        lines.append(
            f"  {row.category}: {row.translated}/{display_total} translated, "
            f"{row.untranslated} untranslated"
            + (f" ({row.separators} separators ignored)" if row.separators else "")
        )
        if row.missing_keys:
            preview = ", ".join(
                _shorten(key, 48) for key in row.missing_keys[:preview_limit]
            )
            suffix = (
                ""
                if len(row.missing_keys) <= preview_limit
                else f", ... (+{len(row.missing_keys) - preview_limit} more)"
            )
            lines.append(f"    missing: {preview}{suffix}")

    lines.append("")
    lines.append(
        f"Total: {translated}/{total} translated, {untranslated} untranslated"
        + (f"; {separators} separator/header strings ignored" if separators else "")
    )
    if data is None:
        lines.append("No translation dictionary is installed for this game.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Translation dictionary loading
# ---------------------------------------------------------------------------


def load_translation(path: Any) -> TranslationData:
    """Load a flat ``{source: translation}`` JSON dictionary.

    Non-string values are skipped with a warning.  Empty/whitespace-only
    translations are retained in ``raw_entries`` and reported via
    ``empty_keys`` but are not part of ``entries``.
    """

    source_path = _absolute_path(path)
    info = _load_json_file(source_path)
    if info.error:
        return TranslationData(
            path=source_path,
            warnings=[info.error],
            ok=False,
            error=info.error,
        )
    if not info.exists:
        error = f"Translation file not found: {source_path}"
        return TranslationData(
            path=source_path, warnings=[error], ok=False, error=error
        )
    if not isinstance(info.value, dict):
        error = f"Translation file must contain a flat JSON object: {source_path}"
        return TranslationData(
            path=source_path, warnings=[error], ok=False, error=error
        )

    entries: Dict[str, str] = {}
    raw_entries: Dict[str, Any] = {}
    empty_keys: List[str] = []
    identity_keys: List[str] = []
    warnings: List[str] = []
    invalid_count = 0

    for raw_key, raw_value in info.value.items():
        key = _normalize_text(raw_key)
        raw_entries[raw_key] = raw_value
        if not isinstance(raw_value, str):
            invalid_count += 1
            if len(warnings) < MAX_WARNING_DETAILS:
                warnings.append(
                    f"Non-string translation skipped for key {_shorten(key)!r}."
                )
            continue
        value = _normalize_text(raw_value)
        if _is_blank(value):
            empty_keys.append(key)
            continue
        if value == key:
            identity_keys.append(key)
            continue
        entries[key] = value

    if invalid_count > MAX_WARNING_DETAILS:
        warnings.append(
            f"{invalid_count - MAX_WARNING_DETAILS} more non-string values were skipped."
        )

    return TranslationData(
        path=source_path,
        entries=entries,
        raw_entries=raw_entries,
        empty_keys=empty_keys,
        identity_keys=identity_keys,
        invalid_count=invalid_count,
        warnings=warnings,
        ok=True,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"%(\d+)")
_ARG_CODE_RE = re.compile(r"\s*\[\s*([+-]?\d+)\s*\]")
_PUNCTUATION_CODES = "!.|^<>$"


def _control_tokens(value: str, engine: str) -> List[str]:
    """Return an ordered list of control-code tokens for validation.

    Escaped backslashes are consumed first.  Only codes supported by the
    detected engine are recognised; unknown codes (including ``\\_``) are
    ignored so they cannot produce false positives.
    """

    tokens: List[str] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char != "\\":
            index += 1
            continue
        if index + 1 >= length:
            break
        following = value[index + 1]
        if following == "\\":
            tokens.append("\\\\")
            index += 2
            continue

        code: Optional[str] = None
        position = index + 2
        if engine == "MZ" and value.startswith("FS", index + 1):
            code, position = "FS", index + 3
        elif engine == "MZ" and value.startswith("PX", index + 1):
            code, position = "PX", index + 3
        elif engine == "MZ" and value.startswith("PY", index + 1):
            code, position = "PY", index + 3
        elif engine == "MV" and following == "S":
            code, position = "S", index + 2
        elif following in "CINVPG":
            code, position = following, index + 2
        elif following in _PUNCTUATION_CODES:
            tokens.append(following)
            index += 2
            continue
        elif following == "f":
            # Form-feed/page-break is a layout character, not a code.
            index += 2
            continue
        else:
            # Unknown/non-standard escape (including \_ and \n); ignore.
            index += 2
            continue

        if code in ("C", "I", "N", "V", "P", "S", "FS", "PX", "PY"):
            match = _ARG_CODE_RE.match(value, position)
            if match:
                tokens.append(f"{code}[{int(match.group(1))}]")
                index = match.end()
            else:
                tokens.append(code)
                index = position
        else:
            tokens.append(code)
            index = position
    return tokens


def _placeholder_tokens(value: str) -> List[str]:
    return [str(int(match.group(1))) for match in _PLACEHOLDER_RE.finditer(value)]


def _format_code_token(token: str) -> str:
    if token == "\\\\":
        return "\\\\"
    if token in _PUNCTUATION_CODES or token in ("G",):
        return "\\" + token
    if token.endswith("]") or token in ("C", "I", "N", "V", "P", "S", "FS", "PX", "PY"):
        return "\\" + token
    return "\\" + token


def _append_counter_warnings(
    output: List[str],
    source_counter: Counter,
    translation_counter: Counter,
    *,
    kind: str,
    key: str,
) -> None:
    missing = source_counter - translation_counter
    extra = translation_counter - source_counter
    is_code = kind == "control code"
    label = "control code" if is_code else "placeholder"
    for token, count in missing.items():
        for _ in range(count):
            if len(output) >= MAX_WARNING_DETAILS:
                return
            rendered = _format_code_token(token) if is_code else f"%{token}"
            output.append(
                f"Missing {label} {rendered} in translation for {_shorten(key)!r}."
            )
    for token, count in extra.items():
        for _ in range(count):
            if len(output) >= MAX_WARNING_DETAILS:
                return
            rendered = _format_code_token(token) if is_code else f"%{token}"
            output.append(
                f"Extra {label} {rendered} in translation for {_shorten(key)!r}."
            )


def validate_translation(scan: ScanResult, data: TranslationData) -> ValidationResult:
    """Validate coverage, placeholders and engine-aware text codes."""

    unknown_keys = sorted(
        key for key in data.entries if key not in scan.all_source_keys
    )
    matched_keys = [key for key in data.entries if key in scan.all_source_keys]
    line_matched_count = sum(1 for key in data.entries if key in scan.line_keys)

    missing_blocks: List[str] = []
    for block in sorted(scan.block_keys):
        if block in data.entries:
            continue
        lines = scan.block_lines.get(block)
        if lines is None:
            lines = [line for line in block.split("\n") if not _is_blank(line)]
        else:
            lines = [line for line in lines if not _is_blank(line)]
        if not lines:
            # A whitespace-only block should not exist, but never mark it as
            # missing if there is nothing meaningful to translate.
            continue
        if all(line in data.entries for line in lines):
            continue
        missing_blocks.append(block)

    engine = scan.layout.engine if scan.layout is not None else "MV"
    code_warnings: List[str] = []

    for key in data.entries:
        translation = data.entries[key]
        source_counter = Counter(_control_tokens(key, engine))
        translation_counter = Counter(_control_tokens(translation, engine))
        _append_counter_warnings(
            code_warnings,
            source_counter,
            translation_counter,
            kind="control code",
            key=key,
        )
        if len(code_warnings) >= MAX_WARNING_DETAILS:
            break

    if len(code_warnings) < MAX_WARNING_DETAILS:
        for key in data.entries:
            translation = data.entries[key]
            source_counter = Counter(_placeholder_tokens(key))
            translation_counter = Counter(_placeholder_tokens(translation))
            _append_counter_warnings(
                code_warnings,
                source_counter,
                translation_counter,
                kind="placeholder",
                key=key,
            )
            if len(code_warnings) >= MAX_WARNING_DETAILS:
                break

    if len(code_warnings) < MAX_WARNING_DETAILS:
        for key in data.entries:
            translation = data.entries[key]
            if _structural_signature(key) != _structural_signature(translation):
                code_warnings.append(
                    "Control-code/tag order mismatch for "
                    f"{_shorten(key)!r}: source and translation must keep the "
                    "same ordered codes and name tags."
                )
            if len(code_warnings) >= MAX_WARNING_DETAILS:
                break
            if _placeholder_tokens(key) != _placeholder_tokens(translation):
                code_warnings.append(
                    "Placeholder order mismatch for "
                    f"{_shorten(key)!r}: %1/%2 order must be preserved."
                )
            if len(code_warnings) >= MAX_WARNING_DETAILS:
                break

    warnings: List[str] = []
    warnings.extend(data.warnings[:MAX_WARNING_DETAILS])
    warnings.extend(code_warnings[:MAX_WARNING_DETAILS])

    if len(data.entries) > 0 and len(matched_keys) == 0:
        warnings.append(
            "No dictionary keys match this game's exported text. "
            "Check the game version/language."
        )
    if unknown_keys:
        preview = ", ".join(
            _shorten(key, 50) for key in unknown_keys[:MAX_UNKNOWN_PREVIEW]
        )
        suffix = "" if len(unknown_keys) <= MAX_UNKNOWN_PREVIEW else ", ..."
        warnings.append(
            f"{len(unknown_keys)} unknown key(s) not found in this game: "
            f"{preview}{suffix}"
        )
    if data.empty_keys:
        warnings.append(
            f"{len(data.empty_keys)} empty translation value(s) are ignored."
        )
    if missing_blocks:
        preview = ", ".join(
            _shorten(key, 60) for key in missing_blocks[:MAX_UNKNOWN_PREVIEW]
        )
        suffix = "" if len(missing_blocks) <= MAX_UNKNOWN_PREVIEW else ", ..."
        warnings.append(
            f"{len(missing_blocks)} dialogue block(s) are not fully translated: "
            f"{preview}{suffix}"
        )

    return ValidationResult(
        matched_count=len(matched_keys),
        unknown_count=len(unknown_keys),
        empty_count=len(data.empty_keys),
        missing_block_count=len(missing_blocks),
        total_block_count=len(scan.block_keys),
        line_matched_count=line_matched_count,
        code_warnings=code_warnings,
        unknown_keys=unknown_keys,
        warnings=warnings,
        missing_block_keys=missing_blocks,
    )


# ---------------------------------------------------------------------------
# plugins.js scanner/editor
# ---------------------------------------------------------------------------

_JS_SPACE = " \t\r\n\v\f"


class _PluginsJsError(TranslationError):
    pass


def _mask_js(text: str) -> str:
    """Mask strings/comments, rejecting template literals or regex literals.

    The resulting string has the same length as *text*; structural characters
    outside strings/comments are preserved so bracket matching is safe.
    """

    output = list(text)
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in ("'", '"'):
            quote = char
            output[index] = " "
            index += 1
            closed = False
            while index < length:
                current = text[index]
                if current == "\\":
                    output[index] = " "
                    index += 1
                    if index < length:
                        if text[index] in "\r\n":
                            output[index] = text[index]
                        else:
                            output[index] = " "
                        index += 1
                    continue
                if current == quote:
                    output[index] = " "
                    index += 1
                    closed = True
                    break
                output[index] = current if current in "\r\n" else " "
                index += 1
            if not closed:
                raise _PluginsJsError("Unterminated string literal in plugins.js.")
        elif char == "`":
            raise _PluginsJsError(
                "plugins.js contains a template literal; refusing to edit it."
            )
        elif char == "/" and index + 1 < length and text[index + 1] == "/":
            output[index] = output[index + 1] = " "
            index += 2
            while index < length and text[index] not in "\r\n":
                output[index] = " "
                index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "*":
            output[index] = output[index + 1] = " "
            index += 2
            closed = False
            while index < length:
                if text[index] == "*" and index + 1 < length and text[index + 1] == "/":
                    output[index] = output[index + 1] = " "
                    index += 2
                    closed = True
                    break
                output[index] = text[index] if text[index] in "\r\n" else " "
                index += 1
            if not closed:
                raise _PluginsJsError("Unterminated block comment in plugins.js.")
        elif char == "/":
            raise _PluginsJsError(
                "plugins.js contains a regex/division operator outside a string; "
                "refusing to guess where the $plugins array ends."
            )
        else:
            index += 1
    return "".join(output)


def _skip_js_space_comments(text: str, index: int) -> int:
    length = len(text)
    while index < length:
        char = text[index]
        if char in _JS_SPACE:
            index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "/":
            index += 2
            while index < length and text[index] not in "\r\n":
                index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                raise _PluginsJsError("Unterminated comment in plugins.js.")
            index = end + 2
        else:
            break
    return index


def _parse_js_string(text: str, index: int) -> Tuple[str, int]:
    quote = text[index]
    if quote not in ("'", '"'):
        raise _PluginsJsError("Expected a string literal in plugins.js.")
    index += 1
    chars: List[str] = []
    length = len(text)
    simple_escapes = {
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "0": "\0",
    }
    while index < length:
        char = text[index]
        if char == quote:
            return "".join(chars), index + 1
        if char in "\r\n":
            raise _PluginsJsError("Unescaped newline in plugins.js string literal.")
        if char != "\\":
            chars.append(char)
            index += 1
            continue
        index += 1
        if index >= length:
            break
        escape = text[index]
        if escape in simple_escapes:
            chars.append(simple_escapes[escape])
            index += 1
        elif escape == "x":
            digits = text[index + 1 : index + 3]
            if len(digits) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{2}", digits):
                raise _PluginsJsError("Invalid \\x escape in plugins.js.")
            chars.append(chr(int(digits, 16)))
            index += 3
        elif escape == "u":
            if index + 1 < length and text[index + 1] == "{":
                close = text.find("}", index + 2)
                if close < 0:
                    raise _PluginsJsError("Invalid \\u{...} escape in plugins.js.")
                digits = text[index + 2 : close]
                try:
                    chars.append(chr(int(digits, 16)))
                except (ValueError, OverflowError) as exc:
                    raise _PluginsJsError(
                        "Invalid Unicode escape in plugins.js."
                    ) from exc
                index = close + 1
            else:
                digits = text[index + 1 : index + 5]
                if len(digits) != 4 or not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
                    raise _PluginsJsError("Invalid \\u escape in plugins.js.")
                chars.append(chr(int(digits, 16)))
                index += 5
        elif escape in "\r\n":
            # Line continuation.
            if escape == "\r" and index + 1 < length and text[index + 1] == "\n":
                index += 2
            else:
                index += 1
        else:
            chars.append(escape)
            index += 1
    raise _PluginsJsError("Unterminated string literal in plugins.js.")


def _match_brace(mask: str, start: int) -> int:
    depth = 0
    for index in range(start, len(mask)):
        char = mask[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise _PluginsJsError("Unbalanced braces in plugins.js.")


def _js_object_property(text: str, start: int, end: int, wanted: str) -> Optional[str]:
    """Find a top-level string property in a JS object literal."""

    index = start + 1
    brace_depth = 1
    bracket_depth = 0
    paren_depth = 0
    while index < end:
        char = text[index]
        if char in _JS_SPACE:
            index += 1
            continue
        if char == "/" and index + 1 < end and text[index + 1] in "/*":
            index = _skip_js_space_comments(text, index)
            continue
        if char in ("'", '"'):
            value, after = _parse_js_string(text, index)
            key_end = _skip_js_space_comments(text, after)
            at_top = brace_depth == 1 and bracket_depth == 0 and paren_depth == 0
            if at_top and key_end < end and text[key_end] == ":":
                if value == wanted:
                    value_start = _skip_js_space_comments(text, key_end + 1)
                    if value_start < end and text[value_start] in ("'", '"'):
                        result, _ = _parse_js_string(text, value_start)
                        return result
                    return None
                index = key_end + 1
                continue
            index = after
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
            if brace_depth == 0:
                break
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
        elif (
            (char.isalpha() or char in "_$")
            and brace_depth == 1
            and bracket_depth == 0
            and paren_depth == 0
        ):
            match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", text[index:])
            if match:
                identifier = match.group(0)
                after = index + len(identifier)
                key_end = _skip_js_space_comments(text, after)
                if key_end < end and text[key_end] == ":":
                    if identifier == wanted:
                        value_start = _skip_js_space_comments(text, key_end + 1)
                        if value_start < end and text[value_start] in ("'", '"'):
                            result, _ = _parse_js_string(text, value_start)
                            return result
                        return None
                    index = key_end + 1
                    continue
                index = after
                continue
        index += 1
    return None


def _analyze_plugins(text: str) -> _PluginsAnalysis:
    mask = _mask_js(text)
    assignments = list(re.finditer(r"\$plugins\s*=", mask))
    if len(assignments) != 1:
        if not assignments:
            raise _PluginsJsError("Could not locate the $plugins array in plugins.js.")
        raise _PluginsJsError(
            "plugins.js contains multiple $plugins assignments; refusing to edit it."
        )

    index = _skip_js_space_comments(mask, assignments[0].end())
    if index >= len(mask) or mask[index] != "[":
        raise _PluginsJsError("The $plugins assignment is not a simple array literal.")

    array_start = index
    depth = 0
    array_end = -1
    while index < len(mask):
        char = mask[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                array_end = index
                break
        index += 1
    if array_end < 0:
        raise _PluginsJsError("Unbalanced $plugins array in plugins.js.")

    entries: List[_PluginEntry] = []
    index = array_start + 1
    while True:
        index = _skip_js_space_comments(mask, index)
        if index >= array_end:
            break
        if mask[index] != "{":
            raise _PluginsJsError(
                "The $plugins array contains a non-object element; refusing to edit it."
            )
        object_start = index
        object_end = _match_brace(mask, index)
        if object_end > array_end:
            raise _PluginsJsError("Malformed $plugins entry in plugins.js.")
        name = _js_object_property(text, object_start, object_end + 1, "name")
        entry = _PluginEntry(start=object_start, end=object_end + 1, name=name)
        entries.append(entry)
        index = object_end + 1
        after = _skip_js_space_comments(mask, index)
        if after < array_end:
            if mask[after] == ",":
                entry.comma_after = after
                index = after + 1
                continue
            raise _PluginsJsError(
                "Expected a comma between $plugins entries in plugins.js."
            )
        index = after
        break

    return _PluginsAnalysis(
        text=text,
        mask=mask,
        array_start=array_start,
        array_end=array_end,
        entries=entries,
    )


def _line_indent(text: str, position: int) -> str:
    line_start = max(text.rfind("\n", 0, position), text.rfind("\r", 0, position)) + 1
    prefix = text[line_start:position]
    return prefix if prefix.strip() == "" else ""


def _merging_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not ranges:
        return []
    ranges.sort()
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _apply_ranges(text: str, ranges: List[Tuple[int, int]]) -> str:
    for start, end in sorted(ranges, reverse=True):
        text = text[:start] + text[end:]
    return text


def _remove_entry_text(text: str) -> Tuple[str, int]:
    analysis = _analyze_plugins(text)
    targets = [
        (index, entry)
        for index, entry in enumerate(analysis.entries)
        if entry.name == PLUGIN_NAME
    ]
    if not targets:
        return text, 0

    ranges: List[Tuple[int, int]] = []
    for index, entry in targets:
        comma_before: Optional[int] = None
        if index > 0:
            comma_before = analysis.entries[index - 1].comma_after
        if entry.comma_after is not None:
            ranges.append((entry.start, entry.comma_after + 1))
        elif comma_before is not None:
            ranges.append((comma_before, entry.end))
        else:
            ranges.append((entry.start, entry.end))

    updated = _apply_ranges(text, _merging_ranges(ranges))
    verify = _analyze_plugins(updated)
    if any(entry.name == PLUGIN_NAME for entry in verify.entries):
        raise _PluginsJsError("Could not remove the translation entry cleanly.")
    original_other = [e.name for e in analysis.entries if e.name != PLUGIN_NAME]
    updated_other = [e.name for e in verify.entries if e.name != PLUGIN_NAME]
    if original_other != updated_other:
        raise _PluginsJsError(
            "Refusing to edit plugins.js: unrelated entries would change."
        )
    return updated, len(targets)


def _entry_json() -> str:
    return json.dumps(
        {
            "name": PLUGIN_NAME,
            "status": True,
            "description": PLUGIN_DESCRIPTION,
            "parameters": {},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _insert_entry_text(text: str, newline: str) -> str:
    analysis = _analyze_plugins(text)
    entry_text = _entry_json()
    start = analysis.array_start
    end = analysis.array_end

    if analysis.entries:
        last = analysis.entries[-1]
        indent = _line_indent(text, last.start)
        anchor = end - 1
        while anchor > start and analysis.mask[anchor] in _JS_SPACE:
            anchor -= 1
        if anchor <= start:
            raise _PluginsJsError("Could not find an insertion point in $plugins.")
        suffix = text[anchor + 1 : end]
        needs_comma = analysis.mask[anchor] != ","
        insertion = ("," if needs_comma else "") + newline + indent + entry_text
        if "\n" not in suffix and "\r" not in suffix:
            insertion += newline + _line_indent(text, end)
        return text[: anchor + 1] + insertion + text[anchor + 1 :]

    closing_indent = _line_indent(text, end)
    inner = text[start + 1 : end]
    if inner.strip() == "":
        return (
            text[: start + 1]
            + newline
            + closing_indent
            + entry_text
            + newline
            + closing_indent
            + text[end:]
        )
    # Preserve comments inside an otherwise empty array, but make sure the
    # entry is separated from them by a newline.
    insertion = newline + closing_indent + entry_text + newline + closing_indent
    return text[: start + 1] + insertion + text[start + 1 :]


def _upsert_entry_text(text: str, newline: str) -> Tuple[str, bool]:
    analysis = _analyze_plugins(text)
    had_entry = any(entry.name == PLUGIN_NAME for entry in analysis.entries)
    stripped, _removed = _remove_entry_text(text)
    updated = _insert_entry_text(stripped, newline)
    verify = _analyze_plugins(updated)
    names = [entry.name for entry in verify.entries]
    if names.count(PLUGIN_NAME) != 1:
        raise _PluginsJsError(
            "Refusing to edit plugins.js: translation entry count would not be 1."
        )
    if [name for name in names if name != PLUGIN_NAME] != [
        entry.name for entry in analysis.entries if entry.name != PLUGIN_NAME
    ]:
        raise _PluginsJsError(
            "Refusing to edit plugins.js: unrelated entries would change."
        )
    return updated, had_entry


def _decode_js_document(raw: bytes, path: Path) -> _JsDocument:
    for text, encoding, bom in _iter_decodings(raw):
        if "\x00" in text:
            continue
        return _JsDocument(
            path=path,
            raw=raw,
            text=text,
            encoding=encoding,
            bom=bom,
            newline=_detect_newline(text),
        )
    raise _PluginsJsError(f"plugins.js appears to be binary: {path}")


def _encode_js_document(document: _JsDocument, text: str) -> bytes:
    try:
        return document.bom + text.encode(document.encoding)
    except UnicodeEncodeError as exc:
        raise _PluginsJsError(
            f"Could not preserve plugins.js encoding {document.encoding}: {exc}"
        ) from exc


def _plugins_js_path(layout: GameLayout) -> Path:
    return layout.plugins_js


def _plugins_js_backup_path(layout: GameLayout) -> Path:
    return layout.js_dir / PLUGINS_BACKUP_NAME


def _read_plugins_document(path: Path) -> _JsDocument:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise _PluginsJsError(f"plugins.js not found: {path}") from exc
    except OSError as exc:
        raise _PluginsJsError(f"Could not read plugins.js: {exc}") from exc
    return _decode_js_document(raw, path)


def _write_plugins_document(document: _JsDocument, text: str) -> None:
    _atomic_write_bytes(document.path, _encode_js_document(document, text))


def _entry_presence_at(js_dir: Path) -> Tuple[bool, Optional[str]]:
    path = Path(js_dir) / "plugins.js"
    if not path.is_file():
        return False, None
    try:
        document = _read_plugins_document(path)
        analysis = _analyze_plugins(document.text)
    except Exception as exc:
        return False, str(exc)
    return any(entry.name == PLUGIN_NAME for entry in analysis.entries), None


def upsert_loader_entry(layout: GameLayout) -> InstallResult:
    """Add or replace the marked translation entry in plugins.js."""

    path = _plugins_js_path(layout)
    warnings: List[str] = []
    backup_path: Optional[Path] = None
    created_backup = False
    wrote_file = False
    document: Optional[_JsDocument] = None
    try:
        document = _read_plugins_document(path)
        updated, had_entry = _upsert_entry_text(document.text, document.newline)
        backup = _plugins_js_backup_path(layout)
        if updated == document.text:
            return InstallResult(
                ok=True,
                status="noop",
                message="Translation loader entry already up to date.",
                backup_path=str(backup) if backup.exists() else None,
                changed=False,
            )
        if not had_entry and not backup.exists():
            _atomic_write_bytes(backup, document.raw)
            backup_path = backup
            created_backup = True
        _write_plugins_document(document, updated)
        wrote_file = True
        check = _read_plugins_document(path)
        check_analysis = _analyze_plugins(check.text)
        if sum(1 for entry in check_analysis.entries if entry.name == PLUGIN_NAME) != 1:
            raise _PluginsJsError(
                "plugins.js validation failed after writing the loader entry."
            )
    except Exception as exc:
        if wrote_file and document is not None:
            try:
                _restore_bytes(path, document.raw)
            except OSError as rollback_exc:
                warnings.append(f"Could not restore plugins.js: {rollback_exc}")
        if created_backup and backup_path is not None:
            try:
                backup_path.unlink()
            except OSError:
                pass
        return InstallResult(
            ok=False,
            status="error",
            message=f"Could not update plugins.js: {exc}",
            error=str(exc),
            warnings=warnings,
        )

    return InstallResult(
        ok=True,
        status="installed",
        message="Translation loader entry ensured.",
        warnings=warnings,
        installed=[str(path)],
        backup_path=str(backup_path)
        if backup_path is not None
        else (str(backup) if backup.exists() else None),
        changed=True,
    )


def _remove_loader_entry_at(js_dir: Path) -> InstallResult:
    path = Path(js_dir) / "plugins.js"
    if not path.is_file():
        return InstallResult(
            ok=True,
            status="noop",
            message="plugins.js not present; loader entry already absent.",
        )
    try:
        document = _read_plugins_document(path)
        updated, removed = _remove_entry_text(document.text)
        if removed == 0:
            return InstallResult(
                ok=True,
                status="noop",
                message="Translation loader entry already absent.",
            )
        _write_plugins_document(document, updated)
    except Exception as exc:
        return InstallResult(
            ok=False,
            status="error",
            message=f"Could not remove the translation loader entry: {exc}",
            error=str(exc),
        )
    return InstallResult(
        ok=True,
        status="removed",
        message="Translation loader entry removed.",
        removed=[str(path)],
        changed=True,
    )


def remove_loader_entry(layout: GameLayout) -> InstallResult:
    """Remove the marked entry without touching translation artifacts."""

    return _remove_loader_entry_at(layout.js_dir)


# ---------------------------------------------------------------------------
# Config / state / status
# ---------------------------------------------------------------------------


def _normalize_config(
    value: Any, warnings: Optional[List[str]] = None
) -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if not isinstance(value, Mapping):
        return config
    if isinstance(value.get("enabled"), bool):
        config["enabled"] = value["enabled"]
    elif "enabled" in value and warnings is not None:
        warnings.append("Config field 'enabled' is not a boolean; using false.")
    if isinstance(value.get("fontFace"), str):
        config["fontFace"] = value["fontFace"]
    elif "fontFace" in value and warnings is not None:
        warnings.append("Config field 'fontFace' is not a string; using empty.")
    if isinstance(value.get("wrap"), bool):
        config["wrap"] = value["wrap"]
    elif "wrap" in value and warnings is not None:
        warnings.append("Config field 'wrap' is not a boolean; using true.")
    if isinstance(value.get("debug"), bool):
        config["debug"] = value["debug"]
    elif "debug" in value and warnings is not None:
        warnings.append("Config field 'debug' is not a boolean; using false.")
    if isinstance(value.get("titleOverlay"), bool):
        config["titleOverlay"] = value["titleOverlay"]
    for key, low, high in (
        ("titleOverlayX", -2000, 2000),
        ("titleOverlayY", -2000, 2000),
        ("titleOverlayFontSize", 8, 200),
        ("titleOverlayBackground", 0, 255),
    ):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue
        config[key] = max(low, min(high, raw))
    raw_labels = value.get("uiLabels")
    if isinstance(raw_labels, list):
        labels: List[str] = []
        seen_labels: Set[str] = set()
        for raw_label in raw_labels:
            if not isinstance(raw_label, str):
                continue
            label = _normalize_text(raw_label).strip()
            if not label or len(label) > 240 or label in seen_labels:
                continue
            seen_labels.add(label)
            labels.append(label)
            if len(labels) >= 2000:
                break
        config["uiLabels"] = labels
    return config


def _load_config_info(layout: GameLayout) -> _ConfigInfo:
    path = layout.plugin_dir / CONFIG_NAME
    info = _load_json_file(path)
    warnings: List[str] = []
    if info.error:
        warnings.append(info.error)
        return _ConfigInfo(dict(DEFAULT_CONFIG), True, False, warnings)
    if not info.exists:
        return _ConfigInfo(dict(DEFAULT_CONFIG), False, False, warnings)
    if not isinstance(info.value, dict):
        warnings.append(f"Translation config is not a JSON object: {path}")
        return _ConfigInfo(dict(DEFAULT_CONFIG), True, False, warnings)
    config = _normalize_config(info.value, warnings)
    return _ConfigInfo(config, True, True, warnings)


def read_config(layout: GameLayout) -> Dict[str, Any]:
    """Read the runtime config, returning safe defaults when missing/invalid."""

    return _load_config_info(layout).config


def write_config(layout: GameLayout, config: Mapping[str, Any]) -> Dict[str, Any]:
    """Atomically write a sanitized runtime config into the game folder."""

    try:
        _recover_pending_install(layout)
    except Exception as exc:
        raise TranslationError(
            f"Could not recover a pending translation install: {exc}"
        ) from exc
    if _txn_path(layout.plugin_dir).is_file():
        raise TranslationError(
            "A pending translation install must be repaired before changing settings."
        )
    if _corrupt_txn_path(layout.plugin_dir).is_file():
        raise TranslationError(
            "An invalid translation transaction is quarantined; re-load or remove the translation."
        )
    normalized = _normalize_config(config)
    layout.plugin_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(layout.plugin_dir / CONFIG_NAME, normalized)
    return normalized


def read_state(layout: GameLayout) -> Optional[Dict[str, Any]]:
    """Read launcher metadata from the game folder, if present/valid."""

    info = _load_json_file(layout.plugin_dir / STATE_NAME)
    if not info.exists or info.error or not isinstance(info.value, dict):
        return None
    return dict(info.value)


def read_translation_state(
    game_path_or_layout: Any, layout: Optional[GameLayout] = None
) -> Optional[Dict[str, Any]]:
    """Compatibility helper accepting either a GameLayout or a game path."""

    if isinstance(game_path_or_layout, GameLayout):
        selected = game_path_or_layout
    elif layout is not None:
        selected = layout
    else:
        selected = detect_game_layout(game_path_or_layout)
    if selected is None:
        return None
    return read_state(selected)


def _normalize_state(state: Any, entry_count: int) -> Dict[str, Any]:
    result: Dict[str, Any] = dict(state) if isinstance(state, Mapping) else {}
    result.setdefault("schema_version", 1)
    result.setdefault("entry_count", entry_count)
    result.setdefault("updated_at", _now_iso())
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        result = {
            "schema_version": 1,
            "entry_count": entry_count,
            "updated_at": _now_iso(),
        }
    return result


def get_status(layout: GameLayout) -> Dict[str, Any]:
    """Return a UI-friendly snapshot of the per-game translation state."""

    warnings: List[str] = []
    try:
        warnings.extend(_recover_pending_install(layout))
    except Exception as exc:
        warnings.append(f"Could not recover a pending translation install: {exc}")
    config_info = _load_config_info(layout)
    warnings.extend(config_info.warnings)
    config = config_info.config

    dict_path = layout.plugin_dir / DICTIONARY_NAME
    plugin_path = layout.plugin_dir / PLUGIN_JS_NAME
    config_path = layout.plugin_dir / CONFIG_NAME
    state_path = layout.plugin_dir / STATE_NAME
    txn_path = _txn_path(layout.plugin_dir)
    corrupt_txn_path = _corrupt_txn_path(layout.plugin_dir)

    dictionary_exists = dict_path.is_file()
    plugin_exists = plugin_path.is_file()
    state_exists = state_path.is_file()
    files_present = (
        dictionary_exists
        or plugin_exists
        or config_path.is_file()
        or state_exists
        or txn_path.is_file()
        or corrupt_txn_path.is_file()
    )

    data = load_translation(dict_path) if dictionary_exists else None
    if data is not None:
        warnings.extend(data.warnings)
    entry_count = len(data.entries) if data is not None else 0

    state = read_state(layout)
    if state_exists and state is None:
        warnings.append("Translation metadata is invalid; it can be rebuilt on launch.")

    pending_txn = txn_path.is_file()
    corrupt_txn = corrupt_txn_path.is_file()
    if pending_txn:
        warnings.append("A pending translation install could not be recovered.")
    if corrupt_txn:
        warnings.append(
            "An invalid translation transaction is quarantined; re-load the translation."
        )
    active = bool(
        config.get("enabled")
        and config_info.valid
        and dictionary_exists
        and plugin_exists
        and data is not None
        and data.ok
        and entry_count > 0
        and not pending_txn
        and not corrupt_txn
    )
    if active:
        if state is None:
            warnings.append("Translation active, metadata missing.")
        entry_present, entry_error = _entry_presence_at(layout.js_dir)
        if entry_error:
            warnings.append(f"plugins.js could not be inspected: {entry_error}")
        elif not entry_present:
            warnings.append(
                "Translation loader entry is missing; it will be restored on launch."
            )
    elif files_present:
        if not config_info.valid:
            warnings.append("Translation config is missing or invalid.")
        if not dictionary_exists:
            warnings.append("Translation dictionary is missing.")
        if not plugin_exists:
            warnings.append("Translation plugin JS is missing.")

    # Selecting a game in the UI keeps the path-only index in sync, which
    # covers games that were copied or moved after installation.  Exported
    # standalone apps are deliberately not recorded.
    try:
        if _inside_app_bundle(layout.game_path):
            pass
        elif files_present:
            upsert_cleanup_index(layout)
        else:
            orphan = cleanup_orphan_entry(layout)
            warnings.extend(orphan.warnings)
            if not orphan.ok:
                warnings.append(orphan.message)
    except Exception as exc:
        warnings.append(f"Could not update the translation cleanup index: {exc}")

    return {
        "active": active,
        "enabled": bool(config.get("enabled")),
        "entry_count": entry_count,
        "pending_transaction": pending_txn,
        "corrupt_transaction": corrupt_txn,
        "state": state,
        "config": config,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Path-only cleanup index
# ---------------------------------------------------------------------------


def read_cleanup_index() -> Dict[str, str]:
    """Read the path-only cleanup index.

    A missing index returns an empty mapping.  A malformed index raises
    :class:`TranslationError` so callers can decide whether to warn or abort.
    """

    path = cleanup_index_path()
    info = _load_json_file(path)
    if not info.exists:
        return {}
    if info.error:
        raise TranslationError(info.error)
    if not isinstance(info.value, dict):
        raise TranslationError(f"Cleanup index is not a JSON object: {path}")
    result: Dict[str, str] = {}
    for key, value in info.value.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TranslationError(
                f"Cleanup index contains non-string path data: {path}"
            )
        result[key] = value
    return result


def write_cleanup_index(index: Mapping[str, str]) -> None:
    cleanup_index_path().parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(cleanup_index_path(), dict(index))


def upsert_cleanup_index(layout: GameLayout) -> None:
    index = read_cleanup_index()
    index[str(layout.game_path)] = str(layout.plugin_dir)
    write_cleanup_index(index)


def remove_cleanup_index_entry(game_path: Any) -> None:
    index = read_cleanup_index()
    key = str(_absolute_path(game_path))
    if key not in index:
        # Also tolerate callers using the exact stored representation.
        key = str(game_path)
    if key in index:
        del index[key]
        write_cleanup_index(index)


def _try_upsert_cleanup_index(layout: GameLayout, warnings: List[str]) -> None:
    try:
        upsert_cleanup_index(layout)
    except Exception as exc:
        warnings.append(
            f"Translation installed, but the cleanup index could not be updated: {exc}"
        )


def _try_remove_cleanup_index_entry(layout: GameLayout, warnings: List[str]) -> None:
    try:
        remove_cleanup_index_entry(layout.game_path)
    except Exception as exc:
        warnings.append(
            f"Translation removed, but the cleanup index could not be updated: {exc}"
        )


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def _plugin_source_bytes() -> Tuple[bytes, Optional[str]]:
    source = Path(PLUGIN_JS_SOURCE)
    try:
        data = source.read_bytes()
    except FileNotFoundError as exc:
        raise TranslationError(
            f"Translation plugin asset is missing: {source}. "
            "The launcher package is incomplete."
        ) from exc
    except OSError as exc:
        raise TranslationError(f"Could not read plugin asset: {exc}") from exc
    if not data.strip():
        raise TranslationError(
            f"Translation plugin asset is empty: {source}. "
            "The launcher package is incomplete."
        )
    return data, None


def _normalize_entries(entries: Any) -> Tuple[Dict[str, str], int]:
    if isinstance(entries, TranslationData):
        source: Mapping[Any, Any] = entries.entries
    elif isinstance(entries, Mapping):
        source = entries
    else:
        raise TranslationError("Translation entries must be a mapping.")

    normalized: Dict[str, str] = {}
    invalid = 0
    for raw_key, raw_value in source.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            invalid += 1
            continue
        key = _normalize_text(raw_key)
        value = _normalize_text(raw_value)
        if _is_blank(key) or _is_blank(value) or value == key:
            invalid += 1
            continue
        normalized[key] = value
    return normalized, invalid


def _validate_staged_file(
    path: Path, expected: bytes, *, json_object: bool = False
) -> None:
    actual = path.read_bytes()
    if actual != expected:
        raise TranslationError(f"Staged file validation failed for {path}.")
    if json_object:
        try:
            value = json.loads(actual.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TranslationError(f"Staged JSON is invalid for {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise TranslationError(f"Staged JSON is not an object for {path}.")


def _ensure_plugin_file(layout: GameLayout) -> Tuple[bool, Optional[str]]:
    target = layout.plugin_dir / PLUGIN_JS_NAME
    if target.is_file():
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise TranslationError(
                f"Could not read installed plugin JS: {exc}"
            ) from exc
        try:
            plugin_bytes, warning = _plugin_source_bytes()
        except TranslationError as exc:
            return False, (
                "Bundled translation plugin asset is unavailable; "
                f"using the installed plugin. ({exc})"
            )
        if existing == plugin_bytes:
            return False, warning
        _atomic_write_bytes(target, plugin_bytes)
        return True, warning
    plugin_bytes, warning = _plugin_source_bytes()
    _atomic_write_bytes(target, plugin_bytes)
    return True, warning


def install_translation(
    layout: GameLayout,
    entries: Any,
    config: Optional[Mapping[str, Any]] = None,
    state: Optional[Mapping[str, Any]] = None,
) -> InstallResult:
    """Atomically install dictionary/config/state/plugin and the loader entry."""

    if not isinstance(layout, GameLayout):
        return InstallResult(
            ok=False,
            status="error",
            message="Invalid game layout.",
            error="Invalid layout",
        )

    warnings: List[str] = []
    try:
        warnings.extend(_recover_pending_install(layout))
    except Exception as exc:
        warnings.append(f"Could not recover a pending translation install: {exc}")
    if _txn_path(layout.plugin_dir).is_file():
        return InstallResult(
            ok=False,
            status="error",
            message="A pending translation install could not be recovered.",
            error="Pending transaction",
            warnings=warnings,
        )
    corrupt_txn = _corrupt_txn_path(layout.plugin_dir)
    if corrupt_txn.is_file():
        warnings.append(
            "A quarantined invalid transaction manifest will be replaced by this install."
        )
    try:
        normalized_entries, invalid_count = _normalize_entries(entries)
    except TranslationError as exc:
        return InstallResult(ok=False, status="error", message=str(exc), error=str(exc))
    if invalid_count:
        warnings.append(
            f"{invalid_count} invalid/empty/identity translation value(s) were skipped."
        )
    if not normalized_entries:
        return InstallResult(
            ok=False,
            status="error",
            message="The translation dictionary has no non-empty translations.",
            error="Empty dictionary",
            warnings=warnings,
        )

    normalized_config = _normalize_config(config)
    normalized_state = _normalize_state(state, len(normalized_entries))

    try:
        plugin_bytes, plugin_warning = _plugin_source_bytes()
        if plugin_warning:
            warnings.append(plugin_warning)
    except TranslationError as exc:
        installed_plugin = layout.plugin_dir / PLUGIN_JS_NAME
        if installed_plugin.is_file():
            try:
                plugin_bytes = installed_plugin.read_bytes()
            except OSError as read_exc:
                return InstallResult(
                    ok=False,
                    status="error",
                    message=f"Could not read the installed translation plugin: {read_exc}",
                    error=str(read_exc),
                )
            warnings.append(
                "Bundled translation plugin asset is unavailable; "
                f"using the installed plugin. ({exc})"
            )
        else:
            return InstallResult(
                ok=False, status="error", message=str(exc), error=str(exc)
            )

    plugin_dir = layout.plugin_dir
    js_path = layout.plugins_js
    targets = [
        plugin_dir / PLUGIN_JS_NAME,
        plugin_dir / DICTIONARY_NAME,
        plugin_dir / CONFIG_NAME,
        plugin_dir / STATE_NAME,
    ]
    contents = [
        plugin_bytes,
        _json_bytes(normalized_entries),
        _json_bytes(normalized_config),
        _json_bytes(normalized_state),
    ]

    # Preflight plugins.js before touching any game file.
    try:
        document = _read_plugins_document(js_path)
        updated_js, had_entry = _upsert_entry_text(document.text, document.newline)
        updated_js_bytes = _encode_js_document(document, updated_js)
    except Exception as exc:
        return InstallResult(
            ok=False,
            status="error",
            message=f"Translation could not be installed: {exc}",
            error=str(exc),
            warnings=warnings,
        )

    try:
        previous = _read_target_bytes(targets + [js_path])
    except TranslationError as exc:
        return InstallResult(ok=False, status="error", message=str(exc), error=str(exc))
    previous_js = previous[js_path]

    staged: List[Tuple[Path, Path]] = []
    backup_path: Optional[Path] = None
    created_backup = False
    replaced_targets: List[Path] = []
    js_replaced = False
    txn_path: Optional[Path] = None

    try:
        plugin_dir.mkdir(parents=True, exist_ok=True)
        for target, data in zip(targets, contents):
            temp_path = _write_temp_bytes(target, data)
            staged.append((target, temp_path))
            _validate_staged_file(temp_path, data, json_object=target.suffix == ".json")

        new_contents: Dict[Path, bytes] = dict(zip(targets, contents))
        new_contents[js_path] = updated_js_bytes
        txn_path = _write_transaction(layout, previous, new_contents)

        backup = _plugins_js_backup_path(layout)
        if not had_entry and not backup.exists():
            _atomic_write_bytes(backup, document.raw)
            backup_path = backup
            created_backup = True

        for target, temp_path in staged:
            os.replace(str(temp_path), str(target))
            replaced_targets.append(target)

        _atomic_write_bytes(js_path, updated_js_bytes)
        js_replaced = True

        # Re-read the installed plugins.js and JSON files as a final validation.
        check_document = _read_plugins_document(js_path)
        check_analysis = _analyze_plugins(check_document.text)
        if sum(1 for entry in check_analysis.entries if entry.name == PLUGIN_NAME) != 1:
            raise TranslationError(
                "plugins.js validation failed after installing the loader entry."
            )
        for target in targets[1:3]:
            check = _load_json_file(target)
            if not check.exists or check.error or not isinstance(check.value, dict):
                raise TranslationError(
                    f"Installed file failed validation: {target}: {check.error}"
                )
    except Exception as exc:
        rollback_warnings: List[str] = []
        for target in replaced_targets:
            try:
                _restore_bytes(target, previous[target])
            except OSError as rollback_exc:
                rollback_warnings.append(f"Could not restore {target}: {rollback_exc}")
        if js_replaced:
            try:
                _restore_bytes(js_path, previous_js)
            except OSError as rollback_exc:
                rollback_warnings.append(
                    f"Could not restore plugins.js: {rollback_exc}"
                )
        if created_backup and backup_path is not None:
            try:
                backup_path.unlink()
            except OSError:
                pass
        if txn_path is not None and not rollback_warnings:
            try:
                _delete_transaction(txn_path)
            except OSError as txn_exc:
                rollback_warnings.append(
                    f"Could not remove the transaction manifest: {txn_exc}"
                )
        for _target, temp_path in staged:
            try:
                temp_path.unlink()
            except OSError:
                pass
        warnings.extend(rollback_warnings)
        return InstallResult(
            ok=False,
            status="error",
            message=f"Translation install failed and was rolled back: {exc}",
            error=str(exc),
            warnings=warnings,
        )
    finally:
        for _target, temp_path in staged:
            try:
                temp_path.unlink()
            except OSError:
                pass

    if txn_path is not None:
        try:
            _delete_transaction(txn_path)
        except OSError as txn_exc:
            warnings.append(f"Could not remove the transaction manifest: {txn_exc}")
    if corrupt_txn.is_file():
        try:
            _unlink_file(corrupt_txn)
            warnings.append("Removed the quarantined invalid transaction manifest.")
        except OSError as exc:
            warnings.append(
                f"Could not remove the quarantined transaction manifest: {exc}"
            )
    _try_upsert_cleanup_index(layout, warnings)
    backup = _plugins_js_backup_path(layout)
    return InstallResult(
        ok=True,
        status="installed",
        message=(
            f"Translation installed ({len(normalized_entries)} entries). "
            "Restart the game to apply it."
        ),
        warnings=warnings,
        installed=[str(path) for path in targets],
        backup_path=str(backup) if backup.exists() else None,
        changed=True,
    )


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------


def set_enabled(layout: GameLayout, enabled: bool) -> InstallResult:
    """Enable or disable the installed translation without removing files."""

    if not isinstance(layout, GameLayout):
        return InstallResult(
            ok=False,
            status="error",
            message="Invalid game layout.",
            error="Invalid layout",
        )
    enabled = bool(enabled)
    warnings: List[str] = []
    try:
        warnings.extend(_recover_pending_install(layout))
    except Exception as exc:
        warnings.append(f"Could not recover a pending translation install: {exc}")
    if _txn_path(layout.plugin_dir).is_file():
        return InstallResult(
            ok=False,
            status="error",
            message="A pending translation install could not be recovered.",
            error="Pending transaction",
            warnings=warnings,
        )
    if _corrupt_txn_path(layout.plugin_dir).is_file():
        return InstallResult(
            ok=False,
            status="error",
            message=(
                "An invalid translation transaction is quarantined; "
                "re-load or remove the translation."
            ),
            error="Corrupt transaction",
            warnings=warnings,
        )
    dict_path = layout.plugin_dir / DICTIONARY_NAME
    config_path = layout.plugin_dir / CONFIG_NAME

    if enabled:
        if not dict_path.is_file():
            return InstallResult(
                ok=False,
                status="error",
                message="Cannot enable translation: dictionary is missing.",
                error="Dictionary missing",
            )
        data = load_translation(dict_path)
        warnings.extend(data.warnings)
        if not data.ok or not data.entries:
            return InstallResult(
                ok=False,
                status="error",
                message="Cannot enable translation: dictionary has no effective entries.",
                error=data.error or "Empty dictionary",
                warnings=warnings,
            )

        created_plugin = False
        try:
            created_plugin, plugin_warning = _ensure_plugin_file(layout)
            if plugin_warning:
                warnings.append(plugin_warning)
        except Exception as exc:
            return InstallResult(
                ok=False,
                status="error",
                message=f"Could not ensure the translation plugin: {exc}",
                error=str(exc),
                warnings=warnings,
            )

        # Validate plugins.js before persisting enabled=true.
        try:
            document = _read_plugins_document(layout.plugins_js)
            _upsert_entry_text(document.text, document.newline)
        except Exception as exc:
            if created_plugin:
                try:
                    (layout.plugin_dir / PLUGIN_JS_NAME).unlink()
                except OSError:
                    pass
            return InstallResult(
                ok=False,
                status="error",
                message=f"Could not enable translation: {exc}",
                error=str(exc),
                warnings=warnings,
            )

        old_config: Optional[bytes]
        try:
            old_config = config_path.read_bytes() if config_path.exists() else None
        except OSError as exc:
            return InstallResult(
                ok=False,
                status="error",
                message=f"Could not read translation config: {exc}",
                error=str(exc),
                warnings=warnings,
            )

        config = read_config(layout)
        config["enabled"] = True
        try:
            _atomic_write_json(config_path, config)
            loader = upsert_loader_entry(layout)
            if not loader.ok:
                raise TranslationError(loader.message)
            warnings.extend(loader.warnings)
        except Exception as exc:
            try:
                _restore_bytes(config_path, old_config)
            except OSError as rollback_exc:
                warnings.append(f"Could not restore config: {rollback_exc}")
            if created_plugin:
                try:
                    (layout.plugin_dir / PLUGIN_JS_NAME).unlink()
                except OSError:
                    pass
            return InstallResult(
                ok=False,
                status="error",
                message=f"Could not enable translation: {exc}",
                error=str(exc),
                warnings=warnings,
            )

        _try_upsert_cleanup_index(layout, warnings)
        return InstallResult(
            ok=True,
            status="enabled",
            message="Translation enabled. Restart the game to apply it.",
            warnings=warnings,
            changed=True,
        )

    # Disable: write enabled=false first so the plugin itself no-ops even if
    # loader entry removal is impossible.
    files_exist = any(
        (layout.plugin_dir / name).is_file()
        for name in (PLUGIN_JS_NAME, DICTIONARY_NAME, CONFIG_NAME, STATE_NAME)
    )
    if files_exist:
        config = read_config(layout)
        config["enabled"] = False
        try:
            _atomic_write_json(config_path, config)
        except Exception as exc:
            return InstallResult(
                ok=False,
                status="error",
                message=f"Could not disable translation: {exc}",
                error=str(exc),
                warnings=warnings,
            )

    loader = remove_loader_entry(layout)
    if not loader.ok:
        # Keep the config disabled: the plugin checks config.enabled itself, so
        # a failed loader-entry removal cannot keep the translation active.
        if files_exist:
            _try_upsert_cleanup_index(layout, warnings)
        return InstallResult(
            ok=False,
            status="error",
            message=(
                "Translation config was disabled, but the loader entry could not "
                f"be removed: {loader.message}"
            ),
            error=loader.error or loader.message,
            warnings=warnings,
        )
    warnings.extend(loader.warnings)

    if files_exist:
        _try_upsert_cleanup_index(layout, warnings)
    else:
        _try_remove_cleanup_index_entry(layout, warnings)

    return InstallResult(
        ok=True,
        status="disabled",
        message="Translation disabled. Files remain for re-enabling.",
        warnings=warnings,
        changed=True,
    )


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


@dataclass
class _RemoveFilesResult:
    ok: bool
    removed: List[Path] = field(default_factory=list)
    message: str = ""
    error: str = ""


def _remove_translation_files(plugin_dir: Path) -> _RemoveFilesResult:
    """Remove plugin/dictionary/config, then state last."""

    paths = [
        plugin_dir / PLUGIN_JS_NAME,
        plugin_dir / DICTIONARY_NAME,
        plugin_dir / CONFIG_NAME,
        plugin_dir / STATE_NAME,
        plugin_dir / TXN_NAME,
        plugin_dir / (TXN_NAME + ".corrupt"),
        plugin_dir.parent / PLUGINS_BACKUP_NAME,
    ]
    removed: List[Path] = []
    for path in paths:
        try:
            existed = path.is_file()
            _unlink_file(path)
            if existed:
                removed.append(path)
        except OSError as exc:
            return _RemoveFilesResult(
                ok=False,
                removed=removed,
                message=f"Could not remove {path}: {exc}",
                error=str(exc),
            )
    return _RemoveFilesResult(ok=True, removed=removed)


def _remove_translation_at(
    layout: GameLayout, *, update_index: bool = True, force: bool = False
) -> InstallResult:
    warnings: List[str] = []
    try:
        warnings.extend(_recover_pending_install(layout))
    except Exception as exc:
        warnings.append(f"Could not recover a pending translation install: {exc}")
    unresolved_txn = _txn_path(layout.plugin_dir).is_file()
    corrupt_txn = _corrupt_txn_path(layout.plugin_dir).is_file()
    if (unresolved_txn or corrupt_txn) and not force:
        return InstallResult(
            ok=False,
            status="error",
            message="A pending or invalid translation install could not be recovered.",
            error="Pending transaction",
            warnings=warnings,
        )
    if force and unresolved_txn:
        warnings.append("Discarded an unresolved translation transaction manifest.")
    if force and corrupt_txn:
        warnings.append("Discarded a quarantined invalid transaction manifest.")
    loader = _remove_loader_entry_at(layout.js_dir)
    if not loader.ok:
        return InstallResult(
            ok=False,
            status="error",
            message=loader.message,
            error=loader.error or loader.message,
            warnings=warnings,
        )
    warnings.extend(loader.warnings)

    files = _remove_translation_files(layout.plugin_dir)
    if not files.ok:
        return InstallResult(
            ok=False,
            status="error",
            message=files.message,
            error=files.error or files.message,
            warnings=warnings,
            removed=[str(path) for path in files.removed],
        )
    if update_index:
        _try_remove_cleanup_index_entry(layout, warnings)
    return InstallResult(
        ok=True,
        status="removed",
        message="Translation removed. Restart the game to see original text.",
        warnings=warnings,
        removed=[str(path) for path in files.removed],
        changed=bool(files.removed) or loader.changed,
    )


def remove_translation(layout: GameLayout) -> InstallResult:
    """Remove loader entry, artifacts (state last) and the cleanup index entry."""

    if not isinstance(layout, GameLayout):
        return InstallResult(
            ok=False,
            status="error",
            message="Invalid game layout.",
            error="Invalid layout",
        )
    return _remove_translation_at(layout, update_index=True, force=True)


def cleanup_orphan_entry(layout: GameLayout) -> InstallResult:
    """Remove a loader entry/index record when no translation files remain."""

    plugin_dir = layout.plugin_dir
    try:
        files_present = any(
            _is_regular_file(plugin_dir / name)
            for name in (
                PLUGIN_JS_NAME,
                DICTIONARY_NAME,
                CONFIG_NAME,
                STATE_NAME,
                TXN_NAME,
                TXN_NAME + ".corrupt",
            )
        )
    except TranslationError as exc:
        return InstallResult(
            ok=False,
            status="error",
            message=str(exc),
            error=str(exc),
        )
    if files_present:
        return InstallResult(
            ok=True,
            status="noop",
            message="Translation files are present; no orphan cleanup needed.",
        )
    loader = _remove_loader_entry_at(layout.js_dir)
    if not loader.ok:
        return loader
    warnings = list(loader.warnings)
    _try_remove_cleanup_index_entry(layout, warnings)
    return InstallResult(
        ok=True,
        status="removed" if loader.changed else "noop",
        message=(
            "Orphan translation loader entry removed."
            if loader.changed
            else "No orphan translation entry found."
        ),
        warnings=warnings,
        removed=loader.removed,
        changed=loader.changed,
    )


# ---------------------------------------------------------------------------
# Launch preparation / standalone copy cleanup
# ---------------------------------------------------------------------------


def _minimal_state(
    entry_count: int, existing: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    state: Dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
    state.setdefault("schema_version", 1)
    state.setdefault("entry_count", entry_count)
    state.setdefault("updated_at", _now_iso())
    return state


def prepare_for_launch(game_path: Any) -> LaunchResult:
    """Ensure the per-game translation is consistent before launch."""

    layout = detect_game_layout(game_path)
    if layout is None:
        return LaunchResult(
            status="unsupported",
            active=False,
            message="Unsupported game: no standard MV/MZ layout detected.",
        )

    warnings: List[str] = []
    try:
        warnings.extend(_recover_pending_install(layout))
    except Exception as exc:
        warnings.append(f"Could not recover a pending translation install: {exc}")
    plugin_dir = layout.plugin_dir
    unresolved_txn = _txn_path(plugin_dir).is_file()
    corrupt_txn = _corrupt_txn_path(plugin_dir).is_file()
    if unresolved_txn or corrupt_txn:
        loader = _remove_loader_entry_at(layout.js_dir)
        warnings.extend(loader.warnings)
        if not loader.ok:
            warnings.append(loader.message)
        try:
            if _artifacts_present(plugin_dir):
                _try_upsert_cleanup_index(layout, warnings)
        except TranslationError as exc:
            warnings.append(str(exc))
        return LaunchResult(
            status="error",
            active=False,
            message=(
                "A pending or invalid translation transaction must be repaired; "
                "re-load the translation. Launching untranslated."
            ),
            warnings=warnings,
        )
    dict_path = plugin_dir / DICTIONARY_NAME
    plugin_path = plugin_dir / PLUGIN_JS_NAME
    config_path = plugin_dir / CONFIG_NAME
    state_path = plugin_dir / STATE_NAME
    txn_path = _txn_path(plugin_dir)
    corrupt_txn_path = _corrupt_txn_path(plugin_dir)

    config_info = _load_config_info(layout)
    warnings.extend(config_info.warnings)
    config = config_info.config

    dictionary_exists = dict_path.is_file()
    plugin_exists = plugin_path.is_file()
    config_exists = config_path.is_file()
    state_exists = state_path.is_file()
    files_present = (
        dictionary_exists
        or plugin_exists
        or config_exists
        or state_exists
        or txn_path.is_file()
        or corrupt_txn_path.is_file()
    )

    if not files_present:
        orphan = cleanup_orphan_entry(layout)
        warnings.extend(orphan.warnings)
        if not orphan.ok:
            warnings.append(orphan.message)
        return LaunchResult(
            status="none",
            active=False,
            message="No translation is installed for this game.",
            warnings=warnings,
        )

    data = load_translation(dict_path) if dictionary_exists else None
    if data is not None:
        warnings.extend(data.warnings)

    # Missing/invalid config: a surviving dictionary is not enough.  Remove
    # any loader entry and launch untranslated.
    if not config_info.valid:
        if not config_info.exists:
            warnings.append("Translation config file is missing.")
        loader = _remove_loader_entry_at(layout.js_dir)
        warnings.extend(loader.warnings)
        if not loader.ok:
            warnings.append(loader.message)
        if dictionary_exists or plugin_exists or state_exists:
            _try_upsert_cleanup_index(layout, warnings)
        message = "Translation config is missing or invalid; launching untranslated."
        return LaunchResult(
            status="error", active=False, message=message, warnings=warnings
        )

    if config.get("enabled"):
        if not dictionary_exists:
            warnings.append("Translation dictionary file is missing.")
            loader = _remove_loader_entry_at(layout.js_dir)
            warnings.extend(loader.warnings)
            if not loader.ok:
                warnings.append(loader.message)
            _try_upsert_cleanup_index(layout, warnings)
            return LaunchResult(
                status="error",
                active=False,
                message="Translation dictionary is missing; launching untranslated.",
                warnings=warnings,
            )
        if data is None or not data.ok or not data.entries:
            loader = _remove_loader_entry_at(layout.js_dir)
            warnings.extend(loader.warnings)
            if not loader.ok:
                warnings.append(loader.message)
            _try_upsert_cleanup_index(layout, warnings)
            return LaunchResult(
                status="error",
                active=False,
                message="Translation dictionary is invalid; launching untranslated.",
                warnings=warnings,
            )

        plugin_existed_before = plugin_path.is_file()
        try:
            created_plugin, plugin_warning = _ensure_plugin_file(layout)
            if plugin_warning:
                warnings.append(plugin_warning)
        except Exception as exc:
            loader = _remove_loader_entry_at(layout.js_dir)
            warnings.extend(loader.warnings)
            warnings.append(f"Could not ensure translation plugin: {exc}")
            return LaunchResult(
                status="error",
                active=False,
                message="Translation plugin could not be installed; launching untranslated.",
                warnings=warnings,
            )

        loader = upsert_loader_entry(layout)
        warnings.extend(loader.warnings)
        if not loader.ok:
            if created_plugin and not plugin_existed_before:
                try:
                    _unlink_file(plugin_path)
                except OSError as unlink_exc:
                    warnings.append(
                        f"Could not remove the newly created plugin: {unlink_exc}"
                    )
            return LaunchResult(
                status="error",
                active=False,
                message="Translation loader entry could not be ensured; launching untranslated.",
                warnings=warnings,
            )

        state = read_state(layout) if state_exists else None
        state_rebuilt = False
        if state is None:
            if state_exists:
                warnings.append("Translation metadata was invalid; rebuilding it.")
            try:
                _atomic_write_json(state_path, _minimal_state(len(data.entries), state))
                state_rebuilt = True
                if state_exists:
                    warnings.append("Translation metadata was rebuilt.")
            except Exception as exc:
                warnings.append(f"Could not rebuild translation metadata: {exc}")

        _try_upsert_cleanup_index(layout, warnings)
        message = f"Translation active ({len(data.entries)} entries)."
        if state_rebuilt:
            message = (
                f"Translation active ({len(data.entries)} entries), metadata rebuilt."
            )
        return LaunchResult(
            status="active", active=True, message=message, warnings=warnings
        )

    # Valid config with enabled=false: remove the loader entry, keep files.
    loader = _remove_loader_entry_at(layout.js_dir)
    warnings.extend(loader.warnings)
    if not loader.ok:
        warnings.append(loader.message)
    _try_upsert_cleanup_index(layout, warnings)
    return LaunchResult(
        status="disabled",
        active=False,
        message="Translation is disabled; files are kept for re-enabling.",
        warnings=warnings,
    )


def loader_entry_present(layout: GameLayout) -> Tuple[bool, Optional[str]]:
    """Return whether the translator loader entry is present in plugins.js."""

    return _entry_presence_at(layout.js_dir)


def clean_export_metadata(game_path: Any) -> InstallResult:
    """Remove launcher-only metadata/backup files from an exported app copy."""

    layout = detect_game_layout(game_path)
    if layout is None:
        return InstallResult(
            ok=False,
            status="error",
            message="Unsupported game layout in exported app.",
            error="Unsupported layout",
        )
    removed: List[str] = []
    paths = [
        layout.plugin_dir / STATE_NAME,
        _txn_path(layout.plugin_dir),
        _corrupt_txn_path(layout.plugin_dir),
        _plugins_js_backup_path(layout),
    ]
    for path in paths:
        try:
            existed = Path(path).is_file()
            _unlink_file(Path(path))
            if existed:
                removed.append(str(path))
        except OSError as exc:
            return InstallResult(
                ok=False,
                status="error",
                message=f"Could not clean exported metadata {path}: {exc}",
                error=str(exc),
                removed=removed,
            )
    return InstallResult(
        ok=True,
        status="removed",
        message="Exported app metadata cleaned.",
        removed=removed,
        changed=bool(removed),
    )


def clean_app_copy(game_path: Any) -> InstallResult:
    """Remove translation artifacts from a copied standalone app only."""

    layout = detect_game_layout(game_path)
    if layout is None:
        return InstallResult(
            ok=False,
            status="error",
            message="Unsupported game layout in standalone copy.",
            error="Unsupported layout",
        )

    try:
        has_artifacts = _artifacts_present(layout.plugin_dir)
        entry_present, entry_error = _entry_presence_at(layout.js_dir)
    except Exception:
        has_artifacts = _artifacts_present(layout.plugin_dir)
        entry_present, entry_error = False, "inspection failed"
    if not has_artifacts and not entry_present:
        if entry_error:
            try:
                plugins_text = layout.plugins_js.read_text(
                    encoding="utf-8-sig", errors="replace"
                )
            except OSError as exc:
                return InstallResult(
                    ok=False,
                    status="error",
                    message=(
                        "Could not inspect the standalone copy's plugins.js "
                        f"for a translation entry: {exc}"
                    ),
                    error=str(exc),
                )
            if PLUGIN_NAME in plugins_text:
                return InstallResult(
                    ok=False,
                    status="error",
                    message=(
                        "The standalone copy may contain a stale translation "
                        "loader entry, but plugins.js could not be parsed."
                    ),
                    error="Uninspectable plugins.js with translation marker",
                )
        return InstallResult(
            ok=True,
            status="none",
            message="No translation artifacts to clean from the standalone copy.",
        )

    loader = _remove_loader_entry_at(layout.js_dir)
    if not loader.ok:
        return InstallResult(
            ok=False,
            status="error",
            message=(
                "Could not remove the translation loader entry from the "
                f"standalone copy: {loader.message}"
            ),
            error=loader.error or loader.message,
            warnings=list(loader.warnings),
        )
    files = _remove_translation_files(layout.plugin_dir)
    if not files.ok:
        return InstallResult(
            ok=False,
            status="error",
            message=files.message,
            error=files.error or files.message,
            warnings=list(loader.warnings),
            removed=[str(path) for path in files.removed],
        )
    removed = [str(path) for path in files.removed]
    backup = _plugins_js_backup_path(layout)
    try:
        if backup.is_file():
            _unlink_file(backup)
            removed.append(str(backup))
    except OSError as exc:
        return InstallResult(
            ok=False,
            status="error",
            message=f"Could not remove the exported backup file: {exc}",
            error=str(exc),
            warnings=list(loader.warnings),
            removed=removed,
        )
    return InstallResult(
        ok=True,
        status="removed",
        message="Translation files removed from standalone copy.",
        warnings=list(loader.warnings),
        removed=removed,
        changed=bool(files.removed) or loader.changed,
    )


# ---------------------------------------------------------------------------
# Uninstall All
# ---------------------------------------------------------------------------


def _artifacts_present(plugin_dir: Path) -> bool:
    return any(
        _is_regular_file(plugin_dir / name)
        for name in (
            PLUGIN_JS_NAME,
            DICTIONARY_NAME,
            CONFIG_NAME,
            STATE_NAME,
            TXN_NAME,
            TXN_NAME + ".corrupt",
        )
    )


def uninstall_all_translations() -> UninstallResult:
    """Clean every translation recorded in the path-only cleanup index.

    Failed game-folder cleanups are kept in the index so the operation can be
    retried; the index itself is never deleted.
    """

    index_path = cleanup_index_path()
    if not index_path.is_file():
        return UninstallResult(
            ok=False,
            missing_index=True,
            pruned_count=0,
            failures=[],
        )
    try:
        index = read_cleanup_index()
    except TranslationError as exc:
        return UninstallResult(
            ok=False,
            missing_index=False,
            index_error=str(exc),
            pruned_count=0,
            failures=[],
        )

    remaining: Dict[str, str] = {}
    failures: List[str] = []
    pruned = 0
    cleaned = 0

    for game_path_text, plugin_dir_text in index.items():
        game_path = Path(game_path_text)
        stored_plugin_dir = Path(plugin_dir_text)
        game_is_dir, game_error = _directory_state(game_path)
        if game_error:
            failures.append(f"{game_path_text}: {game_error}")
            remaining[game_path_text] = plugin_dir_text
            continue
        if not game_is_dir:
            pruned += 1
            continue

        layout = detect_game_layout(game_path)
        if layout is not None:
            plugin_dir = layout.plugin_dir
            js_dir = layout.js_dir
            selected_layout: Optional[GameLayout] = layout
        else:
            # Never trust a stale/corrupt index entry to point at another
            # game's plugin directory.  Only use it when it is clearly inside
            # this game folder and has the expected js/plugins shape.
            try:
                game_resolved = game_path.resolve()
                plugin_resolved = stored_plugin_dir.resolve()
            except OSError as exc:
                failures.append(f"{game_path_text}: {exc}")
                remaining[game_path_text] = plugin_dir_text
                continue
            if (
                (
                    plugin_resolved != game_resolved
                    and game_resolved not in plugin_resolved.parents
                )
                or plugin_resolved.name != "plugins"
                or plugin_resolved.parent.name != "js"
            ):
                failures.append(
                    f"{game_path_text}: cleanup index plugin path is outside the game folder"
                )
                remaining[game_path_text] = plugin_dir_text
                continue
            if _has_symlink_below(game_path, stored_plugin_dir) or _has_symlink_below(
                game_path, plugin_resolved.parent / "plugins.js"
            ):
                failures.append(
                    f"{game_path_text}: cleanup index points at a symlinked plugin path"
                )
                remaining[game_path_text] = plugin_dir_text
                continue
            plugin_dir = plugin_resolved
            js_dir = plugin_dir.parent
            selected_layout = None

        plugin_is_dir, plugin_error = _directory_state(plugin_dir)
        if plugin_error:
            failures.append(f"{game_path_text}: {plugin_error}")
            remaining[game_path_text] = plugin_dir_text
            continue
        if not plugin_is_dir:
            # The plugin directory is gone, but a stale loader entry may still
            # remain in plugins.js.  Remove it before pruning the index path.
            entry_present, entry_error = _entry_presence_at(js_dir)
            if entry_error:
                failures.append(f"{game_path_text}: {entry_error}")
                remaining[game_path_text] = plugin_dir_text
                continue
            if entry_present:
                loader = _remove_loader_entry_at(js_dir)
                if not loader.ok:
                    failures.append(f"{game_path_text}: {loader.message}")
                    remaining[game_path_text] = plugin_dir_text
                    continue
                cleaned += 1
            pruned += 1
            continue

        try:
            artifacts = _artifacts_present(plugin_dir)
        except TranslationError as exc:
            failures.append(f"{game_path_text}: {exc}")
            remaining[game_path_text] = plugin_dir_text
            continue
        entry_present, entry_error = _entry_presence_at(js_dir)
        if entry_error and not artifacts:
            failures.append(f"{game_path_text}: {entry_error}")
            remaining[game_path_text] = plugin_dir_text
            continue
        if not artifacts and not entry_present:
            pruned += 1
            continue

        if selected_layout is None:
            # Fall back to the stored plugin directory for file cleanup, but
            # still use its sibling plugins.js for the loader entry.
            fake_layout = GameLayout(
                game_path=game_path,
                engine="",
                js_dir=js_dir,
                data_dir=game_path / "data",
            )
            result = _remove_translation_at(fake_layout, update_index=False, force=True)
        else:
            result = _remove_translation_at(
                selected_layout, update_index=False, force=True
            )

        if result.ok:
            if not artifacts:
                pruned += 1
            else:
                cleaned += 1
        else:
            failures.append(f"{game_path_text}: {result.message}")
            remaining[game_path_text] = plugin_dir_text

    try:
        write_cleanup_index(remaining)
    except Exception as exc:
        failures.append(f"Could not save the translation cleanup index: {exc}")
        # Preserve the original index when possible; atomic writes leave the
        # old file intact on failure.
        return UninstallResult(
            ok=False,
            missing_index=False,
            pruned_count=pruned,
            failures=failures,
            cleaned_count=cleaned,
        )

    return UninstallResult(
        ok=not failures,
        missing_index=False,
        pruned_count=pruned,
        failures=failures,
        cleaned_count=cleaned,
    )


__all__ = [
    "PLUGIN_NAME",
    "PLUGIN_JS_NAME",
    "DICTIONARY_NAME",
    "CONFIG_NAME",
    "STATE_NAME",
    "DEFAULT_EXPORT_NAME",
    "RESERVED_EXPORT_NAMES",
    "APP_SUPPORT_ENV",
    "CLEANUP_INDEX_NAME",
    "PLUGINS_BACKUP_NAME",
    "DEFAULT_CONFIG",
    "PLUGIN_DESCRIPTION",
    "PLUGIN_JS_SOURCE",
    "GameLayout",
    "ScanResult",
    "TranslationData",
    "ValidationResult",
    "InstallResult",
    "ExportTargetInfo",
    "ExportResult",
    "CoverageRow",
    "CompilationResult",
    "LaunchResult",
    "UninstallResult",
    "TranslationError",
    "app_support_dir",
    "get_app_support_dir",
    "cleanup_index_path",
    "detect_game_layout",
    "scan_game",
    "export_raw_text",
    "export_raw_text_result",
    "export_clean_text_result",
    "export_missing_text_result",
    "export_clean_missing_text_result",
    "metadata_export_keys",
    "export_metadata_result",
    "inspect_export_target",
    "create_export_backup",
    "coverage_report",
    "coverage_summary",
    "missing_text_keys",
    "clean_export_keys",
    "compile_translation",
    "load_translation",
    "validate_translation",
    "read_config",
    "write_config",
    "read_state",
    "read_translation_state",
    "get_status",
    "read_cleanup_index",
    "write_cleanup_index",
    "upsert_cleanup_index",
    "remove_cleanup_index_entry",
    "upsert_loader_entry",
    "remove_loader_entry",
    "install_translation",
    "set_enabled",
    "remove_translation",
    "cleanup_orphan_entry",
    "prepare_for_launch",
    "loader_entry_present",
    "clean_export_metadata",
    "clean_app_copy",
    "uninstall_all_translations",
]
