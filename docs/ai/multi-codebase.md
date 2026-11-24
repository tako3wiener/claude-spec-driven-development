# v5.5 複数コードベースを横断する変更

## 適用範囲
ツール本体とClaude Codeを**同一のWSLまたはDev Container内**に導入する。1変更IDにつき2〜10個の独立したGitリポジトリを選択する。1セッションが書き込むのは担当リポジトリのみ。複数ターミナルで同時に実行できる。他の対象リポジトリは参照用にClaudeへ追加し、別の担当セッションで変更する。OSレベルの読み取り専用マウントやサンドボックスを提供するものではない。Hookは補助ガード。コンテナ越しのファイル操作・別コンテナへのリモート実行はしない。

## 1. 登録

```bash
export SDD_HOME="$HOME/tools/sdd"
"$SDD_HOME/sdd" configure --profile api --codebase /workspaces/api --execution devcontainer
"$SDD_HOME/sdd" configure --profile web --codebase /workspaces/web --execution devcontainer
"$SDD_HOME/sdd" configure --profile batch --codebase /workspaces/batch --execution devcontainer
"$SDD_HOME/sdd" profiles
```

各パスは同じ実行環境から見えるGitルート。WSLのみの場合は `--execution local` とWSL上の実パスを使用。別のコンテナにしか存在しないコードベースは同じDev Containerへワークスペースをマウントするなどして事前にアクセス可能にする。ホスト側のパスへ自動変換しない。グループ内で実行環境区分を混在させない。同一ルートの別名・入れ子のルートを拒否する。プロファイルの登録数そのものは制限せず、1案件に最大10個を選択できる。

初回は各プロファイルで `sdd run --profile api` 等を起動し、`/project-setup` と `/tool-setup` を実施して終了する。Git/Java/Maven/Gradle/Playwrightを実行する環境は各セッションと同じ。実際のNET隔離は別途必要。

## 2. 共通の変更案件を作成

```bash
"$SDD_HOME/sdd" group create --change-id CHG-101 --profiles api web batch \
  --summary 'API項目追加。APIとWeb、夜間バッチの契約・改修順序・横断テストを確認する'
```

同じインストール先でグループの変更IDは一意。メンバーは作成後固定。対象を変更する場合は新しい変更IDを作る。`group list` で一覧確認。共通計画に対象・API/DB契約・改修順序・依存関係・横断テスト・リリース/ロールバック方針を記載する。詳細な計画をファイルにまとめ、次で更新できる。

```bash
"$SDD_HOME/sdd" group plan --change-id CHG-101 --file /path/to/cross-repo-plan.md
```

共通計画を更新したら全メンバーのG2再承認が必要。既に起動しているClaudeセッションは終了・再起動して最新の計画を読み込む。共有計画に認証情報等を含めない。

## 3. G1・G2・実装

各担当を別ターミナルで実行する（この例は3セッション）。

```bash
"$SDD_HOME/sdd" group run --change-id CHG-101 --profile api
"$SDD_HOME/sdd" group run --change-id CHG-101 --profile web
"$SDD_HOME/sdd" group run --change-id CHG-101 --profile batch
```

それぞれのClaude Code内で `/spec-change CHG-101` を実行し、担当コードベースと他メンバーとの契約を調査する。G1成果物は各Gitルートの `docs/changes/CHG-101/`。REQ/TC-IDはリポジトリ内のIDなので、横断計画では `api:REQ-001` のようにプロファイル名を付けて参照する。同一チェックアウトを2つのSDD Claudeセッションが同時に編集することはランチャーのファイルロックで拒否する（別の手動起動や他ツールを排他するものではない）。

全G1計画を人が確認したら、別の通常のターミナルで一度だけ次を実行する。

```bash
"$SDD_HOME/sdd" group approve-g2 --change-id CHG-101
```

共通計画、全メンバーの要件・期待結果・変更計画・設定を確認する。承認者名と確認文字列を入力すると各リポジトリのG2 JSONとグループの承認世代が記録される。個別 `record-g2` はグループ案件には使用しない。全メンバーのG2が有効になるまで実装・テストを開始できない。どれかのG1計画・設定・G2記録・共通計画が変わった場合、全メンバーのG2を再承認する。署名鍵は不要。既に起動中のG1セッションは承認確認後に続行できる。

## 4. テスト・受入

各リポジトリは既存のG3/G4/G5/G6を維持する。NET承認は接続先/環境ごとに人が個別実行する。最終E2Eは全メンバーの実装と契約が揃ってから実行する。グループ承認世代と全リポジトリのGitスナップショットをRUNの記録に含め、実行中/実行後にいずれかの対象が変わったら正式E2Eの再実行が必要。既存のG6は各リポジトリのRUNとスナップショットを確認する。共通計画で実際の結合・システム横断シナリオを定義して実行すること。個別のPASSやG6だけでシステム全体のPASSとはしない。レビュー資料等のGit追跡対象が変わった場合もスナップショットが変わるため、最終RUNの再実行が必要になることがある。テスト済み組合せ以外をマージ・デプロイしない。

```bash
"$SDD_HOME/sdd" exec --profile api approval_gate.py record-net --change-id CHG-101 --allowed-origin http://localhost:8080 --isolation-mode container-egress-policy
"$SDD_HOME/sdd" exec --profile api run_e2e.py --change-id CHG-101 --mode final
"$SDD_HOME/sdd" exec --profile api approval_gate.py record-g6 --change-id CHG-101 --run-dir reports/e2e/CHG-101/RUN-ID
"$SDD_HOME/sdd" group status --change-id CHG-101
```

NETのURL、隔離方式、RUN-IDは実値に変更。web/batchにもその担当環境・RUNで同様に実行する。`group status` は全メンバーの設定/G2/NET/G6と進捗申告をJSONで表示する。承認がない、期限切れ、変更された場合は完了扱いしない。`all_member_acceptances_verified` は個別受入の整合確認のみ。統合受入・リリース順序・マージ/デプロイの自動実行は行わない。

グループ管理情報 `.sdd-groups.json` はツールのインストール先にあり、再インストール/アンインストール時に保持する。承認・仕様・証跡は各Gitリポジトリ側。既存ダッシュボードは `--repo` に各リポジトリを指定して閲覧できる。複数全体の集約表示は現時点では `group status` を使用する。グループ管理情報はローカルの確認記録であり、承認者本人の認証や改竄耐性、分散トランザクションを保証しない。
