# Claude Code + GPT-6 Luna / 既存Java Webアプリ仕様駆動開発スターター v5.3

**現状:** v5スターター実装／実ユーザー環境への組み込み・ブラウザ実起動は未検証。v4から改修した新規成果物。仕様書→G2承認→Java実装→JUnit/結合→Playwright for Java E2E・PNG/Trace→独立レビュー→G6承認を支援する。外部のGateway/OSサンドボックスは同梱されない。

## 重要な境界

- **Claude CodeのHookだけで本番操作・外部接続を完全に防ぐことはできない。** 必須のOS/コンテナアウトバウンド制御は利用者の環境で構築する。接続先に関する責任者のNET承認がない場合、正式E2EはBLOCKED。
- **v5.3: 電子署名不要。** G2/G6/NETは人が別ターミナルから `approval_gate.py record-*` で記録し、JSONと対象ハッシュを検証する。鍵・.sig不要。本人証明や耐改竄性はない。
- **Luna利用には独自Gatewayの互換性実証が必要。** Anthropicは非ClaudeモデルへのGateway利用をサポート対象として保証していない。モデル名だけでは接続・ツール呼び出しの成功を保証しない。
- R12（CI強制）は保留なので、ローカルの証跡PASSを独立CIの真正性証明と誤認しない。

## インストール方法（初回導入）

このツールは既存のJava WebアプリのGitリポジトリにスクリプト、Claude Code Skills/Agents、設定、テンプレートを組み込んで使います。ツール自身のリポジトリをJavaアプリの開発対象にする必要はありません。ダッシュボードは任意の別アプリです。

### 1. 前提環境

- Git、Python 3.9以降（`python3`）、Claude Code CLI（`claude`）。Claude Codeは別途インストール・認証を済ませる。
- 対象アプリに適合するJDK、MavenまたはGradle（既存のWrapperがあれば優先）。Playwright for Java、対応ブラウザ・OS依存ライブラリはE2E用に別途準備する。
- GitHubのPrivateリポジトリにアクセス可能なGit認証。HTTPSならGit Credential Manager等、または設定済みSSHを使う。トークンをURL・ソース・設定ファイルに書かない。
- 以下のコピー例はmacOS/Linux/WSLのBashと`rsync`を使用。WindowsでWSLを使わない場合は同じフォルダ構成をエクスプローラー等で手動統合する。

```bash
git --version
python3 --version
claude --version
java -version
```

JDKとビルドツールの版は対象アプリの既存要件に従う。G2/NET/G6の通常承認にOpenSSL・秘密鍵は不要（R19の任意のGateway署名検証は別機能）。

### 2. スターターを取得する

既存のJavaプロジェクトとは別の場所へcloneする。以下のパスは例なので自分の環境に読み替える。

```bash
mkdir -p "$HOME/work"
cd "$HOME/work"
git clone https://github.com/tako3wiener/claude-spec-driven-development.git
STARTER="$HOME/work/claude-spec-driven-development"
APP="$HOME/work/your-java-project"
```

SSHを利用する場合は `git clone git@github.com:tako3wiener/claude-spec-driven-development.git` でもよい。Privateリポジトリのため、GitHubに未認証または権限がない場合は取得できない。

### 3. 既存Javaリポジトリに組み込む

対象アプリの未コミット変更を退避またはコミットしてから、作業ブランチを作成する。既存の設定やソースを一括上書きしない。

```bash
cd "$APP"
git rev-parse --show-toplevel
git status --short
# 作業ツリーがクリーンであり、APPがGitルートであることを確認してから実行
git switch -c chore/install-sdd
```

組み込む対象は `CLAUDE.md`、`.claude/settings.json`、`.claude/skills/`、`.claude/agents/`、`scripts/`、`templates/`、`docs/ai/`、`examples/playwright-java/`、`.gitignore`。以下は既存ファイルを上書きしないコピー例。最初は必ずdry-runを確認する。

```bash
# 追加予定ファイルを確認（既存ファイルはスキップ）
rsync -rni --ignore-existing \
  --exclude='/.git/' --exclude='/README.md' \
  --exclude='/.claude/local/' --exclude='/reports/' \
  "$STARTER/" "$APP/"

# 同名の既存ファイルで内容が異なるものを確認（コピーは行わない）
rsync -rnic --existing \
  --exclude='/.git/' --exclude='/README.md' \
  --exclude='/.claude/local/' --exclude='/reports/' \
  "$STARTER/" "$APP/"

# 内容を確認してから追加分のみコピー
rsync -ri --ignore-existing \
  --exclude='/.git/' --exclude='/README.md' \
  --exclude='/.claude/local/' --exclude='/reports/' \
  "$STARTER/" "$APP/"
```

既存の `CLAUDE.md`、`.claude/settings.json`、`.gitignore` やスクリプト等に同名ファイルがある場合は差分を確認し、必要なSkills/Agents、permissions、SessionStart/PreToolUse Hook、除外ルールを手動統合する。JSONは有効な形式を維持し、既存Hookも消さない。特に `.claude/local/` とテスト証跡、秘密情報をGit管理対象にしない。

```bash
cd "$APP"
git status --short
git diff
python3 -m json.tool .claude/settings.json
python3 -m compileall -q scripts
python3 -m unittest discover -v -s scripts/tests
```

テストで既存アプリへ接続する必要はない。既存の同名テストがある場合は内容を確認して実行する。変更内容をレビューして対象プロジェクトの運用に従いコミットする。ここでは本番環境への接続やデプロイは行わない。

### 4. プロジェクト初期設定とツール診断

JavaリポジトリのルートでClaude Codeを起動する。

```bash
cd "$APP"
claude
```

Claude Code内で順番に実行する。

```text
/project-setup
/tool-setup
```

`/project-setup`では対象Javaモジュール、local/test/stagingのURL、単体・結合・E2Eのコマンド、保存先（既定: `reports/test-results` と `reports/e2e`）、保持期間（既定90日）を確認する。設定は `.claude/local/project-env.json` に保存される。必要なら通常のターミナルから `python3 scripts/project_setup.py init` で対話設定してもよい。

`/tool-setup`ではJDK、ビルドツール、Playwright Java、ブラウザの準備状況を診断する。依存解決・ダウンロードは内容を人が承認した後だけ実行する。G2前のHookで解決・インストール操作が拒否された場合は、承認済みのコマンドを人が別ターミナルから実行する（Hookを無効にしない）。

```bash
python3 scripts/project_setup.py check
python3 scripts/toolchain.py doctor --phase setup
python3 scripts/toolchain.py doctor --phase dev
python3 scripts/toolchain.py doctor --phase e2e
```

Claude Code内で `/hooks` などを利用して設定が読み込まれていることを確認する。実際のE2E対象アプリ専用のPlaywright Java/JUnitテストと外部ネットワーク隔離も準備する。`examples/playwright-java` はDEMO_ONLYで正式テストには使用できない。

### 5. 仕様駆動開発を開始する

対象アプリ内の `docs/spec/` に仕様書を配置する（ディレクトリがなければ作成）。変更IDを決めてClaude Codeを起動する。

```bash
cd "$APP"
mkdir -p docs/spec
export SDD_CHANGE_ID=CHG-001
claude
```

Claude Code内で実行する。

```text
/spec-change CHG-001
```

G2で待機したら、仕様・影響・テストケースを確認し、**別の通常のターミナル**で対象アプリのGitルートに移動して承認を記録する。

```bash
cd "$APP"   # 別ターミナルではAPPを設定するか実際の絶対パスを指定
python3 scripts/approval_gate.py record-g2 --change-id CHG-001
```

承認者名と確認文字列を入力し、Claude Code側で続行する。NET・G6も同様に人が記録する。URL・隔離方式・RUN-IDの指定方法を含む詳細は [運用手順](docs/ai/v5-operations.md) を参照。鍵や署名ファイルは不要。

### 6. 更新・アンインストール

スターター側で `git pull --ff-only` を実行しただけでは、導入済みJavaプロジェクトには反映されない。更新のたびに差分を確認し、対象アプリの新しい作業ブランチで必要なファイルを統合して自己テストを実行する。ローカル設定・承認・証跡は自動上書きしない。設定や承認対象が変わった場合は再承認する。

取り外す場合は導入時のGitコミット・差分を基に、追加されたファイルと既存設定へ統合した部分だけを削除する。アプリ固有コード、仕様書、承認記録、テスト証跡を一括削除しない。

進捗の可視化が必要なら、別途 [Spec Change Dashboard](https://github.com/tako3wiener/spec-change-dashboard) を取得し、そのREADMEに従って起動する。導入しなくても本ツールは動作する。

## 開発フローの概要（手順は docs/ai/v5-operations.md を参照）

1. v4をバックアップ。v5を既存Gitリポジトリのルートへ配置し、既存の`CLAUDE.md`と`.claude/settings.json`を**上書きせずに統合**。
2. `claude`を起動、`/project-setup`でコードベース・テスト用URL・正式なJava E2Eコマンドを確認。`examples/`は`DEMO_ONLY`。
3. `/tool-setup`でJDK/Maven/Gradle/Playwright Javaを診断。`resolve`/`install`は依存解決・ダウンロードへの**人の承認後のみ**。
4. `docs/spec/`に原本仕様書、`docs/changes/CHG-001/`に解析結果・変更設計と期待結果を作成。
5. **G2承認:** G2の入力ファイルを人が確認、別ターミナルから `python3 scripts/approval_gate.py record-g2 --change-id CHG-001` を実行。`SDD_CHANGE_ID=CHG-001`をClaude環境に設定。
6. **NET承認:** 実際に隔離されたテストネットワーク・テストDB・人工データ・Trace保存ポリシーを人が確認、別ターミナルで `record-net` を実行（接続先と隔離方式を指定）。
7. G2承認後にClaude CodeがJava改修・JUnit/結合を実施。`run_java_tests.py` で単体・結合結果を集約し、NET承認後に `run_e2e.py --change-id CHG-001 --mode final` で正式E2Eを実行する。
8. 設定済みE2E保存先の`CHG-001/<RUN-ID>/`にあるJUnit/スクリーンショット/Trace/summaryをレビュー。別ターミナルで `record-g6` を実行して検証。マージ・デプロイは別途人が判断。

## 代表コマンド

```bash
python3 scripts/project_setup.py check
python3 scripts/toolchain.py doctor --phase dev
python3 scripts/toolchain.py doctor --phase e2e
# 解決・ダウンロードの必要性を人が承認してから:
python3 scripts/toolchain.py resolve --approve-resolution
python3 scripts/toolchain.py plan
python3 scripts/toolchain.py install --apply --approve-downloads
# 人間の承認記録を作成した後:
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
| R02 | 電子署名必須化は撤回。G2/G6の人間承認記録と対象ハッシュ照合を維持 |
| R03 | NET承認と外部隔離確認が正式E2Eの前提。OSでの隔離を新規構築するわけではない |
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
- `scripts/approval_gate.py`, `scripts/v5_core.py`: G2/G6/NET承認記録・Gitスナップショット
- `scripts/toolchain.py`, `scripts/gradle/*`: phase別診断・実依存解決・Playwright browser取得
- `scripts/artifact_paths.py`, `scripts/run_java_tests.py`: 保存先安全性、単体/結合の新規JUnit証跡
- `scripts/prune_artifacts.py`: 保持期間超過の証跡を一覧表示・明示承認後に削除
- `scripts/run_e2e.py`, `scripts/check_e2e_evidence.py`: Java E2E実行と証拠の収集/照合
- `examples/playwright-java`: **DEMO_ONLY** Javaテスト雛形・EvidenceRecorder
- `docs/ai/v5-operations.md`: 人間承認・NET隔離・運用の詳細手順
- `docs/ai/artifact-storage.md`: 保存先の設定・90日保持・旧v5からの移行

## オフライン自己テスト

```bash
python3 -m compileall -q scripts
python3 -m unittest discover -v -s scripts/tests
```

Pythonの模擬・ローカル試験はJava Webアプリの起動やブラウザ操作の証明ではない。実アプリへ初めて接続する前には専用ネットワークと権限を人が検証すること。

## 任意の進捗ダッシュボード連携 (v5.2)
外部アプリとは独立に `scripts/progress.py` が `.claude/local/progress/` に進捗を保存します。画面は別配布の `spec-change-dashboard` が読み取り専用で表示します。画面未導入でも全工程を実行できます。`docs/ai/progress-events.md`を参照。
