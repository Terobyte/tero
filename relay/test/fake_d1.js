// relay/test/fake_d1.js — тонкая обёртка над node:sqlite DatabaseSync(':memory:')
// с интерфейсом D1 (prepare().bind().run()/first()/all(), batch).
// Выполняет настоящий relay/schema.sql. Не разбор SQL регулярками.

import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';

const SCHEMA_URL = new URL('../schema.sql', import.meta.url);

class FakeStatement {
  constructor(db, sql) {
    this._db = db;
    this._sql = sql;
    this._params = [];
  }

  bind(...params) {
    const next = new FakeStatement(this._db, this._sql);
    next._params = params.length === 1 && Array.isArray(params[0])
      ? params[0].slice()
      : params.slice();
    return next;
  }

  run() {
    const info = this._db._raw.prepare(this._sql).run(...this._params);
    return { success: true, meta: { changes: info.changes, last_row_id: info.lastInsertRowid } };
  }

  first() {
    const row = this._db._raw.prepare(this._sql).get(...this._params);
    return row === undefined ? null : row;
  }

  all() {
    return { results: this._db._raw.prepare(this._sql).all(...this._params) };
  }

  get _isSelect() {
    // Читающий запрос — тот, у которого есть колонки результата (WITH … SELECT тоже).
    return this._db._raw.prepare(this._sql).columns().length > 0;
  }
}

export function openFakeDb() {
  const raw = new DatabaseSync(':memory:');
  raw.exec(readFileSync(SCHEMA_URL, 'utf8'));
  const db = {
    _raw: raw,
    prepare: (sql) => new FakeStatement(db, sql),
    batch: (statements) => {
      // Как в D1: весь batch — одна транзакция, сбой любого стейтмента
      // откатывает все предыдущие в этом же вызове.
      raw.exec('BEGIN');
      try {
        const results = statements.map((stmt) => (stmt._isSelect ? stmt.all() : stmt.run()));
        raw.exec('COMMIT');
        return results;
      } catch (err) {
        raw.exec('ROLLBACK');
        throw err;
      }
    },
  };
  return db;
}
