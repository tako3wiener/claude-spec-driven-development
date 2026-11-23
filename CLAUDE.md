# Claude Code向け必須ルール（Java既存Webアプリの仕様駆動改修）

## 優先規則
1. ユーザーが承認した変更スコープ/仕様/期待結果を正とする。原本 `docs/spec/` は**変更禁止**。
2. 既存動作を保持する。変更対象外の公開API・DBスキーマ・認証/認可・例外契約・性能を勝手に変えない。
3. 仕様の推測を確定事項として扱わない。仕様出典 `文書名:章/節/ページ` または `既存コード/テスト:パス:行` を必ず記録。
4. すべての要求を `REQ-###`、検証を `TC-###` で管理する。仕様→変更コード→テスト→実行証跡を追跡する。
5. テストの期待結果を実装に合わせて変更しない。期待結果の変更は理由・差分・人の再承認が必要。
6. 環境値/資格情報を読んだり出力したりしない。本番アクセス、データ削除、デプロイ、push/mergeはしない。
7. 実行していないテストはPASSと報告しない。失敗/未実行/BLOCKEDを区別する。

## 起動時のコードベース・環境セットアップ（G-SETUP）
- **初回・設定無効時**はコード解析・編集・テストより先に `/project-setup` を実行し、コードベース（GitルートとJavaモジュール）・専用テスト環境名/URL・実行コマンドを人に確認する。
- 毎回のClaude Code起動時に `SessionStart` hookが `.claude/local/project-env.json` を検査する。保存済みでも**今回の対象コードベース・テスト環境を人に提示し、変更がないことを確認する**。確認まで改修・テストは行わない。
- `python3 scripts/project_setup.py check` の成功をG0の必須条件とし、Gitルート・対象パス・環境URLの不一致は `BLOCKED`。環境を推測して自動切替しない。
- テストは承認された環境に限定し、UI E2Eは `python3 scripts/run_e2e.py --change-id CHG-001` **経由**で実行。URLを直接上書きして実行しない。
- ローカル設定 `.claude/local/` をGitへコミットしない。資格情報は記録・表示しない。参照: `docs/ai/startup-setup.md`。

## 必須ツール診断と取得（G-TOOLS）
- プロジェクト設定後 `/tool-setup` を実行し、`python3 scripts/toolchain.py doctor --phase dev` / `--phase e2e` でGit/Python/JDK/Claude CLI/MavenまたはGradle/Playwright Java依存/ブラウザを診断する。
- 依存未導入時はインストール計画 `python3 scripts/toolchain.py plan` を提示する。**人の明示承認前にネットワーク取得・パッケージ追加・バージョン変更をしない。**
- `python3 scripts/toolchain.py install --apply --approve-downloads` は人が具体的な実行計画を承認した場合のみ。取得元は既存ビルド設定のリポジトリおよびPlaywright公式配布先を使う。
- JDK/Claude Code/Git/Python/Maven/Gradleが欠落する場合、OSの環境変更や特権操作を独断で行わず公式導入先を案内し停止する。`docs/ai/tool-installation.md` を参照。
- E2E実行前にツール診断をPASSさせる。`BOOTSTRAPPED`はインストール記録であって実際のブラウザ起動成功を意味しない。E2Eで実行確認する。

## 最初に確認するもの
- `docs/ai/startup-setup.md`、`docs/ai/project-profile.md`、`docs/ai/quality-gates.md`、`docs/ai/test-strategy.md`
- `git status`, ビルドツール、Javaバージョン、既存テスト・CI、DB・画面/APIの層構成
- `docs/spec/`、既存コード、類似機能、障害履歴（存在する場合）
- 変更前のビルド/既存テスト実行結果。失敗している場合はベースライン不具合として分離。

## 作業単位
- 1つの変更ID (例 `CHG-001`) ごとに `docs/changes/<ID>/` を作る。
- 変更は小さく区切り、スコープ外の整理/リファクタリングを混ぜない。
- 要件解析 → 影響調査 → 実装前テスト設計 → **G2人間承認** → 実装 → 自動テスト → 独立レビュー → **G6人間承認**。
- G2承認前は実装コードを編集しない。承認が確認できないなら止める。
- D分類の業務上重大な不明点、破壊的DB変更、本番/外部システム操作に遭遇したら該当作業を停止し確認する。

## Java/Web規約
- 既存のMaven/Gradleラッパー、JDK、フレームワーク、パッケージ構成、命名、例外処理、ログ、DI、テストスタックを優先する。
- 新規ライブラリ追加は、既存の代替手段を調べて必要性と影響を説明し、承認対象にする。
- DBアクセスは専用テストDBのみ。実環境データの複製は匿名化・認可確認なしに行わない。
- 入力検証、権限、CSRF/XSS/SQL Injection、ログ秘匿、時刻/タイムゾーン、並行実行、ロールバックを対象に応じて検証する。

## 失敗の扱い
- 失敗: 原因を再現 → ログと対象REQ/TCを記録 → 最小修正 → 同一テストと関連回帰を再実行。
- 同一原因で3回連続失敗・不明点で期待結果が決まらない・環境が利用できない場合は `BLOCKED` とし理由を報告。
- テストのスキップ、閾値の引下げ、検証コード削除、ログの捏造で「成功」にしない。

## エージェント利用
- `spec-analyst`: 仕様と既存動作の分析（読み取り専用）
- `test-designer`: 実装に先行する期待結果付きテスト設計
- `java-developer`: 承認済み計画に従う実装と単体/結合テスト
- `e2e-runner`: 専用環境でのE2Eと証跡収集
- `independent-reviewer`: 仕様・コード・テスト結果を読み取り専用で第三者検証

## 出力形式
各工程終了時は `完了/未完了/BLOCKED`, 根拠資料, 変更ファイル, 実行したコマンド, テスト結果, 残リスク, 次のゲートを報告する。

## Playwright E2E・エビデンス必須規約
- UI E2EはPlaywright **for Java** + プロジェクトのJUnitで実行する。Playwright MCPによる操作は正式E2E結果の代わりとしない。
- `docs/ai/e2e-evidence.md` を読み、G2でE2E対象TC、画面キャプチャ箇所、固定環境、マスク箇所を確定する。
- すべてのE2E TCで操作前/主要ステップ・検証成功後のPNGスクリーンショットを取得し、PASSには`*-result-verified.png`を要求する。失敗時は可能な限り`99-failure.png`を残す。
- `<project-env.jsonで設定したE2E保存先>/<CHG-ID>/<RUN-ID>/<TC-ID>/` にPNG、Playwright `trace.zip`、`manifest.json`を出力。JUnit XMLをCI artifactへ保管し、`index.md`を生成する。
- 証跡がない、画像のハッシュ不一致、Traceがない、JUnitで未実行の場合はG4を通過しない。Gitに証跡・秘密情報をコミットしない。
- Traceはネットワーク/DOM内の秘密情報も含み得るため、低権限の隔離環境・架空データのみで実行する。スクリーンショットのマスクだけではTrace内の情報を保護できない。

## v5 追加強制ルール（最新版。v4の記述と矛盾する場合はこちらを優先）
- `docs/ai/v5-operations.md`が手順の正本。G2/G6/NETは`approvals.md`の記載ではなく**人が記録した承認JSONと対象ハッシュ**を検証する。電子署名不要。Claudeは `record-g2/net/g6` を実行せず、承認ファイルを作成・変更しない。
- 作業開始時に`SDD_CHANGE_ID`を確認。未承認G2でJavaコード改変・テスト実行は不可。`.claude/settings.json`のPreToolUseは補助ガード、実アクセス制限は外部管理。
- NET承認で確認された**外部の隔離構成**がなければ正式E2Eを実行しない。承認記録は実際のファイアウォール/テストDBを生成しない。
- 開発中の部分テスト(dev)と正式ゲート(final)を区別。finalでskip、0件、DEMO_ONLY、旧JUnit流用、証拠不一致を許可しない。
- E2Eは`python3 scripts/run_e2e.py --change-id CHG-001 --mode final`だけで実行。PNG/Trace/JUnitとRUN_ID/snapshot-IDが合致した結果のみローカルPASSとする。
- `toolchain.py resolve --approve-resolution`はMaven/Gradleプラグイン実行/ネットワークアクセスを伴うため人の個別承認が必要。doctor/planは診断のみ。
- v5ではR12 CI品質ゲート、R15複数Javaモジュール自動対応、R18高度網羅率管理は**保留**。勝手に追加しない。
- GPT-6 Luna接続の保証はない。実際のGateway下流モデルとツール呼び出し可否を責任者の監査ログ等で確認し、不明なら未検証と報告する。

## ローカル証跡の保存先（v5追加）
- `/project-setup` が確定した`unit_integration_results_dir`、`e2e_evidence_dir`、`artifact_retention_days`を使用する。既存v5は既定値（`reports/test-results`、`reports/e2e`、90日）へフォールバック。
- 単体/結合テストの正式な集約は`scripts/run_java_tests.py`、E2Eは`scripts/run_e2e.py`を使う。旧JUnitを流用してPASSとしない。
- 90日経過で即時自動削除しない。削除候補を点検し、管理者の明示承認で削除する。

## v5.2 進捗イベント（任意の閲覧UIと疎結合）
- `/spec-change` の各G0〜G6境界で `scripts/progress.py` に状況・次アクションを記録する。記録は承認証明ではない。
- ダッシュボードの起動は必須ではない。進捗記録が失敗してもテスト・承認の既存判定は変更しない。
