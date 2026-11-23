# 進捗記録（ダッシュボードに依存しない）

`/spec-change` は各工程境界で `scripts/progress.py` を実行する。書き込み先は Git 除外された `.claude/local/progress/CHG-001.json`（最新状態）と `.jsonl`（履歴）。

```bash
python3 scripts/progress.py --change-id CHG-001 --gate G1 --state running --next-action "仕様と既存コードの影響を調査する"
python3 scripts/progress.py --change-id CHG-001 --gate G2 --state waiting --next-action "変更計画・TCを確認し外部でG2承認する"
python3 scripts/progress.py --change-id CHG-001 --gate G5 --state running --next-action "独立レビューを実施する"
```

- Gate: G0 初期診断、G1 仕様・影響調査、G2 設計承認、G3 実装とJUnit、G4 Playwright E2E、G5 独立レビュー、G6 最終承認。
- State: `running`（作業中）、`waiting`（承認待ちなど）、`blocked`（問題により停止）、`done`（担当者がその工程の作業終了を報告）。
- **重要**：進捗データは検証済み承認や合格証明ではない。G2/G6/NET の正式判定には従来の `approval_gate.py` による人間承認記録と対象照合が必要。
- 外部ダッシュボードが存在しなくても通常処理は動作する。ダッシュボードを停止/削除しても依存関係はない。
- `run_java_tests.py` / `run_e2e.py` は実行開始・完了/失敗を補助的に記録する。失敗したログ書き込みでテスト本体の結果を変更しない。
- 複数 Claude セッションが同一 CHG を同時操作すると最新イベントが上書きされるため、1変更IDにつき1つの作業セッションを推奨する。
- `.claude/local/progress/` は Git 管理しない。パスワードや認証情報を `--note` / `--next-action` に入れない。
