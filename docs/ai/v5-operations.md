# v5 運用手順：署名付き承認・実行環境隔離・Playwright証跡

## 適用範囲
既存Java Webアプリ／単一対象モジュール／Playwright for Java＋JUnit。R01,R02,R03,R05-R10,R14,R16を実装し、R04/R11/R13/R17/R19は条件付きで運用する。R12(CI強制)、R15(多モジュール自動対応)、R18(高度な網羅性評価)は今回追加しない。

**重要：本パックだけで完全なアクセス制御・ネットワーク遮断・証拠の真正性は成立しない。** Claude Codeのリポジトリ内Hookは補助ガード。OS/コンテナ制御、外部の署名鍵・承認者の権限管理、専用テストアカウントが必要。これらが未設定なら正式E2Eを`BLOCKED`とする。

## 0. 導入
- v4をバックアップし、v5を既存Javaリポジトリのルートへ統合。`CLAUDE.md`/`.claude/settings.json`は既存設定を上書きせず差分を手で統合する。
- 最小の実行環境はPython 3.9+、Git、OpenSSL。Java/JDK、Maven/Gradle、Playwrightブラウザは後から診断する。OSによるネットワーク隔離や鍵格納はユーザー環境に実装する。
- `.claude/local`と`reports/e2e`はGit除外。画像/TraceにはDOM・入力値・通信などの機密情報が入り得る。人工テストデータのみ利用、閲覧ACLと保持期間を事前に合意する。

## 1. プロジェクトとツールの初期設定

```bash
python3 scripts/project_setup.py init
python3 scripts/project_setup.py check
python3 scripts/toolchain.py doctor --phase setup
python3 scripts/toolchain.py doctor --phase dev
python3 scripts/toolchain.py doctor --phase e2e
```

`/project-setup`でGitコードベース・モジュール・URL・単体/結合/E2Eコマンド・実行場所を人が確認する。サンプル`examples/playwright-java`は`DEMO_ONLY`で、正式E2Eに使えない。後から構成を変更すると設定とG2のハッシュが失効する。

Playwright Javaの実際の依存版は有効POM/解決依存から取得する。親POMやGradleカタログに変数がある場合も、宣言だけを実際のバージョンと断定しない。

```bash
# 外部プラグイン取得・Gradle build logic実行の可能性があるため人が承認してから
python3 scripts/toolchain.py resolve --approve-resolution
python3 scripts/toolchain.py plan
# ダウンロード内容を別途承認した場合のみ
python3 scripts/toolchain.py install --apply --approve-downloads
python3 scripts/toolchain.py doctor --phase e2e
```

`doctor`/`plan`は外部取得しない。`resolve`/`install`はネットワーク・build logic・プラグイン実行を伴う。必要な認証情報をリポジトリに書かない。ブラウザBOOTSTRAPPEDは実起動成功の証明ではない。

## 2. G2：署名付きの外部人間承認

G2対象ファイル:
- `docs/changes/CHG-001/change-plan.md`
- `docs/changes/CHG-001/requirements.csv`
- `docs/changes/CHG-001/test_cases.csv`
- `.claude/local/project-env.json`

署名前に人間が内容・影響範囲・テスト期待結果を確認。`python3 scripts/approval_gate.py hashes --change-id CHG-001`で4つのSHA-256を表示。

以下の**JSONの構造を例として**、承認者が自分で署名する。`*_hash`はコマンド出力の値に置き換える（例の値のまま使わない）。

```json
{
  "change_id": "CHG-001", "gate": "G2", "decision": "APPROVED",
  "approver": "担当者ID", "approved_at": "2026-11-23T11:00:00+09:00",
  "requirements_hash": "<実SHA256>", "test_cases_hash": "<実SHA256>",
  "scope_hash": "<実SHA256>", "project_config_hash": "<実SHA256>"
}
```

承認JSONを `.claude/local/approvals/CHG-001-G2.json` に保存。**署名はClaude Code実行環境の外**で作り、署名結果のみ持ち込む。鍵はOpenSSL SHA-256 RSA/ECDSA署名を想定。

```bash
# ここはClaude Codeのセッション外かつ秘密鍵を読ませない環境で操作する
openssl dgst -sha256 -sign /secure/operator-private.pem \
  -out CHG-001-G2.sig CHG-001-G2.json
# 署名JSONと.sigだけを .claude/local/approvals/ に転送する
# 公開鍵もClaudeに書き換えられないOS管理の場所へ配置
export SDD_APPROVAL_PUBLIC_KEY=/operator-managed/approval-public.pem
export SDD_CHANGE_ID=CHG-001
python3 scripts/approval_gate.py verify-g2 --change-id CHG-001
```

秘密鍵をリポジトリやClaude起動環境へ渡すと強制保証がなくなる。公開鍵とポリシーをClaudeが変更できるだけでも保証は弱くなる。外部からのファイルの読取専用マウント、管理されたClaude Code設定、OSのファイル権限が望ましい。

## 3. R03：本番・許可外環境へのネットワーク遮断

URLの文字列比較は遮断にならない。**外部で**以下を検証してから、NET承認書へ署名する。

- E2Eを専用のコンテナ・VM・ホストで起動し、**OS/コンテナ/プロキシでアウトバウンド通信を明示的許可先に限定**。DNS別名・リダイレクト・ポップアップ・WebSocket・SSO・CDN・バックエンドからのAPI接続も検証する。
- 専用低権限ユーザー/DBを使用。削除・課金・本番通知・実顧客への通信権限がないことを確認。
- テストデータは人工データ。スクリーンショットとTraceを保存してよいこと、保管先とアクセス権を確認。

NET署名用JSON例（署名方法はG2と同じ、ファイル名を`CHG-001-NET.json/.sig`にする）:

```json
{
  "change_id": "CHG-001", "gate": "NET", "decision": "APPROVED",
  "approver": "検証環境責任者", "approved_at": "2026-11-23T11:00:00+09:00",
  "allowed_origin": "http://localhost:8080", "isolation_mode": "container-egress-policy",
  "dedicated_test_credentials": true, "synthetic_test_data": true,
  "trace_storage_approved": true
}
```

`isolation_mode`は`container-egress-policy`/`network-firewall`/`dedicated-isolated-host`のいずれか。**このNET署名は責任者の確認記録であり、技術的なファイアウォールを作成しない。** 虚偽の申告や誤設定はスクリプトだけでは検出できない。

```bash
python3 scripts/approval_gate.py verify-net --change-id CHG-001 \
  --allowed-origin http://localhost:8080
```

## 4. G3〜G5：開発・正式E2E

G2承認後、開発中は変更箇所の単体テストなど部分実行を許す（`dev`）。最終判定では同じ承認済みTCを再実行し、テストスキップ・JUnit 0件を失敗扱いにする。直接E2E環境変数を上書きしない。

```bash
python3 scripts/run_e2e.py --change-id CHG-001 --dry-run
python3 scripts/run_e2e.py --change-id CHG-001 --mode final
```

処理は `G2署名検証 -> NET署名検証 -> 正式Javaテスト存在確認 -> Playwright/Java診断 -> 新規RUN_ID作成 -> Maven/Gradleテスト -> 収集 -> 検査 -> summary.json`。証跡は`reports/e2e/CHG-001/<RUN_ID>/`へ保存、再利用不可。`RUN_ID`、Git HEAD、tracked diffのSHA、untrackedソースのハッシュからスナップショットを識別する。生成された`target/`や`build/`は識別対象外。

自動で出力される主要ファイル: `run-context.json`, `junit/`, `TC-*/01-*.png`, `TC-*/02-result-verified.png`, `TC-*/trace.zip`, `TC-*/manifest.json`, `index.md`, `test_results.csv`, `verification.json`, `summary.json`。

JUnitは今回のRUN専用の空のフォルダに出力。Maven Surefire/Failsafeには別のreportsDirectoryを渡し、Gradleには`e2e-junit.init.gradle`で保存先を設定する。既存プロジェクトがこれを上書きするなら、旧XMLからPASSを作らずBLOCKEDとする。

`summary.json`の`status=PASS`は**ローカルの形式・実行整合性が確認できた**という意味であり、外部CIが署名した証拠ではない。R12は今回保留。

## 5. G6：人間の最終受入

ユーザーが`summary.json`・スクリーンショット・Trace・JUnit結果・仕様照合・残課題をレビュー後、G6署名を作成する。G6 JSONは`change_id`, `gate=G6`, `decision=APPROVED`, `approver`, `approved_at`, `run_id`, `snapshot_id`, `summary_sha256`を持つ。`summary_sha256`は`sha256sum reports/e2e/CHG-001/<RUN_ID>/summary.json`で取得。`verify-g6`は現時点のGitスナップショットと証跡のハッシュを検査する。

```bash
python3 scripts/approval_gate.py verify-g6 --change-id CHG-001 \
  --run-dir reports/e2e/CHG-001/<RUN_ID>
```

**署名G6はマージ・デプロイの自動実行権限ではない。** 今回R12は保留なので、既存のPR/マージ制御に従って人が判断する。

## 6. 条件付き機能
- **R04:** 開発中の部分テストは許可。正式E2Eでテストスキップ・0件・結果不足を拒否。
- **R11:** ヘルスチェック`project_setup.py probe --confirm-network`はNET確認後に人が許可して実施。アプリ自動起動・DB seed/cleanupは承認済みの個別アダプタが用意されるまで未実装（自動削除しない）。
- **R13:** 人工データ・Git除外・最小権限・Trace保存の責任者承認を必須とする。暗号化保管、保持日数、ACL自動設定は各組織環境で要実装。
- **R17:** Playwrightの安定Locator、auto-wait、独立BrowserContextを採用。Page Objectやクロスブラウザは必要時のみ。
- **R19:** `scripts/model_probe.py`は外部Gatewayの署名済み実際のモデルルーティング受領記録を検証する。コスト自動集計は未実装、Gateway側計測を要する。`gpt-6-luna`等モデル名を入力しただけでは検証済みとしない。

## 7. v4との互換性・限界
- v4のPNG/Trace/manifestは閲覧できるがv5 G4の正式PASSには使用できない。新RUNで再実行する。
- v4の`project-env.json`（schema_version 1）を引き継げる。`/project-setup`で正式E2Eモジュールがサンプルではないことを確認してから実行する。変更した場合G2は失効する。
- v4の`.claude/settings.json`と既存ユーザー設定はマージ方法により優先順位が変わる。実Claude Code上で`/hooks`等の読み込み確認が必須。
- JDK/Playwright/Chrome実実行、外部署名鍵の保護、実ネットワーク隔離、既存業務仕様の照合は、このオフライン配布物単体では実施済みではない。初回導入時の受入試験が必要。

承認JSONには任意で`expires_at`（タイムゾーン付きISO 8601）を付けられる。期限超過または不正な期限なら承認は失効する。

**モデル利用に関する注意:** Claude Codeの公式ドキュメントは、他社モデルへのGateway経由ルーティングをサポート対象として保証していない。Lunaの実接続は独自Gateway側の責任で検証し、問題があればClaude Codeの公式対応モデルへ切り替えるか別の実行基盤を選ぶ。<https://code.claude.com/docs/en/llm-gateway>


## 証跡保存設定の拡張（v5追加）
`/project-setup`が2つの保存先（既定`reports/test-results`/`reports/e2e`）と保持期間（90日）を確認・保存する。Java EvidenceRecorderと`scripts/run_e2e.py`はE2E保存先を共有し、変更ID/RUN-ID/TC-IDで格納。`run_java_tests.py`で単体・結合JUnit XMLを新規RUNに保存する。旧v5設定値が欠ける場合は既定値に補完。保存先変更はG2署名対象の設定変更となる。

90日後の削除は自動実行せず、`prune_artifacts.py`で候補を一覧後、明示承認した管理者のみ `--apply --confirm-delete` を実行。CI・定期ジョブへの登録は運用側が検討する。
