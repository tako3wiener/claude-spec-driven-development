# v5 Playwright for Java E2E エビデンス基準

**正式E2Eは `scripts/run_e2e.py` 経由のみ**。`examples/playwright-java`は**DEMO_ONLY**。セットアップしたJava Webアプリ固有の承認済みTCとJavaメソッドを対応づけること。

## 取得ルール
- 操作前に`01-initial.png`、主要な操作後に`NN-xxx.png`、**期待結果のアサーション成功後**に`NN-result-verified.png`を取得。
- 失敗時は可能であれば`99-failure.png`。各TCのPlaywright `trace.zip`を保存。
- 各TCの`manifest.json`は`schema_version=2`, `change_id`, `run_id`, `tc_id`, `result`, `snapshot_id`, `runner_token_digest`, `environment_origin`, `git_commit`, `trace_file`, `trace_sha256`, `screenshots[]`（ファイル名/step/SHA256）を必須とする。
- JUnit XMLは新規RUNの専用`junit/`へ出力。**テスト0件・skipped・JUnit結果不足は正式PASS不可**。
- `<project-env.jsonのe2e_evidence_dir>/<CHG>/<RUN>/`に`run-context.json`, `verification.json`, `summary.json`, `index.md`, `test_results.csv`を保存。実行ごとのRUN-IDは再利用禁止。

## 管理・証拠レベル
- `scripts/check_e2e_evidence.py`はPNGデータ・ハッシュ・Playwright traceイベント・JUnit/TC対応・RUN_ID・snapshot-IDを照合する。
- `summary.json`が`PASS`でも、独立CIの実行由来（R12は保留）や期待結果の業務的妥当性を証明できない。G5独立レビューとG6人間確認を要する。
- TraceはDOMやネットワーク・認証トークン等を含み得る。スクリーンショットのマスクがTraceを匿名化することはない。**人工データ・最低権限ユーザー・組織の保管権限/保持期間・NET署名**を前提とする。

## コマンド
```bash
python3 scripts/approval_gate.py verify-g2 --change-id CHG-001
python3 scripts/toolchain.py doctor --phase e2e
python3 scripts/run_e2e.py --change-id CHG-001 --mode final
# run-idを確認後に再検査（通常はrunnerが自動実施）:
python3 scripts/check_e2e_evidence.py docs/changes/CHG-001 \
  <設定したE2E保存先>/CHG-001/<RUN-ID> --require-pass
```

接続先が未確認、G2/NET署名が未検証、OS/コンテナ隔離が未構築の場合はE2Eの実行を**BLOCKED**とする。`docs/ai/v5-operations.md`参照。

## 保存先と保持期間
- `/project-setup`で指定した`e2e_evidence_dir`を Python runner と Java EvidenceRecorder が共通利用する。環境変数`E2E_EVIDENCE_ROOT`はそのディレクトリの絶対パス。
- デフォルトは`reports/e2e`、保持期間は90日。証跡を勝手に削除せず`python3 scripts/prune_artifacts.py`で削除候補を確認する。
- 旧v5の設定ファイルに保存先の項目がなくても、既存`reports/e2e`を参照する。
