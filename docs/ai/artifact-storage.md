# v5 保存先・証跡保存期間の設定

## 1. /project-setup で確認する項目

| 項目 | 設定キー | 初期値 |
|---|---|---|
| 単体・結合JUnit結果の集約先 | `unit_integration_results_dir` | `reports/test-results` |
| Playwright E2E（PNG/Trace/JUnit/manifest）の保存先 | `e2e_evidence_dir` | `reports/e2e` |
| 保存期間 | `artifact_retention_days` | `90` 日 |
| 結合テストコマンド（任意） | `integration_test_argv` | 未設定 |

対話的な `python3 scripts/project_setup.py init` は保存先と期間を提示し、明示確認を求める。Claude Codeの`/project-setup` Skillも同じ値をユーザーに提示・確認する。

非対話では、旧v5との互換性のため新しい値を省略すれば初期値が使われる。**初期値以外の保存先または保持期間へ変更する場合**は`--confirm-artifact-storage`を指定する。ただしこのフラグ自体は承認者の本人認証ではない。

```sh
python3 scripts/project_setup.py init --non-interactive \
  --codebase . --environment test --base-url https://qa.example.internal/ \
  --unit-command './mvnw test' \
  --integration-command './mvnw verify' \
  --e2e-command './mvnw -f e2e/pom.xml verify' --e2e-workdir . \
  --unit-results-dir reports/test-results \
  --e2e-evidence-dir reports/e2e \
  --retention-days 90 \
  --confirm-codebase --confirm-isolated --confirm-artifact-storage
```

（Maven/Gradleコマンドとディレクトリは実プロジェクトに合わせる。上記は例。）

## 2. 保存先の安全性

- リポジトリ内の2階層以上の相対パスのみ。絶対パス、`..`、Windowsドライブパス、隠しディレクトリ、ソース/設定などの保護対象、シンボリックリンクを拒否する。
- 2つの保存先が同じ、あるいは親子関係になる指定を拒否する。
- すでにGit管理されているファイルを含むディレクトリは保存先にできない。
- 初期設定時に `.gitignore` の **管理対象ブロックだけ** を更新する。既存のルールを勝手に削除せず、保存先変更前の除外指定も残す（過去のPNG/Traceを誤ってGit管理しないため）。Git除外はファイルのアクセス権を制御するものではない。
- ローカル設定は `.claude/local/project-env.json` に保存し、Git管理除外。G2承認後に保存先を変更した場合は署名が失効し、再承認が必要。
- 共有フォルダ/NASなどのリポジトリ外パスへの書き込みは非対応。別ストレージに移す場合、責任者が明示的に転送し、暗号化・閲覧権限・保存期限を設定する。

## 3. 実際に集約される場所

`CHG-001`の例：

```text
reports/
├── test-results/
│   └── CHG-001/
│       └── RUN-.../
│           ├── unit/
│           │   ├── run-context.json
│           │   ├── summary.json
│           │   └── junit/TEST-*.xml
│           └── integration/                 # コマンド設定時のみ
│               ├── run-context.json
│               ├── summary.json
│               └── junit/TEST-*.xml
└── e2e/
    └── CHG-001/
        └── RUN-.../
            ├── run-context.json
            ├── junit/TEST-*.xml
            ├── TC-001/
            │   ├── 01-initial.png
            │   ├── 02-result-verified.png
            │   ├── trace.zip
            │   └── manifest.json
            ├── verification.json
            ├── summary.json
            └── index.md
```

```sh
# G2承認とセットアップが済んだ後
python3 scripts/run_java_tests.py --phase unit --change-id CHG-001
python3 scripts/run_java_tests.py --phase integration --change-id CHG-001
# E2EはG2とNET承認も必要
python3 scripts/run_e2e.py --change-id CHG-001 --mode final
```

Java `EvidenceRecorder`が参照する `E2E_EVIDENCE_ROOT` は「**設定済みE2E保存先の絶対パス**」。Pythonランナーが注入する。Java側はその下に`<CHG>/<RUN>/<TC>`を作成する。

Maven/Gradleを**直接実行**したテストのレポートは通常の`target/surefire-reports`や`build/test-results`に残る。今回追加したランナー経由での実行のみ、実行ID付きの集約先に保存される。

## 4. 90日の保持と削除

保持期間は**削除対象になる基準日数**。90日を超えて完了済みのRUNだけが候補になる。作業途中や情報が不完全なRUNは削除しない。

```sh
python3 scripts/prune_artifacts.py                          # 候補を表示（非破壊）
python3 scripts/prune_artifacts.py --apply --confirm-delete # 確認後に削除
```

**自動削除スケジューラは同梱しない**。毎日等の自動削除が必要な場合は、人が管理するバッチ/cron/タスクスケジューラで上記コマンドを実行する。消去不可の監査ログ保全や法的保管要件がある場合は、それを優先する。

## 5. 旧v5からの移行

`project-env.json`に新キーが存在しない場合、既定値（`reports/test-results`、`reports/e2e`、90日）が自動適用される。旧設定をそのまま読み取れるため、移行のための上書きは必須ではない。

旧v5の`.gitignore`を引き継いだ場合は、`reports/test-results/`の除外ルールを追加するか、`/project-setup`を再実行して管理対象ブロックを作る。後者は設定ファイルを書き換えるため、既存のG2署名を失効させる点に注意。
