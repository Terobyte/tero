"""Фаза 08-7: push_ok:false в ответе relay → строка в `tero status`."""

from types import SimpleNamespace


def test_status_push_broken(tmp_path, capsys):
    """relay.json с push_ok:false → status печатает «пуш-канал сломан — перепривяжи»."""
    from src.commands.runs import run_status
    from src.loop.state import RunState
    from src.relay.pusher import Pusher

    run = tmp_path / "run"
    run.mkdir()
    RunState(run / "state.json").update(status="running", phase="p", phase_index=0, round=1)

    class _Relay:
        push_ok = False

        def push_state(self, state):
            return True

    assert Pusher(run, _Relay()).send_once() is True
    code = run_status(SimpleNamespace(working_dir=str(tmp_path), run=str(run), json=False))
    assert code == 0
    assert "пуш-канал сломан — перепривяжи" in capsys.readouterr().out
