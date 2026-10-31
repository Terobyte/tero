"""`tero resume --answer-from-inbox`: the signed phone answer for a stopped run."""

from __future__ import annotations

from pathlib import Path

from src.fsio import append_text, read_json_dict
from src.loop.human_key import who_of
from src.loop.state import RunState

ANSWER_FILE = "resume-answer.json"


def _log_bad_signature(run_dir: Path) -> None:
    try:
        append_text(run_dir / "hud.log", "resume-answer.json без верной подписи — игнор\n")
    except Exception:
        pass


def peek_inbox_answer(run_dir: str | Path) -> str:
    """Verified inbox text without touching anything (safe before the lock).

    No delete, no state.json write: a contender refused with code 9 leaves
    no trace, the file stays for the real owner. A bad signature is ignored
    with a line in `hud.log`.
    """
    from src.loop.resume.step import clean_answer

    run = Path(run_dir)
    data = read_json_dict(run / "inbox" / ANSWER_FILE)
    if data is None:
        return ""
    if who_of(run.name, data) != "human":
        _log_bad_signature(run)
        return ""
    return clean_answer(str(data.get("text") or "")).strip()


def saved_phone_answer(run_dir: str | Path) -> str:
    """Phone answer the owner already persisted (crash between save and resume)."""
    saved = RunState.read(Path(run_dir) / "state.json") or {}
    if saved.get("answer_from_phone") and saved.get("answer"):
        return str(saved["answer"])
    return ""


def peek_or_saved_answer(run_dir: str | Path) -> str:
    """Pre-lock answer: verified inbox text, else the persisted phone answer.

    Reads only — no delete, no state.json write, so a contender refused with
    code 9 changes nothing.
    """
    return peek_inbox_answer(run_dir) or saved_phone_answer(run_dir)


def consume_inbox_answer(run_dir: str | Path) -> str:
    """Verified inbox text, file deleted. No state.json write.

    The owner calls this after acquiring the lock and persists the answer
    with a partial `RunState.update`, so parallel fields survive.
    A bad signature is ignored with a line in `hud.log` (file kept).
    """
    from src.loop.resume.step import clean_answer

    run = Path(run_dir)
    path = run / "inbox" / ANSWER_FILE
    data = read_json_dict(path)
    if data is None:
        return ""
    if who_of(run.name, data) != "human":
        try:
            append_text(run / "hud.log", "resume-answer.json без верной подписи — игнор\n")
        except Exception:
            pass
        return ""
    text = clean_answer(str(data.get("text") or "")).strip()
    try:
        path.unlink()
    except OSError:
        pass
    return text


def persist_inbox_answer(run_dir: str | Path, done_io: RunState) -> str:
    """Owner-side inbox answer: verify, persist under the held lock, then delete.

    The file is deleted only after `answer` + `answer_from_phone` are written
    together: a failed write keeps the file for a retry and returns "", so
    resume never continues with an unsaved phone text (it would look local to
    ResumeStep and could leak into the judge prompt). Never raises: a code 9
    contender never reaches here, the file stays for the real owner.
    """
    from src.loop.resume.step import clean_answer

    run = Path(run_dir)
    path = run / "inbox" / ANSWER_FILE
    try:
        data = read_json_dict(path)
    except Exception:
        return ""
    if data is None:
        return ""
    if who_of(run.name, data) != "human":
        _log_bad_signature(run)
        return ""
    text = clean_answer(str(data.get("text") or "")).strip()
    if not text:
        return ""
    try:
        done_io.update(answer=text, answer_from_phone=True)
    except Exception:
        return ""
    try:
        path.unlink()
    except OSError:
        pass
    return text


def pre_lock_answer(run_dir: str | Path) -> str:
    """Pre-lock resolve for `resume`: never raises, reads only (code 9 safe)."""
    try:
        return peek_or_saved_answer(run_dir)
    except Exception:
        return ""


def owner_answer(run_dir: str | Path, done_io: RunState, answer: str, had_answer: bool) -> str:
    """Owner path (lock held): persist the inbox answer, return the text to use.

    `persist_inbox_answer` returns "" when the write failed (file kept): a
    pre-lock peek text that was never persisted must not continue —
    ResumeStep would mark it local and leak it to the judge prompt. An
    explicit `--answer` or an already-persisted phone answer survives.
    """
    persisted = persist_inbox_answer(run_dir, done_io)
    if persisted:
        return persisted
    if not had_answer and answer != saved_phone_answer(run_dir):
        return ""
    return answer


def take_resume_answer(run_dir: str | Path) -> str:
    """Text of `<run>/inbox/resume-answer.json`, or "" (missing / bad signature).

    Never writes state.json: persisting the answer is the owner's job after
    acquiring the lock (`consume_inbox_answer` + a partial `RunState.update`
    in `resume`), otherwise a contender without the lock clobbers parallel
    fields with a stale read-modify-write, and a code 9 contender still
    mutates state. A valid file is deleted on read; without the file (a retry
    after code 9) the persisted phone answer is reused. A bad signature is
    ignored with a line in `hud.log`.
    """
    from src.loop.resume.step import clean_answer

    run = Path(run_dir)
    path = run / "inbox" / ANSWER_FILE
    data = read_json_dict(path)
    if data is not None:
        if who_of(run.name, data) != "human":
            try:
                append_text(run / "hud.log", "resume-answer.json без верной подписи — игнор\n")
            except Exception:
                pass
            return ""
        text = clean_answer(str(data.get("text") or "")).strip()
        try:
            path.unlink()
        except OSError:
            pass
        return text
    saved = RunState.read(run / "state.json") or {}
    if saved.get("answer_from_phone") and saved.get("answer"):
        return str(saved["answer"])
    return ""
