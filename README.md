# Claude Code + GPT-6 Luna / 既存Java Webアプリ仕様駆動開発スターター v5.1（保存先設定拡張）

**現状:** v5スターター実装／実ユーザー環境への組み込み・ブラウザ実起動は未検証。v4から改修した新規成果物。仕様書→G2承認→Java実装→JUnit/結合→Playwright for Java E2E・PNG/Trace→独立レビュー→G6承認を支援する。外部のGateway/OSサンドボックス/承認鍵は同梱されない。

## 重要な境界

- **Claude CodeのHookだけで本番操作・外部接続を完全に防ぐことはできない。** 必須のOS/コンテナアウトバウンド制御は利用者の環境で構築する。接続先に関する責任者の署名付きNET承認がない場合、正式E2EはBLOCKED。
- **人間承認は外部署名で検証。** `.claude/local/approvals`に置くG2/G6/NET JSONと `.sig`、外部OS管理の公開鍵が必要。Claudeから秘密鍵を触れる状態では強い保証にならない。
- **Luna利用には独自Gatewayの互換性実証が必要。** Anthropicは非ClaudeモデルへのGateway利用をサポート対象として保証していない。モデル名だけでは接続・ツール呼び出しの成功を保証しない。
- R12（CI強制）は保留なので、ローカルの証跡PASSを独立CIの真正性証明と誤認しない。

## 初期導入（手順は docs/ai/v5-operations.md を参照）

1. v4をバックアップ。v5を既存Gitリポジトリのルートへ配置し、既存の`CLAUDE.md`と`.claude/settings.json`を**上書きせずに統合**。
2. `claude`を起動、`/project-setup`でコードベース・テスト用URL・正式なJava E2Eコマンドを確認。`examples/`は`DEMO_ONLY`。
3. `/tool-setup`でJDK/Maven/Gradle/Playwright Javaを診断。`resolve`/`install`は依存解決・ダウンロードへの**人の承認後のみ**。
4. `docs/spec/`に原本仕様書、`docs/changes/CHG-001/`に解析結果・変更設計と期待結果を作成。
5. **G2承認:** G2の入力ファイルを人が確認、リポジトリ外の秘密鍵で承認JSONに署名。署名と公開鍵を設定。`SDD_CHANGE_ID=CHG-001`をClaude環境に設定。
6. **NET承認:** 実際に隔離されたテストネットワーク・テストDB・人工データ・Trace保存ポリシーを人が確認、NET JSONに署名。
7. G2承認後にClaude CodeがJava改修・JUnit/結合を実施。必要なら`run_java_tests.py`で結果を設定済み保存先へ集約。E2Eは`python3 scripts/run_java_tests.py --phase unit --change-id CHG-001
# 結合テストコマンドを設定した場合:
python3 scripts/run_java_tests.py --phase integration --change-id CHG-001
python3 scripts/run_e2e.py --change-id CHG-001 --mode final`で実行。
8. 設定済みE2E保存先の`CHG-001/<RUN-ID>/`にあるJUnit/スクリーンショット/Trace/summaryをレビュー。G6承認JSONを署名して検証。マージ・デプロイは別途人が判断。

## 代表コマンド

```bash
python3 scripts/project_setup.py check
python3 scripts/toolchain.py doctor --phase dev
python3 scripts/toolchain.py doctor --phase e2e
# 解決・ダウンロードの必要性を人が承認してから:
python3 scripts/toolchain.py resolve --approve-resolution
python3 scripts/toolchain.py plan
python3 scripts/toolchain.py install --apply --approve-downloads
# 外部署名が配置された後:
python3 scripts/approval_gate.py verify-g2 --change-id CHG-001
python3 scripts/run_e2e.py --change-id CHG-001 --mode final
python3 scripts/approval_gate.py verify-g6 --change-id CHG-001 --run-dir reports/e2e/CHG-001/<RUN-ID>
```

## v5 追加：保存先・90日保持ポリシー

`/project-setup` の初回確認で **単体・結合テスト結果: `reports/test-results`**、**Playwright E2E: `reports/e2e`**、**保持期間: 90日** を確認する。必要ならリポジトリ内の安全な相対パスに変更でき、`project-env.json`と `.gitignore` に反映される。既存v5の設定ファイルで新項目がなくても、この3つの初期値を用いる（schema_version=1のまま）。変更するとG2の設定ハッシュが変わるため再承認が必要。

- 単体／結合は `python3 scripts/run_java_tests.py --phase unit --change-id CHG-001` または `--phase integration`（後者は結合テストコマンド設定時のみ）。RUN-ID別の新規JUnit XML・summaryを保存。従来のMaven/Gradle直接実行も可能だが、実行結果は自動集約対象外。
- E2Eは `run_e2e.py` が設定済みE2E保存先へRUN-ID別に記録し、Java `EvidenceRecorder` に同じ絶対パスを渡す。
- 保持期間90日を超えた**完了済みRUN**の削除候補は `python3 scripts/prune_artifacts.py` で一覧。**削除は自動実行しない**。人が一覧を確認後 `--apply --confirm-delete` を明示すると削除する。自動保持期限運用は管理者のスケジューラからこのコマンドを実行する。
- 設定パスはリポジトリ内の2階層以上の相対ディレクトリのみ。ソース・設定・Git管理済みファイル、シンボリックリンク、保存先同士の重複は拒否する。

## v4からの主要変更

| 項目 | v5の実装 |
|---|---|
| R01 | Hookの文字列包含例外を廃止。複合シェルやG2前のコード変更を拒否（OS隔離は別途） |
| R02 | G2/G6の署名検証、承認対象ハッシュとの照合。`approvals.md`は署名証明ではない |
| R03 | NET署名付きの外部隔離確認が正式E2Eの前提。OSでの隔離を新規構築するわけではない |
| R04 | dev部分テストを許可、finalはスキップ/0件/未実行/サンプルを拒否 |
| R05 | `doctor --phase setup/dev/e2e`を分離。devにE2Eモジュール不要 |
| R06 | `resolve --approve-resolution`が解決済Playwright版を記録し、変更時は再解決 |
| R07 | `run_e2e.py`が新規実行・JUnit/画像/Trace索引・総合判定を集約 |
| R08 | HEAD/branch/tracked diff/untrackedソースのハッシュをスナップショットに記録 |
| R09 | PNGの画像データ/CRC、TraceのイベントJSON、JUnitとmanifestの整合を検査 |
| R10 | 新RUNディレクトリ、RUN-IDとsnapshot-ID/token digest紐づけ、JUnit専用保存先 |
| R14 | `examples/playwright-java`を正式E2E判定から除外 |
| R16 | 検査ツールの正常/異常/改変/失敗注入試験を追加 |
| 条件付き | R11: 手動許可ヘルスチェック、R13: 人工データと保管責任者確認、R17: 安定Locator/BrowserContext分離、R19: Gateway署名受領書のモデル検証 |
| 保留 | R12 CI強制、R15マルチモジュール自動対応、R18高度網羅率 |

## 主なファイル

- `CLAUDE.md`: 全工程の不変条件
- `.claude/skills/{project-setup,tool-setup,spec-change}/SKILL.md`: 3 Skill
- `.claude/agents/*.md`: Spec/Test/Developer/E2E/Independent Reviewer
- `.claude/settings.json`: `onFailure: block`付きPreToolUse補助ガード
- `scripts/approval_gate.py`, `scripts/v5_core.py`: G2/G6/NET署名・Gitスナップショット
- `scripts/toolchain.py`, `scripts/gradle/*`: phase別診断・実依存解決・Playwright browser取得
- `scripts/artifact_paths.py`, `scripts/run_java_tests.py`: 保存先安全性、単体/結合の新規JUnit証跡
- `scripts/prune_artifacts.py`: 保持期間超過の証跡を一覧表示・明示承認後に削除
- `scripts/run_e2e.py`, `scripts/check_e2e_evidence.py`: Java E2E実行と証拠の収集/照合
- `examples/playwright-java`: **DEMO_ONLY** Javaテスト雛形・EvidenceRecorder
- `docs/ai/v5-operations.md`: 署名承認・NET隔離・運用の詳細手順
- `docs/ai/artifact-storage.md`: 保存先の設定・90日保持・旧v5からの移行

## オフライン自己テスト

```bash
python3 -m compileall -q scripts
python3 -m unittest discover -v -s scripts/tests
```

Pythonの模擬・ローカル試験はJava Webアプリの起動やブラウザ操作の証明ではない。実アプリへ初めて接続する前には専用ネットワークと権限を人が検証すること。

## 任意の進捗ダッシュボード連携 (v5.2)
外部アプリとは独立に `scripts/progress.py` が `.claude/local/progress/` に進捗を保存します。画面は別配布の `spec-change-dashboard` が読み取り専用で表示します。画面未導入でも全工程を実行できます。`docs/ai/progress-events.md`を参照。
