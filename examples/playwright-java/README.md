# DEMO_ONLY — Playwright for Java + JUnit5 証跡サンプル (v5)

このディレクトリは**教材・単体デモ用であり、正式G4 E2EのPASSには使用禁止**。実案件では承認済みTCに固有のアサーション、データセット、Javaテストソースを別途作成すること。

- `SmokeE2eTest.java`：サンプル表示を確認するだけの**DEMO_ONLY**テスト。業務品質の証拠にできない。
- `EvidenceRecorder.java`：PNG（初期/結果/失敗）、Playwright Trace、各ハッシュとRUN_ID/snapshot-IDを含むmanifestを生成。
- すべてのE2Eは`python3 scripts/run_e2e.py --change-id CHG-001 --mode final`経由。G2/NET署名・専用テスト環境・Playwrightツール診断を要求する。
- 直接サンプルを実験する場合は外部隔離の専用環境と人工データを使う。正式証跡への転用は禁止。
- `BrowserContext.route("**/*", ...)`による同一originガードはブラウザ側の補助策。**OS/コンテナのアウトバウンド遮断の代替ではない。**

参照: `docs/ai/v5-operations.md`, `docs/ai/e2e-evidence.md`。


## E2E保存先設定（v5拡張）
`E2E_EVIDENCE_ROOT` はGitルートではなく、`/project-setup`が保存した `e2e_evidence_dir` の**絶対パス**をPythonランナーから受け取る。Java `EvidenceRecorder` はその下に `<CHG>/<RUN>/<TC>` を作る。このモジュールは `DEMO_ONLY` で正式E2Eに使用しない。
