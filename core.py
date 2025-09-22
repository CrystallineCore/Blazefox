import sys, hashlib, logging, shutil, sqlite3, threading, re, fnmatch, os, datetime, json, atexit, subprocess
from tkinter import *
from tkinter import messagebox
from typing import Optional

thread_lock = threading.Lock()

from pathlib import Path
from typing import List, Union

try:
    import blake3
except ImportError:
    blake3 = None
try:
    import xxhash
except ImportError:
    xxhash = None

class FylexConfig:
    MAX_RETRIES = 5
    ON_CONFLICT_MODES = ["larger", "smaller", "newer", "older", "rename", "skip", "prompt", "replace"]
    DEFAULT_CHUNK_SIZE = 16 * 1024 * 1024
    DEFAULT_HASH_ALGO = "xxhash"
    FYLEX_HOME = Path.home() / ".fylex"
    FYLEX_HOME.mkdir(parents=True, exist_ok=True)
    TABLE_NAME = "file_hashes"
    DB_FILE = "file_cache.db"
    DB_PATH = FYLEX_HOME / DB_FILE 
    DB_CONN = None
    DB_LOCK = threading.Lock()
    LOG_MODE = "jsonl"
    
class FylexState:
    def __init__(self):
        self.parameters = {}
        self.func_route = []
        self.process_json = {}
        self.current_process = 1000
        self.dupe_candidates = {}
        self.total_memory_operation = 0
        self.total_memory_operated = 0

state = FylexState()

# --- Process IDs ----
def progress():
    with thread_lock:
        return int(100*state.total_memory_operated/state.total_memory_operation) if state.total_memory_operation else 100

def json_writer():
    json_dir = Path("json")
    json_dir.mkdir(exist_ok=True)
    with thread_lock:
        file_path = json_dir / f"{state.current_process}.jsonl"
        if not file_path.exists():
            with open(file_path, "w", encoding="utf-8") as f:
                meta = {"parameters": {
                            k: (str(v) if isinstance(v, Path) else v)
                            for k, v in state.parameters.items()
                        }}
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")

        if state.current_process in state.process_json:
            entries = state.process_json[state.current_process]
            if entries:
                latest_entry = entries[-1]
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(latest_entry, ensure_ascii=False) + "\n")

    return 0

def finalize_json():
    with thread_lock:
        process_id = state.current_process
    jsonl_path = Path("json") / f"{process_id}.jsonl"
    json_path = Path("json") / f"{process_id}.json"

    if not jsonl_path.exists():
        raise FileNotFoundError(f"{jsonl_path} not found")

    with open(jsonl_path, "r", encoding="utf-8") as f:
        try:
            lines = [json.loads(line) for line in f]
        except json.JSONDecodeError as e:
            logging.error(f"[JSON] Corrupted jsonl file: {e}")
            return None

    parameters = lines[0]["parameters"]
    entries = lines[1:]

    data = {
        "parameters": parameters,
        "process_json": {
            str(process_id): entries
        }
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return json_path

# --- Raw copier ---
def get_optimal_buffer_size(src: Path | str) -> int:
    """
    Choose an optimal buffer size for copying a file based on its size.
    Falls back safely if file does not exist.
    """
    src = Path(src)

    try:
        if src.is_file():
            file_size = src.stat().st_size
        else:
            # Not a file → just use safe default
            return 8 * 1024 * 1024  # 8 MB
    except FileNotFoundError:
        # File missing (e.g., was moved/deleted) → safe default
        return 8 * 1024 * 1024
    except OSError as e:
        logging.warning(f"[BUFFER] Could not stat {src}: {e}, using default")
        return 8 * 1024 * 1024

    if file_size < 100 * 1024 * 1024:         # < 100 MB
        return 1 * 1024 * 1024                # 1 MB
    elif file_size < 10 * 1024 * 1024 * 1024: # < 10 GB
        return 8 * 1024 * 1024                # 8 MB
    else:
        return 32 * 1024 * 1024               # 32 MB


def has_cmd(cmd: str) -> bool:
    """Check if a system command exists in PATH."""
    return shutil.which(cmd) is not None

def copier(src: Path | str, dest: Path | str, buffer_size: int,
           algo: str, preserve_meta: bool, mode: str, dry_run: bool):
    src = Path(src)
    tmp_dir = Path(dest).parent / "fylex.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_dest = tmp_dir / Path(dest).name
    actual_dest = Path(dest)
    size = src.stat().st_size

    if not dry_run:
        with open(src, "rb") as fsrc, open(tmp_dest, "wb") as fdest:
            in_fd, out_fd = fsrc.fileno(), fdest.fileno()
            remaining = size
            offset = 0

            if hasattr(os, "copy_file_range"):
                try:
                    while remaining > 0:
                        to_copy = min(buffer_size, remaining)
                        try:
                            # Modern signature: (fd_in, fd_out, count)
                            n = os.copy_file_range(in_fd, out_fd, to_copy)
                        except TypeError:
                            # Older signature: (fd_in, fd_out, offset_in, offset_out, count)
                            n = os.copy_file_range(in_fd, out_fd, offset, None, to_copy)
                        if not n:
                            raise RuntimeError("copy_file_range() returned 0 before completion")
                        remaining -= n
                        offset += n
                except Exception as e:
                    logging.debug(f"[COPY] copy_file_range failed, falling back: {e}")
                    fsrc.seek(0)
                    fdest.seek(0)
                    shutil.copyfileobj(fsrc, fdest, length=buffer_size)

            elif hasattr(os, "sendfile"):
                while remaining > 0:
                    sent = os.sendfile(out_fd, in_fd, offset, remaining)
                    if sent == 0:
                        raise RuntimeError("sendfile() returned 0 before completion")
                    offset += sent
                    remaining -= sent
            else:
                shutil.copyfileobj(fsrc, fdest, length=buffer_size)

            fdest.flush()
            os.fsync(fdest.fileno())

        if preserve_meta:
            shutil.copystat(src, tmp_dest, follow_symlinks=False)
            if hasattr(os, "chown"):
                try:
                    st = os.stat(src, follow_symlinks=False)
                    os.chown(tmp_dest, st.st_uid, st.st_gid, follow_symlinks=False)
                except (PermissionError, AttributeError, NotImplementedError):
                    pass
            if has_cmd("getfattr") and has_cmd("setfattr"):
                try:
                    xattr_out = subprocess.run(
                        ["getfattr", "-d", "-m", "-", str(src)],
                        capture_output=True, text=True
                    )
                    if xattr_out.stdout:
                        for line in xattr_out.stdout.splitlines():
                            if "=" in line and not line.startswith("#"):
                                attr, value = line.split("=", 1)
                                subprocess.run(
                                    ["setfattr", "-n", attr, "-v", value, str(tmp_dest)],
                                    check=True
                                )
                except subprocess.CalledProcessError as e:
                    logging.warning(f"[XATTR] Could not preserve xattrs for {src}: {e}")
            else:
                logging.warning("Skipping xattr preservation (requires `attr` package).")

            # Preserve ACLs
            if has_cmd("getfacl") and has_cmd("setfacl"):
                try:
                    acl_out = subprocess.run(
                        ["getfacl", "-p", str(src)],
                        capture_output=True, text=True
                    )
                    if acl_out.stdout:
                        subprocess.run(
                            ["setfacl", "--set-file=-", str(tmp_dest)],
                            input=acl_out.stdout, text=True, check=True
                        )
                except subprocess.CalledProcessError as e:
                    logging.warning(f"[ACL] Could not preserve ACLs for {src}: {e}")
            else:
                logging.warning("Skipping ACL preservation (requires `acl` package).")

        safe_replace(tmp_dest, actual_dest)
    process_json_writer(src, actual_dest, "copy", algo, dry_run)

# -------- Updater --------
def func_route_updater(func_name: str):
    with thread_lock:
        if not state.func_route:
            # fresh log file only on first use
            with open('fylex.log', 'w') as f:
                pass
        # overwrite instead of append, so [0] is always the current func
        state.func_route = [func_name]
    return True

# -------- Logger Setup --------
class InfoToLogger:
    def __init__(self, verbose: bool):
        self.verbose = verbose
    def write(self, msg: str):
        msg = msg.strip()
        if not msg:
            return
        logging.info(msg)
    def flush(self): pass

def safe_logging(verbose=True):
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Add a FileHandler to write to fylex.log if not already present
    fpath_abs = os.path.abspath("fylex.log")
    has_fh = False
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == fpath_abs:
            has_fh = True
            break
    if not has_fh:
        fh = logging.FileHandler("fylex.log", mode="a", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        root.addHandler(fh)
    
def log_copier(func_name, summary):
    global state
    do_work = False
    with thread_lock:
        if state.func_route and state.func_route[0] == func_name:
            do_work = True
            process_id = state.current_process

    if not do_work:
        return

    try:
        if summary:
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                    handler.close()
                except Exception:
                    logging.exception("Failed to close handler while finalizing logs")
            try:
                logging.shutdown()
            except Exception:
                pass
            try:
                shutil.copy("fylex.log", summary)
            except Exception:
                logging.exception("Failed to copy fylex.log to summary")
        json_writer()
        finalize_json()
    finally:
        with thread_lock:
            state = FylexState()


# -------- Input Prompt --------
def ask_user(question: str) -> str:
    with thread_lock:
        root = Tk()
        root.withdraw()  # hide the main window
        result = messagebox.askyesno("Fylex", question)
        root.destroy()
        return "y" if result else "n"
# -------- Database management --------

def get_db_conn(db_file: Optional[str] = None):
    if db_file:
        FylexConfig.DB_PATH = db_file

    if FylexConfig.DB_CONN is not None:
        return FylexConfig.DB_CONN

    # Ensure directory exists
    db_dir = (Path(FylexConfig.DB_PATH).resolve()).parent
    if db_dir and not db_dir.exists():
        Path(db_dir).mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        FylexConfig.DB_PATH,
        timeout=30,
        isolation_level=None,
        check_same_thread=False
    )

    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {FylexConfig.TABLE_NAME} (
            abs_path TEXT NOT NULL,
            hash_algo TEXT NOT NULL,
            mtime REAL NOT NULL,
            file_hash TEXT NOT NULL,
            PRIMARY KEY (abs_path, hash_algo)
        )
    """)

    FylexConfig.DB_CONN = conn

    try:
        atexit.register(close_db)
    except Exception:
        pass

    return FylexConfig.DB_CONN


def close_db():
    if FylexConfig.DB_CONN is None:
        return
    try:
        FylexConfig.DB_CONN.execute("PRAGMA wal_checkpoint(FULL);")
    except Exception:
        pass
    try:
        FylexConfig.DB_CONN.close()
    except Exception:
        pass
    FylexConfig.DB_CONN = None

def hash_file(path: str | Path, algo: str = FylexConfig.DEFAULT_HASH_ALGO, chunk_size: int = FylexConfig.DEFAULT_CHUNK_SIZE) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a valid file")
    algo = algo.lower()
    if algo == "blake3":
        if blake3 is None:
            raise ImportError("blake3 library is not installed")
        hasher = blake3.blake3()
    elif algo == "xxhash":
        if xxhash is None:
            raise ImportError("xxhash library is not installed")
        hasher = xxhash.xxh64()
    elif algo == "md5":
        hasher = hashlib.md5()
    elif algo == "sha256":
        hasher = hashlib.sha256()
    elif algo == "sha512":
        hasher = hashlib.sha512()
    else:
        raise ValueError(f"Unsupported algorithm: {algo}")

    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    
    return hasher.hexdigest()

def get_or_update_file_hash(src: str | Path,
                            algo: str = FylexConfig.DEFAULT_HASH_ALGO,
                            chunk_size: int = FylexConfig.DEFAULT_CHUNK_SIZE,
                            db_file: Optional[str] = None) -> str:
    src = Path(src).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"{src} is not a valid file")

    conn = get_db_conn(db_file)
    abs_path_str = str(src)
    current_mtime = src.stat().st_mtime

    cursor = None
    try:
        with FylexConfig.DB_LOCK:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT mtime, file_hash FROM {FylexConfig.TABLE_NAME}
                WHERE abs_path=? AND hash_algo=?
            """, (abs_path_str, algo))
            row = cursor.fetchone()
            if row:
                cached_mtime, cached_hash = row
                if float(cached_mtime) == float(current_mtime):
                    return cached_hash

            new_hash = hash_file(src, algo, chunk_size)

            cursor.execute("BEGIN;")
            cursor.execute(f"""
                INSERT INTO {FylexConfig.TABLE_NAME} (abs_path, hash_algo, mtime, file_hash)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(abs_path, hash_algo) DO UPDATE SET
                    mtime=excluded.mtime,
                    file_hash=excluded.file_hash
            """, (abs_path_str, algo, current_mtime, new_hash))
            cursor.execute("COMMIT;")
            return new_hash
    finally:
        if cursor:
            cursor.close()


# -------- File filtering --------
def sanitize_glob_regex(glob_pattern: str) -> str:
    """
    Convert a glob to a regex that matches the *entire* filename.
    """
    glob_re = fnmatch.translate(glob_pattern)  # e.g. '(?s:.*\\.cp)\\Z'
    m = re.match(r'^\(\?s:(.*)\)\\Z$', glob_re)
    inner = m.group(1) if m else re.sub(r'\\Z$', '', glob_re)
    return rf'(?s:^(?:{inner}))\Z'


def extract_global_flags(regex: str) -> tuple[str, str]:
    match = re.match(r"^\(\?([aiLmsux]+)\)", regex)
    if match:
        return match.group(1), regex[match.end():]
    return "", regex

def compile_patterns(match: bool,
                     names: Union[List[str], str, None],
                     regex: Union[str, None] = None,
                     glob: Union[List[str], str, None] = None) -> Optional[re.Pattern]:
    patterns: List[str] = []
    if regex:
        patterns.append(regex)

    if names:
        if isinstance(names, str):
            names = [names]
        # Exact filename match semantics
        patterns.extend([rf'^(?:{re.escape(name)})\Z' for name in names])

    if glob:
        if isinstance(glob, str):
            glob = [glob]
        patterns.extend([sanitize_glob_regex(g) for g in glob])

    if not patterns:
        return re.compile(r".*") if match else None

    combined_regex = "|".join(patterns)
    flags_str, pattern_str = extract_global_flags(combined_regex)
    flags = 0
    if "i" in flags_str: flags |= re.IGNORECASE
    if "m" in flags_str: flags |= re.MULTILINE
    if "s" in flags_str: flags |= re.DOTALL
    if "x" in flags_str: flags |= re.VERBOSE

    return re.compile(pattern_str, flags)

def list_matching_files(src: Union[Path, str],
                        match_names: Union[List[str], str] = None,
                        match_regex: str = None,
                        match_glob: Union[List[str], str] = None,
                        exclude_names: Union[List[str], str] = None,
                        exclude_regex: str = None,
                        exclude_glob: Union[List[str], str] = None,
                        recurse: bool = False) -> tuple[list[Path], set[int], set[str]]:
    src = Path(src).resolve()
    if not src.is_dir():
        return [src], {src.stat().st_size}, {src.suffix.lower()}

    include_pattern = compile_patterns(True, match_names, match_regex, match_glob)
    exclude_pattern = compile_patterns(False, exclude_names, exclude_regex, exclude_glob)

    matched_files: list[Path] = []
    file_sizes, file_extns = set(), set()

    walker = (src.glob("*") if not recurse else src.rglob("*"))
    logging.debug(f"[DEBUG] include_pattern: {include_pattern.pattern}")
    logging.debug(f"[DEBUG] exclude_pattern: {exclude_pattern.pattern if exclude_pattern else None}")

    for path in walker:
        if path.is_file():
            filename = path.name
            logging.debug(f"[DEBUG] Checking file: {filename}")
            if exclude_pattern and exclude_pattern.search(filename):
                logging.debug(f"[DEBUG] {filename} -> excluded")
                continue
            if include_pattern and include_pattern.search(filename):
                logging.debug(f"[DEBUG] {filename} -> included")
                matched_files.append(path.resolve())
                st = path.stat()
                with thread_lock:
                    state.total_memory_operation += st.st_size
                file_sizes.add(st.st_size)
                file_extns.add(path.suffix.lower())

    return matched_files, file_sizes, file_extns


# -------- Conflict resolution --------
def resolve_conflict(src_file: Path, dest_file: Path, resolve: str) -> Union[bool, Path]:
    resolve = resolve.lower()
    if resolve not in FylexConfig.ON_CONFLICT_MODES:
        raise ValueError(f"Invalid resolve mode: {resolve}")

    if not dest_file.exists():
        return True

    if resolve == "skip":
        return False
    if resolve == "replace":
        return True
    if resolve in ("larger", "smaller"):
        return (src_file.stat().st_size > dest_file.stat().st_size) if resolve=="larger" else (src_file.stat().st_size < dest_file.stat().st_size)
    if resolve in ("newer", "older"):
        return (src_file.stat().st_mtime > dest_file.stat().st_mtime) if resolve=="newer" else (src_file.stat().st_mtime < dest_file.stat().st_mtime)
    if resolve == "prompt":
        choice = ask_user(f"File conflict: {dest_file} | Replace with {src_file}? (y/n): ")
        return choice == "y"
    if resolve == "rename":
        base, ext = dest_file.stem, dest_file.suffix
        parent = dest_file.parent
        counter = 1
        new_file = parent / f"{base} ({counter}){ext}"
        while new_file.exists():
            counter += 1
            new_file = parent / f"{base} ({counter}){ext}"
        return new_file
    return False


def fast_move(src: Path | str, dest: Path | str, algo: str, dry_run: bool) -> bool:
    if not dry_run:
        if dest:
            try:
                Path(src).replace(dest)
            except OSError:
                shutil.move(src, dest)
            process_json_writer(src, dest, "move", algo, dry_run)
        else:
            src.unlink()
            process_json_writer(src, None, "delete", None, dry_run)

    return True

def safe_replace(src_tmp: Path, dest: Path):
    try:
        Path(dest).unlink(missing_ok=True)
        Path(src_tmp).replace(dest)
    except Exception as e:
        logging.error(f"[CRITICAL] Failed to finalize copy {src_tmp} -> {dest}: {e}")
        raise RuntimeError(f"Failed safe replace: {e}")


def create_dirs(path: Path | str, e_ok: bool = True, dry_run: bool = False):
    try:
        if not Path(path).exists():
            Path(path).mkdir(parents=True, exist_ok=e_ok)
            process_json_writer(path, None, "create", None, dry_run)
    except Exception as e:
        logging.error(f"[ERROR] Could not create a fresh directory: {e}")

def try_remove(file_path: Path | str):
    try:
        file_path.unlink()
        logging.info(f"[REMOVE] File {file_path} removed successfully.")
    except FileNotFoundError:
        logging.error(f"[PHANTOM] File {file_path} does not exist.")
    except PermissionError:
        logging.error(f"[ERROR] Permission denied: cannot remove {file_path}.")
    except Exception as e:
        logging.critical(f"[CRITICAL] Error removing file {file_path}: {e}")

def process_json_writer(src: Path | str, dest: Path | str, ops: str, algo: str, dry_run: bool=False):
    try:
        src = Path(src)
        dest_path = Path(dest) if dest else None
        file_hash = None
        if dest_path and dest_path.is_file() and algo:
            file_hash = get_or_update_file_hash(dest_path, algo)
        with thread_lock:
            if state.current_process not in state.process_json:
                state.process_json[state.current_process] = []

            state.process_json[state.current_process].append({
                'src': str(src.resolve()),
                'dest': str(dest_path.resolve()) if dest_path else None,
                'operation': ops,
                'algo': algo,
                'hash': file_hash,
                'timestamp': datetime.datetime.now().isoformat(),
                'dry_run': bool(dry_run),
            })

        json_writer()
        return True
    except Exception:
        logging.exception("[process_json_writer] failed to write process entry")
        return False

def copy_with_conflict_resolution(mode: str, src: Path | str, dest: Path | str, backup: Path | str, dry_run: bool, algo: str, resolve: str, 
            verbose: bool, preserve_meta: bool, verify: bool, chunk_size: int, trials: int, recurse: bool):
    dest = Path(dest).resolve()
    create_dirs(backup, True, dry_run)
    if dest.is_dir():
        dest_file = dest / src.name
    else:
        dest_file = dest
    for existing in dest.parent.glob("*"):
        if existing.name.lower() == dest_file.name.lower():
            dest_file = existing
            break

    save_as = Path(dest) / src.name
    if dest_file.exists():
        save_as = Path(backup) / datetime.datetime.now().isoformat()

    action = resolve_conflict(src, dest_file, resolve)

    if isinstance(action, Path):
        save_as = action
        logging.info(f"[RENAME] Renaming copy target: {src} -> {action}")
        copier(src, action, get_optimal_buffer_size(src), algo, preserve_meta, mode, dry_run)
        if (verify):
            if (get_or_update_file_hash(src, algo) != get_or_update_file_hash(action, algo)):
                try_remove(action)
                logging.warning(f"[RETRY] Hash mismatch: Retrying- {trials}")
                if (trials < FylexConfig.MAX_RETRIES):
                    copy_with_conflict_resolution(mode, src, dest, backup, dry_run, algo, resolve, verbose, preserve_meta, verify, chunk_size, trials+1, recurse)
                else:
                    logging.error("[ERROR] File transaction failed: Maximum retries(5) exceeded.")
                    return False
            else:
                with thread_lock:
                    state.total_memory_operated += action.stat().st_size
                if mode == "move":
                    if src.exists() and src.is_file():
                        src.unlink()
                        logging.info(f"[PROGRESS: {progress()}%] File moved and hash verified using {algo} successfully.")
                    else:
                        logging.info(f"[PHANTOM] File vanished before completing the operation: {src}")
                else:
                    logging.info(f"[PROGRESS: {progress()}%] File copied and hash verified using {algo} successfully.")
                with thread_lock:
                    state.dupe_candidates.setdefault(action.stat().st_size, []).append(get_or_update_file_hash(action, algo, chunk_size))
        else:
            with thread_lock:
                state.total_memory_operated += action.stat().st_size
            if mode == "move":
                if src.exists() and src.is_file():
                    src.unlink()
                    logging.info(f"[PROGRESS: {progress()}%] File moved successfully.")
                else:
                    logging.info(f"[PHANTOM] File vanished before completing the operation: {src}")
            else:
                logging.info(f"[PROGRESS: {progress()}%] File copied successfully.")
        return True

    if action and isinstance(action, bool):
        logging.info(f"[COPY] Copying {src} -> {dest_file}")
        if (dest_file.is_file()):
            logging.info(f"[DEPRECATE] {dest_file} is deprecated. Transferring it to: {save_as}")
            fast_move(dest_file, save_as, algo, dry_run)
        copier(src, dest_file, get_optimal_buffer_size(src), algo, preserve_meta, mode, dry_run)
        if (verify):
            if (get_or_update_file_hash(src, algo) != get_or_update_file_hash(dest_file, algo)):
                try_remove(dest_file)
                logging.warning(f"[WARNING] Hash mismatch: Retrying- {trials}")
                if (trials < FylexConfig.MAX_RETRIES):
                    copy_with_conflict_resolution(mode, src, dest, backup, dry_run, algo, resolve, verbose, preserve_meta, verify, chunk_size, trials+1, recurse)
                else:
                    logging.error("[ERROR] File transaction failed: Maximum retries(5) exceeded.")
                    return False
            else:
                with thread_lock:
                    state.total_memory_operated += src.stat().st_size
                if mode == "move":
                    if src.exists() and src.is_file():
                        src.unlink()
                        logging.info(f"[PROGRESS: {progress()}%] File moved and hash verified using {algo} successfully.")
                    else:
                        logging.info(f"[PHANTOM] File vanished before completing the operation: {src}")
                else:
                    logging.info(f"[PROGRESS: {progress()}%] File copied and hash verified using {algo} successfully.")
                with thread_lock:
                    state.dupe_candidates.setdefault(save_as.stat().st_size, []).append(get_or_update_file_hash(save_as, algo, chunk_size))

        else:
            with thread_lock:
                state.total_memory_operated += src.stat().st_size
            if mode == "move":
                if src.exists() and src.is_file():
                    src.unlink()
                    logging.info(f"[PROGRESS: {progress()}%] File moved successfully.")
                else:
                    logging.info(f"[PHANTOM] File vanished before completing the operation: {src}")
            else:
                logging.info(f"[PROGRESS: {progress()}%] File copied successfully.")
        return True
    else:
        with thread_lock:
            state.total_memory_operation -= src.stat().st_size
        if mode == "move":
            # Move original file to backup
            create_dirs(src / "fylex.deprecated" / str(state.current_process), e_ok=True, dry_run=dry_run)
            target_path = src / "fylex.deprecated" / str(state.current_process) / src.name
            fast_move(src, target_path, algo, dry_run)
            logging.info(f"[SKIP] Deprecating {src}, conflict resolution = {resolve}")
        else:
            logging.info(f"[SKIP] Skipping {src}, conflict resolution = {resolve}")
        return False


def find_dupe_candidates(dest: Path | str, file_sizes: set[int], file_extns: set[str], recursive_check: bool,
        has_extension: bool, algo: str, chunk_size: int) -> dict[int, list[str]]:
    dest = Path(dest)
    dupe_dict: dict[int, list[str]] = {}
    file_extns = [ext.lower() for ext in file_extns]
    candidates = dest.rglob("*") if recursive_check else dest.iterdir()

    for path in candidates:
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size not in file_sizes:
            continue
        if has_extension:
            if path.suffix.lower() not in file_extns:
                continue
        file_hash = get_or_update_file_hash(path, algo, chunk_size)
        dupe_dict.setdefault(size, []).append(file_hash)

    return dupe_dict


# -------- File Processes --------
def fileops(func_name: str, src: Path | str, dest: Path | str, resolve: str = "rename", algo: str = FylexConfig.DEFAULT_HASH_ALGO, 
            chunk_size: int = FylexConfig.DEFAULT_CHUNK_SIZE, verbose: bool = True, dry_run: bool = False, summary: Path | str = None, 
            match_regex: str = None, match_names: str = None, match_glob: str = None, exclude_regex: str = None, exclude_names: str = None, 
            exclude_glob: str = None, recursive_check: bool = False, recurse: bool = False, verify: bool = False, has_extension: bool = False, 
            no_create: bool = False, preserve_meta: bool = True, backup: Path | str = "fylex.deprecated") -> bool:
    global state
    src = Path(src).resolve()
    dest = Path(dest).resolve()

    # Basic path checks
    if not dest.is_dir() and no_create:
        raise NotADirectoryError(f"The provided path '{dest}' is not a directory and no_create is enabled.")

    if src == dest:
        raise ValueError("Source and destination paths cannot be the same.")
    if dest.is_relative_to(src):
        raise ValueError("Destination cannot be a subdirectory of source.")

    # Ensure destination directories exist
    create_dirs(dest, e_ok=True, dry_run=dry_run)
    create_dirs(dest / "fylex.tmp", e_ok=True, dry_run=dry_run)

    func_route_updater(func_name)

    with thread_lock:
        # Assign a new process ID
        while Path(f"json/{state.current_process}.json").is_file() or Path(f"json/{state.current_process}.jsonl").is_file():
            state.current_process += 1

        # Assign backup path after process ID is finalized
        if backup == "fylex.deprecated":
            backup = dest / f"fylex.deprecated/{state.current_process}"
        backup = Path(backup).resolve()

        if backup == dest:
            raise ValueError("`dest` and `backup` cannot be the same directories.")
        if backup in dest.parents:
            raise ValueError("Backup directory cannot be nested inside destination.")

        # Initialize state for this process
        state.process_json[state.current_process] = []
        state.parameters = {
            "func_name": func_name,
            "src": str(src),
            "dest": str(dest),
            "resolve": resolve,
            "algo": algo,
            "chunk_size": chunk_size,
            "verbose": verbose,
            "dry_run": dry_run,
            "summary": summary,
            "match_regex": match_regex,
            "match_names": match_names,
            "match_glob": match_glob,
            "exclude_regex": exclude_regex,
            "exclude_names": exclude_names,
            "exclude_glob": exclude_glob,
            "recursive_check": recursive_check,
            "verify": verify,
            "has_extension": has_extension,
            "no_create": no_create,
            "preserve_meta": preserve_meta,
            "backup": str(backup)
        }

    safe_logging(verbose)

    # List and filter files
    file_list, file_sizes, file_extns = list_matching_files(
        src, match_names, match_regex, match_glob,
        exclude_names, exclude_regex, exclude_glob,
        recurse
    )

    # Find duplicates in destination
    with thread_lock:
        state.dupe_candidates = find_dupe_candidates(dest, file_sizes, file_extns, recursive_check, has_extension, algo, chunk_size)

    # Process each file
    for file in file_list:
        file_hash = get_or_update_file_hash(file, algo)
        with thread_lock:
            if file_hash in state.dupe_candidates.get(file.stat().st_size, []):
                logging.info(f"[DUPLICATE] Duplicate found for: {file}")
                continue

        # Copy or move file with conflict resolution
        copy_with_conflict_resolution(
            func_name[4:], file, dest, backup, dry_run, algo,
            resolve, verbose, preserve_meta, verify, chunk_size, trials=0, recurse=recurse
        )

    # Cleanup empty folders
    with thread_lock:
        logging.info(f"[NOTE] Note down the process ID for future reference: {state.current_process}")

    if backup.exists() and not any(backup.iterdir()):
        backup.rmdir()
    temp = dest / "fylex.tmp"
    if temp.exists() and not any(temp.iterdir()):
        temp.rmdir()

    log_copier(func_name, summary)
    return True


def filecopy(src: Path | str, dest: Path | str, resolve: str = "rename", algo: str = FylexConfig.DEFAULT_HASH_ALGO, chunk_size: int = FylexConfig.DEFAULT_CHUNK_SIZE, 
             verbose: bool = True, dry_run: bool = False, summary: Path | str = None, match_regex: str = None, match_names: str = None, match_glob: str = None,
             exclude_regex: str = None, exclude_names: str = None, exclude_glob: str = None, recursive_check: bool = False, verify: bool = False,
             has_extension: bool = False, no_create: bool = False, preserve_meta: bool = True, backup: Path | str = "fylex.deprecated", recurse: bool = False) -> bool:
    return fileops("filecopy", src, dest, resolve, algo, chunk_size,
                   verbose, dry_run, summary, match_regex, match_names, match_glob,
                   exclude_regex, exclude_names, exclude_glob, recursive_check, recurse,
                   verify, has_extension, no_create, preserve_meta, backup)


def filemove(src: Path | str, dest: Path | str, resolve: str = "rename", algo: str = FylexConfig.DEFAULT_HASH_ALGO, chunk_size: int = FylexConfig.DEFAULT_CHUNK_SIZE, 
             verbose: bool = True, dry_run: bool = False, summary: Path | str = None, match_regex: str = None, match_names: str = None, match_glob: str = None,
             exclude_regex: str = None, exclude_names: str = None, exclude_glob: str = None, recursive_check: bool = False, verify: bool = False,
             has_extension: bool = False, no_create: bool = False, preserve_meta: bool = True, backup: Path | str = "fylex.deprecated", recurse: bool = False) -> bool:
    return fileops("filemove", src, dest, resolve, algo, chunk_size,
                   verbose, dry_run, summary, match_regex, match_names, match_glob,
                   exclude_regex, exclude_names, exclude_glob, recursive_check, recurse,
                   verify, has_extension, no_create, preserve_meta, backup)

def undo(p_id: str, verbose: bool = True, force: bool = False, summary: Path | str = None) -> bool:
    global state
    safe_logging(verbose)
    func_name = "undo"
    func_route_updater(func_name)

    json_path = Path(f"json/{p_id}.json")
    if not json_path.exists():
        logging.error(f"[UNDO] No record found for process ID {p_id}")
        return False

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    params = data.get("parameters", {})
    algo = params.get("algo", FylexConfig.DEFAULT_HASH_ALGO)
    dry_run = params.get("dry_run", False)
    preserve_meta = params.get("preserve_meta", True)

    with thread_lock:
        if state.func_route[0] == func_name:
            while Path(f"json/{state.current_process}.json").is_file():
                state.current_process += 1
            state.process_json[state.current_process] = []
            state.parameters = {
                "func_name": func_name,
                "p_id": p_id,
                "algo": algo,
                "verbose": verbose,
                "force": force,
                "dry_run": dry_run,
                "preserve_meta": preserve_meta,
            }

    proc_map = data.get("process_json", {})
    try:
        integer_check = int(p_id)
        del integer_check
        entries = proc_map.get(str(p_id)) or []
    except ValueError:
        return ValueError("Expected integer for p_id, but received non-integral values")

    for entry in reversed(entries):
        if entry.get("dry_run", False):
            logging.warning("[UNDO] Skipping dry-run entry (cannot rollback a dry-run).")
            continue

        src = Path(entry['src'])
        dest = Path(entry['dest']) if entry['dest'] else None
        op = entry['operation']

        try:
            if op == "copy":
                if dest and dest.exists():
                    if not dry_run:
                        dest.unlink()
                    logging.info(f"[UNDO] Removed {dest}")
                else:
                    logging.warning(f"[UNDO] File at {dest} not found")
            elif op == "move":
                create_dirs(src.parent, True, False)
                if dest and dest.exists():
                    if not dry_run:
                        fast_move(dest, src, algo, False)
                    logging.info(f"[UNDO] File {dest} has been restored")
                else:
                    logging.warning(f"[UNDO] File at {dest} not found")
            elif op == "delete":
                if src.exists():
                    logging.warning(f"[UNDO] File {src} already exists, cannot restore deleted file")
                else:
                    logging.warning(f"[UNDO] Cannot restore deleted file {src} automatically")
            elif op == "create":
                if src.exists():
                    if not any(src.iterdir()):
                        if not dry_run:
                            src.rmdir()
                        logging.info(f"[UNDO] Removed created directory {src}")
                    else:
                        logging.warning(f"[UNDO] Cannot remove {src} as it has been modified or contains other files")
            else:
                logging.error(f"[UNDO] Unknown operation performed on file: {dest}")
        except Exception as e:
            logging.error(f"[UNDO] Failed to undo {op} for {src}: {e}")
            if not force:
                return False

    if not entries:
        logging.info(f"[UNDO] No entries to process in JSON for {p_id}.")
    else:
        logging.info(f"[UNDO] Process {p_id} undone successfully")

    log_copier(func_name, summary)
    return True


def redo(p_id: str, verbose: bool = True, force: bool = False, summary: Path | str = None) -> bool:
    global state
    safe_logging(verbose)
    func_name = "redo"
    func_route_updater(func_name)

    json_path = Path(f"json/{p_id}.json")
    if not json_path.exists():
        logging.error(f"[REDO] No record found for process ID {p_id}")
        return False

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    params = data.get("parameters", {})
    algo = params.get("algo", FylexConfig.DEFAULT_HASH_ALGO)
    dry_run = params.get("dry_run", False)
    preserve_meta = params.get("preserve_meta", True)

    with thread_lock:
        if state.func_route[0] == func_name:
            while Path(f"json/{state.current_process}.json").is_file():
                state.current_process += 1
            state.process_json[state.current_process] = []
            state.parameters = {
                "func_name": func_name,
                "p_id": p_id,
                "algo": algo,
                "verbose": verbose,
                "force": force,
                "dry_run": dry_run,
                "preserve_meta": preserve_meta,
            }

    proc_map = data.get("process_json", {})
    try:
        integer_check = int(p_id)
        del integer_check
        entries = proc_map.get(str(p_id)) or []
    except ValueError:
        return ValueError("Expected integer for p_id, but received non-integral values")

    for entry in entries:
        if entry.get("dry_run", False):
            logging.info("[REDO] Skipping dry-run entry")
            continue
        src = Path(entry['src'])
        dest = Path(entry['dest']) if entry['dest'] else None
        op = entry['operation']
        try:
            if op == "copy":
                if force or not (dest and dest.exists()):
                    copier(src, dest, get_optimal_buffer_size(src),
                           algo, preserve_meta, mode="copy", dry_run=dry_run)
                    logging.info(f"[REDO] Re-copied {src} -> {dest}")
            elif op == "move":
                if force or not (dest and dest.exists()):
                    fast_move(src, dest, algo, dry_run)
                    logging.info(f"[REDO] Re-moved {src} -> {dest}")
            elif op == "delete":
                if dest and dest.exists():
                    if not dry_run:
                        dest.unlink()
                    logging.info(f"[REDO] Deleted {dest}")
                else:
                    logging.warning(f"[REDO] File at {dest} not found")
            elif op == "create":
                if not dry_run:
                    create_dirs(src, True, dry_run)
                logging.info(f"[REDO] Re-created directory {src}")
            else:
                logging.error(f"[REDO] Unknown operation performed on file: {dest}")
        except Exception as e:
            logging.error(f"[REDO] Failed to redo {op} for {src}: {e}")
            if not force:
                return False

    if not entries:
        logging.info(f"[ERROR] Could not process JSON: {p_id} or it is empty.")
    else:
        logging.info(f"[REDO] Process {p_id} replayed successfully")

    log_copier(func_name, summary)
    return True
