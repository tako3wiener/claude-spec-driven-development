# Java既存Webアプリのテスト戦略

## テスト実装の選択
1. 既存のJDK/ビルド/テストライブラリを最優先。JUnit 4が現役なら無断でJUnit 5へ移行しない。
2. Maven: `./mvnw test` またはプロジェクトが指定する検証コマンド。Gradle: `./gradlew test`。ラッパー未導入なら既存実行手段を使う。
3. 単体: JUnit + 既存モックツール（例Mockito）。純粋な業務ルール、異常値、境界値、再利用部品を高速に確認。
4. 結合: 既存の統合テスト基盤を優先。Springなら既存の `@SpringBootTest` / `@WebMvcTest` / `@DataJpaTest` 等。実DBの契約が重要なら許可済みテストDBまたはTestcontainers（導入承認後）。
5. API: HTTPステータス/エラー形式/認可/入力値/ページング/排他/DB副作用を検証。既存HTTPテストフレームワークがあれば優先。
6. UI E2E: 実際のWeb UIがある場合、**Playwright for Java + 既存JUnit**を標準とする。既存Seleniumは影響評価・移行計画なしに削除しない。主要業務動線を再実行可能なテストとして保存する。AIの手動ブラウザ操作のみでは完了にしない。
7. E2E環境: 本番隔離、固定fixture、専用アカウント、データ復元/片付け、タイムアウト、時刻/タイムゾーン固定を準備する。

## ケース分類（適用/対象外の理由をREQ単位で記録）
| 層 | 観点 | 代表例 |
|---|---|---|
| Unit | 正常・異常・境界・同値 | 0,1,最大,最大超,空,Null,不正形式 |
| Unit | 状態遷移・業務ルール | 許可/禁止状態・前後条件 |
| Integration | DB・トランザクション | 制約違反、ロールバック、同時更新 |
| API | 互換性・認可 | 既存レスポンス互換、401/403、入力検証 |
| E2E | 主要業務シナリオ | ログイン→検索→登録→確認→後片付け |
| Regression | 非変更箇所への影響 | 同じサービス/API/DB表/画面を共有する既存機能 |
| Nonfunctional | 性能・セキュリティ・監査 | SQLi/XSS/CSRF、マスキング、応答時間 |

## 設計の順序と独立性
- G2前に期待結果を自然言語で確定（HTTPコード、DB差分、画面表示、イベント、ログ等）。
- テストケースはREQと出典を紐付け、コード生成担当は期待結果を編集しない。
- テストが実装に合わせられていないことを独立レビューで確認。
- 変更後: 変更対象テスト → 関連統合/契約 → 影響範囲の回帰 → E2E → 全体テスト（実行可能なら）。

## 障害時と証跡
- `PASS/FAIL/BLOCKED/SKIP` を分ける。環境未準備はBLOCKED、仕様不明はテスト設計保留。
- リトライ成功時も初回失敗/flakeを報告し、通常成功に水増ししない。
- Maven Surefire/FailsafeのXML、Gradle XML、CI実行URL、E2Eのtrace/screenshot/log（機密除外）を証跡として関連付ける。
- DBがE2E環境でも共有される場合は各テストデータを一意にし、後片付け前後を検証。

## リリース非機能条件
既存スローテスト、必要な負荷試験、セキュリティスキャン、性能基準はプロジェクトに準拠。単体テスト100%成功だけでは本番反映可能と結論しない。

## Playwrightスクリーンショットと実行証跡
詳細は `docs/ai/e2e-evidence.md` を参照する。E2Eは操作前・主要操作後・アサーション成功後のPNGを取得し、失敗時も可能ならPNGを保存。全TCについてPlaywright Traceを保存する。`manifest.json`のスクリーンショットハッシュ、実行時刻、環境origin、TC ID、GitコミットIDを出力する。`index.md`に対応関係を一覧化し、JUnit XML/CSV結果を突合する。

## テスト実行前の環境確認（G-SETUP）
- 起動時は現在のGitリポジトリ・Javaモジュールと、許可済みE2E環境が一致するか確認する。未設定は `/project-setup` を使用する。
- `python3 scripts/project_setup.py check` が成功しない場合、Javaの単体/結合/E2Eを実行しない。
- `python3 scripts/run_e2e.py --change-id CHG-001 --run-id <RUN-ID>` をPlaywright Javaの標準起動経路とする。スクリーンショット証跡運用は維持する。

## v5 の実行基準（本節が旧記述より優先）
- `dev`では部分テストを許容し、`final`で承認済みの全必須TCを新規RUNで検証する。最終E2Eは`run_e2e.py`のみ。
- E2Eはサンプル`examples/playwright-java`を正式ケースとして扱わず、業務REQ→TC→固有Javaテストソース→JUnit結果を必須とする。
- テスト用ネットワーク隔離はOS/コンテナ層で実施し、責任者のNET署名を必要とする。
- PNG/Trace/JUnit XMLの自動集約は`docs/ai/e2e-evidence.md`参照。完了条件に独立レビュー/人間G6承認を含む。


## 実行結果の集約（v5保存先拡張）
単体・結合テストは `run_java_tests.py --phase unit|integration --change-id CHG-001` を使うと、`/project-setup`で設定した保存先へRUN-IDごとに新規JUnit XMLを生成し、件数・失敗・skip・終了コードをまとめる。結合テストコマンドは任意設定なので、未設定なら結合テスト集約はBLOCKED。詳細は `docs/ai/artifact-storage.md` を参照。
