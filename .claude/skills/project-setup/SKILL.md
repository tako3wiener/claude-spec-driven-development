---
name: project-setup
description: 対象Gitコードベース、Javaモジュール、テスト環境、テスト結果/Playwright証跡の保存先と保持期間を人の確認後に保存する。
disable-model-invocation: true
---

# v5 初期環境設定 /project-setup

1. ユーザーへ現在のGitルート、Javaモジュール、環境（local/test/staging）、URL、単体・結合/E2Eコマンドを提示し、**対象の選択とテスト先の確認**を求める。
2. 単体・結合結果の集約先（既定 `reports/test-results`）、E2E PNG/Trace/JUnitの保存先（既定 `reports/e2e`）、保存期間（既定90日）を**必ず提示・確認**。保存先はリポジトリ内の安全な相対パスのみ。結合テストコマンドは任意で指定できる。
3. `project_setup.py init`（対話）または`init --non-interactive`を使い、人が回答した値を渡す。`--confirm-codebase --confirm-isolated`は会話上の確認に代わる技術的本人認証ではない。
4. `examples/playwright-java`はDEMO_ONLY。正式E2Eコマンドへ選んだ場合はG4 PASSにならない。業務アプリ固有のテストを指定する。
5. `.claude/local/project-env.json`を保存して`project_setup.py check`で検証。URLやコマンドを変えた場合G2は失効する。設定に秘密値を格納しない。
6. `toolchain.py doctor --phase setup`、`--phase dev`を実行。開発診断にPlaywright依存は不要。`--phase e2e`は別途確認し、必要なら`/tool-setup`。
7. 実際のE2E前には、別途NET署名（実ネットワーク隔離・人工データ・Trace保存許可）が必要。`project_setup`におけるURLの文字列検査は本番接続の遮断を保証しない。

詳細は`docs/ai/v5-operations.md`。

## 保存設定と保持期間の補足
- `unit_integration_results_dir`, `e2e_evidence_dir`, `artifact_retention_days` は `.claude/local/project-env.json` に保存。未記載の旧v5設定は既定値にフォールバック。
- G2承認後の保存先変更は、署名ハッシュが変わるため再承認が必要。
- `.gitignore`の専用ブロックに両保存先を追加する。Git管理済みファイルを隠さないこと。
- 削除は`prune_artifacts.py`の一覧確認の後、管理者が `--apply --confirm-delete` を指定したときだけ行う。90日経過時の自動削除ではない。
