"""Тень эскалации по ситуации (план 08-3): diagnose из jev-bench без изменения логики.

Только stdlib. simulate/CLI бенча сюда не переехали: действие по situation
вне этой фазы, вердикт никто не читает.
"""

from __future__ import annotations

from dataclasses import dataclass

TERMINAL = ("ship_with_debt", "opus_decides", "ask_owner")
OPUS_LIKE = ("opus_decides", "ask_owner")
LABEL_OF_ACTION = {"continue": "continue_muse"}
LEVELS = ("blocker", "major", "minor", "nit")


@dataclass
class Cfg:
    min_conf: float = 0.4
    fix_budget: int = 2
    sonnet_max: int = 2
    mole_rounds: int = 3
    polish_first: bool = False
    polish_min_round: int = 2


def _sources(rnd):
    jev = rnd.get("sev") or []
    judge = rnd.get("judge_level") or []
    for i in range(rnd["n_findings"]):
        yield (jev[i] if i < len(jev) else None), (judge[i] if i < len(judge) else None)


def _known_blocker(jev, judge, cfg):
    # блокер по чьей-то оценке: судья сказал blocker или неизвестный уровень; jev blocker или не уверен
    if judge is not None and (judge == "blocker" or judge not in LEVELS):
        return True
    return bool(jev) and (jev[0] == "blocker" or jev[1] < cfg.min_conf)


def _blockers(rnd, cfg):
    """Для шипа: находка без тяжести ни у кого тоже блокер."""
    return sum(1 for jev, judge in _sources(rnd)
               if _known_blocker(jev, judge, cfg) or (jev is None and judge is None))


def _rated_blockers(rnd, cfg):
    """Для крота: только блокеры, которые кто-то так оценил, — не жечь Opus вслепую."""
    return sum(1 for jev, judge in _sources(rnd) if _known_blocker(jev, judge, cfg))


def _res(situation, action, reason):
    return {"situation": situation, "action": action, "reason": reason}


def diagnose(rounds, cfg, opus_round=None, owner_round=None):
    """opus_round / owner_round — раунд, на котором Opus или хозяин сказал «ещё раунд».
    Окна stuck и mole считаем только после последнего такого раунда; лимит Sonnet — по всей фазе."""
    sonnet_n = sum(1 for r in rounds if r.get("player") == "sonnet")
    reset = max((x for x in (opus_round, owner_round) if x is not None), default=None)
    if reset is not None:
        rounds = [r for r in rounds if r["round"] > reset] or rounds[-1:]
    d = _diagnose(rounds, cfg, sonnet_n)
    if d["action"] == "opus_decides" and opus_round is not None:
        # Opus в фазе — один вызов; второй раз решает хозяин
        return _res(d["situation"], "ask_owner", d["reason"] + "; Opus уже решал в этой фазе")
    return d


def _diagnose(rounds, cfg, sonnet_n):
    cur = rounds[-1]

    def polish():
        if cur["round"] < cfg.polish_min_round:
            return None
        if (_blockers(cur, cfg) == 0 and cur["n_findings"] >= 1 and (cur["still_open"] == 0 or cfg.polish_first)
                and sum(r["n_findings"] for r in rounds) > cfg.fix_budget):
            return _res("polish", "ship_with_debt",
                        "блокеров нет, остались мелочи, бюджет правок исчерпан")
        return None

    def stuck():
        # 2 из 3 последних: ловит и чередование 1,0,1
        recent = rounds[-3:-1]
        return ((cur["still_open"] >= 1 and any(r["still_open"] >= 1 for r in recent))
                or (cur["n_findings"] == 0 and cur["still_open"] >= 1))

    def mole():
        k = cfg.mole_rounds
        if len(rounds) <= k:  # первый раунд фазы закрывать нечего, в окно не берём
            return None
        last = rounds[-k:]
        # в окне блокер в каждом раунде (с известной тяжестью); после Sonnet один раунд
        # с хвостом still_open терпим — иначе 0,1,0 рвёт окно, а stuck не срабатывает
        # после Sonnet терпим один хвост still_open, но только в раундах, сыгранных Sonnet или после него:
        # раунд застоя до Sonnet в окно-допуск не берём, иначе unstuck недостижим
        first_s = next((r["round"] for r in rounds if r.get("player") == "sonnet"), None)
        tails = [r for r in last if r["still_open"] >= 1]
        tolerated = first_s is not None and len(tails) <= 1 and all(r["round"] >= first_s for r in tails)
        if ((not tails or tolerated)
                and all(_rated_blockers(r, cfg) for r in last)):
            return _res("mole", "opus_decides",
                        f"{k} раунда подряд новые находки с блокером, старые закрываются")
        return None

    if cfg.polish_first:
        r = polish()
        if r:
            return r
    if stuck():
        if sonnet_n >= cfg.sonnet_max:
            return _res("stuck_strong", "opus_decides",
                        "застряли, а sonnet уже играл и не помог")
        return _res("stuck", "sonnet_player", "одна и та же находка не закрывается")
    r = mole()
    if r:
        return r
    if not cfg.polish_first:
        r = polish()
        if r:
            return r
    if cur.get("player") == "sonnet" and sonnet_n >= cfg.sonnet_max:
        return _res("unstuck", "muse_player", "sonnet отыграл лимит и расклинил, назад на muse")
    return _res("converging", "continue", "находки закрываются, идём дальше")
