# THY behavior review diagnostics

These synthetic reproducers use `ScriptedProvider`; their correct-behavior assertions are intentionally RED for confirmed current defects. They do not call a real model or read or modify historical runs. `-p tests.conftest` loads the standard test setup for these files outside `tests/`.

Run the three behavior cases from `IDE/Thymira` in Bash with a fresh temporary base:

```bash
uvwin=$(cygpath -u 'C:\Users\carlo\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe')
repo=$(cygpath -w "$PWD")
basetemp=$(cygpath -w /tmp/thymira-thy-review-repros/basetemp-reproduction-copy)
PYTHONPATH="$repo" "$uvwin" run --no-sync pytest -p tests.conftest -c pyproject.toml -q diagnostics/2026-10-09-thy-review/test_behavior_reproductions.py --basetemp="$basetemp"
```

The two intent regressions assert `train_new` survives a dataset/complexity clarification and that the linked plan retains EXPERIMENT. The plan probe uses Core `default_thy_catalog()`. Current behavior instead produces `general` and a DATA-only plan. EDA/explanation controls pass. The graph regression uses `data_agent_catalog()`, registers only DATA's `glob`, `read_file`, `profile_dataset`, and `query_sql` tools, and requires a `COMPLETED` `profile_dataset` event. A graph artifact or a non-None `output.error` satisfies the closure check. Current behavior returns `error=None`, completes profiling, and produces no plot, so the assertion fails. The persisted three-case run reported `3 failed, 1 warning in 49.53s`; all three failures are the intended REDs. The warning is the existing pydantic-graph event-loop deprecation.

For the evaluation callback contract probe, the full temporary adapted test copy remains at `/tmp/thymira-thy-review-repros/tests/test_model_evaluation_continuation_contract.py`. This directory keeps only the exact callback patch. The adapter uses `reconstruct_value` and `ModelEvaluationValue`, with the behavioral assertions unchanged. Run the two parameter cases from `IDE/Thymira`:

```bash
uvwin=$(cygpath -u 'C:\Users\carlo\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe')
repo=$(cygpath -w "$PWD")
testcopy=$(cygpath -w /tmp/thymira-thy-review-repros/tests/test_model_evaluation_continuation_contract.py)
basetemp=$(cygpath -w /tmp/thymira-thy-review-repros/basetemp-model-eval-probe)
PYTHONPATH="$repo" "$uvwin" run --no-sync pytest -p tests.conftest -c pyproject.toml -q "$testcopy::test_prior_model_evaluation_survives_approval_and_reconstruction" --basetemp="$basetemp"
```

Both cases now reach `_assert_published_evaluation` and fail with `evaluation failed: evaluation metrics id is not a successful task-owned evaluate_model publication`; the stale callback `KeyError` is removed by the adapter. No product code was changed.
