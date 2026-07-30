# Bug Report — Active

Покрытие открытых пунктов тестами (включая RED до фикса в коде):

```
python3 -m pytest tests/test_audit_bugs_critical.py tests/test_audit_bugs_serious.py tests/test_audit_bugs_medium.py tests/test_bugs_md_negative_registry.py tests/test_bugs_md_sw_negative.py tests/test_bugs_md_new_findings.py -v
```

Отдельно только аудиты из этого файла + SW + new findings:

```
python3 -m pytest tests/test_audit_bugs_critical.py tests/test_audit_bugs_serious.py tests/test_audit_bugs_medium.py tests/test_bugs_md_negative_registry.py tests/test_bugs_md_sw_negative.py tests/test_bugs_md_new_findings.py --tb=short
```

---

## Closed (~101 fixed)

BUG-01, BUG-02, BUG-03, BUG-04, BUG-05, BUG-06, BUG-07, BUG-14, BUG-15, BUG-16, BUG-17, BUG-18, BUG-20, BUG-21, BUG-22, BUG-25, PLAN-B1..PLAN-B7, GEN-B8..GEN-B17, SW-01..SW-61, NEW-01..NEW-07
