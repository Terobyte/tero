# Архив сданных планов v5

Планы, все фазы которых сданы tero. Здесь только для истории и для поиска «почему так сделано»:
tero их больше не запускает.

| План | Что сделано | Фаз |
|---|---|---|
| `01-state-stream-hud.md` | `state.json` и статус, лок прогона, семьи моделей, порядок старта, `stream.jsonl`, `Tail`, правило запуска HUD, реестр, `tero go` запускает HUD | 11/11 |
| `02-snapshots-judge-pending.md` | снимки `refs/tero/...`, откат только со снимком, судья по снимку, `judge_pending`, `tero review`, `tero resume` с судьи, исполнитель со снимка, K_max | 10/10 |
| `03-resilience-agy.md` | виды ошибок провайдера, убийство по тишине, backoff, `NEEDS_USER` без эскалации, ключи `config.yaml`, провайдер `agy` и его pre-flight | 7/7 |
| `04-compact-pult.md` | счётчик контекста и компакт, `tero compact`, экзамен судей (слой 1), пульс, `tero wait`/`status`, `go --detach`, inbox и `tero answer` | 8/15, фазы 9–15 → `v5/plans/06-pult-decisions.md` |
| `05-refactor.md` | рефакторинг без новой функциональности: план правит только tero, страж размеров, мёртвый код, `src/fsio.py`, `suite_guard.py`, шаги фазы в модулях, общий цикл `run`/`resume`, команды CLI в `src/commands/`; карта — `v5/code-map.md` | 8/8 |

Что дальше: `v5/plans/06-pult-decisions.md` (0/8). Где что лежит в коде — `v5/code-map.md`.
