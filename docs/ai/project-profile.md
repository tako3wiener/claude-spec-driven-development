> **初期設定:** `/project-setup` または `python3 scripts/project_setup.py init` で、コードベース・E2E環境・テストコマンドを人が確認してください。実行設定の正本は `.claude/local/project-env.json`（Git管理対象外）です。表示用の `.claude/local/project-profile.md` が自動生成されます。本ファイルは継続的な設計・構成情報の記録票です。

# 対象システム情報（初回に人が確認して記入）

| 項目 | 値 | 根拠 |
|---|---|---|
| 変更ID | CHG-001 | チケット番号等 |
| 対象リポジトリ/ブランチ | 未確認 | |
| Java/JDKバージョン | 未確認 | `java -version` / pom.xml 等 |
| フレームワーク | 未確認 | Spring Boot/Servlet/Struts/JSF等、実物から判定 |
| ビルド | 未確認 | Maven / Gradle / その他 |
| 既存単体テスト | 未確認 | JUnit/Mockito/TestNG等 |
| 既存結合/E2E | 未確認 | Failsafe/Selenium/Playwright等 |
| Web UI技術 | 未確認 | JSP/Thymeleaf/SPA等 |
| DB/スキーマ移行 | 未確認 | Oracle/PostgreSQL等、Flyway/Liquibase等 |
| API仕様 | 未確認 | OpenAPI等 |
| 認証方式/権限 | 未確認 | |
| テスト環境URL | 未確認 | **本番URL不可** |
| テストDB | 未確認 | 専用・破壊可能範囲を明記 |
| E2Eテストアカウント | 未確認 | 資格情報の値を書かずSecret管理先のみ記す |
| サンドボックス/外部APIモック | 未確認 | |
| テストデータ準備・初期化 | 未確認 | 安全なリセット手順 |
| 実行許可されたビルド/テストコマンド | 未確認 | |
| CI、品質閾値 | 未確認 | |
| 互換性の制約 | 未確認 | 既存API/DB/画面/バッチ等 |
| リリース・ロールバック手順 | 未確認 | |

**原則:** 「未確認」は合理的な推測で埋めない。既存コードから確証がある場合は根拠を記録して更新する。環境情報が不足する場合、実装/テストを可能な範囲に限定し、未実行をPASSにしない。

## E2Eで追加確認する設定
| 項目 | 値 | 根拠・注意 |
|---|---|---|
| E2E実行基盤 | Playwright for Java + 既存JUnit | 既存依存関係と整合させる |
| E2E証跡保存ルート | 未確認 | `E2E_EVIDENCE_ROOT` は設定済みE2E保存ディレクトリの絶対パス |
| E2E環境許可origin | 未確認 | `E2E_ALLOWED_ORIGIN`。アプリURLと厳格一致 |
| E2EアプリURL | 未確認 | `E2E_BASE_URL`。本番不可 |
| ブラウザ・解像度 | Chromium / 1365x768（仮） | G2で確定 |
| ロケール・タイムゾーン | ja-JP / Asia/Tokyo（仮） | G2で確定 |
| スクリーンショット取得地点 | 未確認 | 各TCの操作前・主要操作後・期待結果確認後 |
| 機密箇所・マスク対象 | 未確認 | `data-e2e-sensitive`等。Traceは別途保護 |
| E2E証跡CI Artifactの保持期間 | 未確認 | 組織の情報管理方針を確認 |
| E2Eテスト実行コマンド | 未確認 | Maven/GradleとJUnitバージョンに合わせて確定 |


## 結果保存先（初期設定）
- 単体・結合結果集約先: `reports/test-results`（既定）
- Playwright E2Eエビデンス保存先: `reports/e2e`（既定）
- 保持期間: 90日（既定。超過後の削除には`prune_artifacts.py --apply --confirm-delete`）
- 設定ファイル: `.claude/local/project-env.json`（旧v5で欠ける場合は既定値）
