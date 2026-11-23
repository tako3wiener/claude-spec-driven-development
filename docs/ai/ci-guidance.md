# CI・PR品質ゲートの最低要件

1. 変更対象のビルド、単体、結合、E2E、既存回帰をプロジェクト固有コマンドで実行する。環境が無い場合はSKIPでなくBLOCKEDとしてPRを止める。
2. JUnit XML、JaCoCo等のカバレッジレポート、E2E trace、実行ログをArtifactとして保存する。失敗時もログ収集。
3. `python3 scripts/check_traceability.py docs/changes/<CHG-ID> --require-approval --require-results` を必須チェックに追加する（表の整合性チェックのみ）。
4. `docs/spec/**` の原本変更、`templates/**` や `.claude/**` の防御設定変更、受入条件・期待結果の変更はCODEOWNERSで人間レビューを必須にする。
5. 人のG2承認を実装前に、G6承認をマージ前に確認する。CSV内の文字列やAI作成の承認欄だけでは不十分で、PR承認履歴を真の証拠とする。
6. PRの必須ステータス、最少承認人数、承認後の再変更による承認無効化、直接push禁止、保護ブランチを設定する。
7. テストDB/外部APIは低権限の隔離環境のみ使用。SecretsはCI Secret Storeで管理し、テストログを秘匿化。
8. CIの実装はGitHub Actions/GitLab/Jenkins等の現行ツールが判明してから作成する。既存JavaアプリのMaven/Gradle・E2Eコマンドが不明の状態でCIファイルを作り込まない。

## Playwright E2E artifactの必須チェック
- 許可されたテストURL/SecretだけでPlaywright for JavaのJUnitテストを実行する。
- `E2E_RUN_ID`をCI実行単位に一意に設定し、終了時に`reports/e2e/<CHG-ID>/<RUN-ID>`を**失敗時も**アップロードする。
- `scripts/build_e2e_index.py`で一覧を生成し、`scripts/check_e2e_evidence.py ... --require-pass`を必須チェックにする。JUnit XMLをパースするCIのtest report機能も有効化する。
- テストエビデンスはGitにコミットせず、CI artifactの公開範囲を制限する。Traceにはマスク前のDOM/ネットワーク情報が含まれ得る。

## G-SETUP: CIでも実行先を固定する
- CIで `.claude/local/project-env.json` をGitから取得しようとしない。保護されたCI環境で明示された対象URLとコードベースに基づき、`python3 scripts/project_setup.py init --non-interactive ... --confirm-codebase --confirm-isolated` を実行する。フラグだけでは承認の証明にならないためCIのenvironment approvalを使う。
- `python3 scripts/project_setup.py check` → `python3 scripts/run_e2e.py --change-id <CHG-ID> --run-id <CI-run-id>` の順序で実行する。
- `SessionStart` hookやBashコマンド検知は完全な安全境界ではないため、テスト環境外への通信はCIのネットワーク権限とテスト用認証情報で制限する。
