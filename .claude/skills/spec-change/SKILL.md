---
name: spec-change
description: 既存Java Webアプリの仕様→REQ/TC→署名G2→実装/テスト→Playwright正式E2E→G6受入を管理する。
disable-model-invocation: true
---

# v5.2 仕様駆動開発 /spec-change CHG-001

**進捗イベント記録（ダッシュボード非依存）**：工程の開始・待機・BLOCKED・終了時に `python3 scripts/progress.py --change-id CHG-001 --gate G0..G6 --state running|waiting|blocked|done --next-action "次にする作業"` を呼ぶ。開始時G0 running、G1開始・G2待機・承認後G3開始・G4開始・G5開始・G6待機・最終終了の各境界で必ず更新する。待機中は理由とユーザーの次アクションを記録する。進捗は**作業申告**であり署名・承認・品質判定の代替ではない。記録失敗時は警告して本来の作業を続ける。`docs/ai/progress-events.md`を参照。

0. `docs/ai/v5-operations.md`を読み、`python3 scripts/project_setup.py check`および`toolchain.py doctor --phase dev`を実施。今回のGitコードベースとテスト環境をユーザーへ明示。`SDD_CHANGE_ID`を変更IDへ設定する（承認証明ではない）。
1. G1: `spec-analyst`で要件定義/基本設計、既存コード/既存テストを調査。REQに出典・種別A/B/C/Dを付与、C/Dは未承認のまま実装しない。`docs/changes/<CHG>/requirements.csv`, `change-plan.md`を生成。
2. 実装**前**に`test-designer`で正常/異常/境界/回帰/権限を整理し、`test_cases.csv`にTC-ID・期待結果・Javaテスト実装場所を割り当てる。正式E2Eは`examples/`を使用せず、実アプリ固有のJavaテストへ対応させる。`check_traceability.py`を適宜実行。
3. G2: 人に変更設計・要件・期待結果・既存機能への影響を提示。**承認前はJava実装しない**。ユーザーが承認を述べてもそれだけを機械的署名とみなさない。外部鍵の署名G2を作成するのは人の操作であり、Claudeは秘密鍵を扱わない。`approval_gate.py hashes/verify-g2`で検査。
4. G3: `java-developer`に変更単位の実装を委譲。JUnit/結合、静的解析を実行。正式結果の集約は`scripts/run_java_tests.py --phase unit|integration --change-id <CHG>`を利用し、設定済み保存先へRUN-IDで保存する（結合コマンドは任意設定）。`dev`モードでは部分テストを許可し、正式ゲートの全件実施と混同しない。テスト失敗を理由に期待値を変更しない。
5. G4: `e2e-runner`に正式E2Eを委譲。先にG2、ネットワーク隔離の実運用確認と**NET外部署名**、toolchain e2e診断、正式テスト実装をチェック。`python3 scripts/run_e2e.py --change-id <CHG> --mode final`を実行。スクリーンショット、Trace、JUnit XML、run-context/summary/indexを保存。未実施、SKIP、DEMO_ONLY、証跡欠落はBLOCKED。
6. G5: `independent-reviewer`に仕様↔コード↔TC↔実行結果↔証跡の照合を依頼。G4のローカル整合性PASSは仕様充足と同義ではない。未解決課題を列挙する。
7. G6: 人がコード差分・独立レビュー・JUnit・画面証跡と残存リスクを確認し、最終署名G6を実施。`approval_gate.py verify-g6`でRUN_ID/snapshot/summaryハッシュを照合する。マージ・デプロイは自動実施しない。
8. 外部隔離、署名鍵、対象モジュール/接続先、Gatewayが未準備なら**その工程をBLOCKED**と報告。未準備をPASSと言わない。

参考：`docs/ai/v5-operations.md`, `docs/ai/quality-gates.md`, `docs/ai/e2e-evidence.md`。
