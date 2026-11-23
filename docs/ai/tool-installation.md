# Java Web仕様駆動開発：ツール診断と安全な取得

## 前提：ZIPに同梱されるもの／されないもの
**含まれる:** Claude CodeのSkill/Subagent設定、標準ライブラリPythonスクリプト、Playwright Java + JUnitのサンプルコード/依存定義、証跡処理。
**含まれない:** `claude` CLI、Git、Python実行環境、JDK、Maven/Gradle実行バイナリ、Playwrightブラウザ本体、OS共有ライブラリ、モデルゲートウェイ。オフライン完結パックではない。

| 対象 | 自動診断 | 承認後の取得 | 不足時の扱い |
|---|---|---|---|
| Git | PATHと版 | OS導入はしない | https://git-scm.com/downloads |
| Python 3.9+ | 実行中の版 | OS導入はしない | https://www.python.org/downloads/ |
| Claude Code CLI | PATH | インストールはしない | https://code.claude.com/docs/en/setup |
| JDK | java/javac + プロジェクトの要求版 | OS導入はしない | https://adoptium.net/temurin/releases/ |
| Maven/Gradle | 既存Wrapper優先、PATH | Wrapper使用時は指定版をダウンロード | Maven: https://maven.apache.org/install.html ・ Gradle: https://gradle.org/install/ |
| Playwright Java依存 | 既存E2E pom/build.gradle | 既存のビルド依存解決を利用 | 依存定義がない場合はG2で承認して追加 |
| Playwright Chromium | ビルド定義のSHA256とインストール履歴 | Playwright Java CLI `install chromium` | 成功後も実ブラウザ起動を確認 |
| OSブラウザライブラリ | 実ブラウザ起動時に検出 | 自動sudoしない | OS管理者による承認・導入 |

## 運用
初めに `/project-setup` でGitルートとJavaモジュール、E2EモジュールとURL、テストコマンドを確定する。続けて `/tool-setup` を呼ぶ。

```sh
python3 scripts/toolchain.py doctor --phase setup  # システム・JDK・ビルド
python3 scripts/toolchain.py doctor --phase dev    # Javaテスト実行可能性
python3 scripts/toolchain.py doctor --phase e2e    # Playwrightブラウザ含む
python3 scripts/toolchain.py plan                   # ダウンロード不要、内容の提示
python3 scripts/toolchain.py install --apply --approve-downloads  # 明示承認後だけ実行
python3 scripts/toolchain.py doctor --phase e2e    # 証跡確認
```

- `install`は `scripts/project_setup.py` の承認済みE2Eコマンドに書かれた `-f pom.xml` 等から対象E2Eビルドを解決する。意図しない別リポジトリに書き込まない。
- Maven: `mvn -f <approved-pom> test-compile exec:java -Dexec.classpathScope=test -Dexec.mainClass=com.microsoft.playwright.CLI -Dexec.args="install chromium"` に相当するコマンドを実行。Maven Exec Plugin、Maven依存、ブラウザが取得される場合がある。依存取得元は `settings.xml`、pom.xml等に従う。
- Gradle: 既存のJavaモジュールを対象に、一時的なInit Scriptを指定して `bootstrapPlaywrightBrowser` タスクを実行。ビルド定義を編集せず、依存構成からPlaywright Java CLIを呼ぶ。複雑なマルチモジュール構成は個別の設定/承認が必要。
- **重要:** Maven/Gradleのビルドスクリプト／プラグイン自体が任意コードを実行し得る。実行前にソース・依存リポジトリ・CIポリシーをレビューし、隔離された開発環境で行う。`install` が自動で接続先の信頼性を検証するものではない。
- `.claude/local/toolchain-lock.json`はプロジェクトビルド定義のSHA256、利用したPlaywright版、ブラウザ種別、OS/CPU、取得日時を保存する。対象ビルドの変更または別マシンでは再取得が必要になる。
- 取得成功は**Playwright CLIの終了コードが0だったことの記録**にすぎない。Javaテストで実際にChromiumを起動し、画面キャプチャ・Trace・JUnit XMLの証跡を検証するまでE2E完了にしない。
- 企業プロキシ/社内ミラーは**事前承認済みのMaven/Gradle設定**で指定。`curl | sh`、HTTPS検証の無効化、プロキシの迂回は禁止。
- OS共有ライブラリの不足が出たらPlaywrightの公式要件を確認し、管理者が承認したコンテナ/OSイメージで整備する。`install --with-deps` の自動sudo実行は行わない。

## モデル接続について
GPT-6 LunaをClaude Codeで使うためのモデル互換ゲートウェイは、ここでは調達・自動導入しない。使用契約とセキュリティ設計に沿って管理者が構成し、`docs/ai/model-routing.md` の読み取りタスクで接続検証する。Claude Code CLI本体やAPIキーはこのZIPに含まれない。

## 公式参照
- https://playwright.dev/java/docs/browsers
- https://maven.apache.org/tools/mavenwrapper.html
- https://docs.gradle.org/current/userguide/gradle_wrapper.html
- https://code.claude.com/docs/en/hooks

## v5 変更：依存の実解決が必須
`toolchain.py doctor --phase setup|dev|e2e`に分離。`dev`はE2Eモジュール不要。
Maven親POM・Gradle catalog等からの**実解決**には`python3 scripts/toolchain.py resolve --approve-resolution`を使用。これは依存取得やbuild logic実行を伴い得るので事前に人が承認する。`doctor/plan`はネットワーク取得しない。解決版をローカルlockへ記録し、ローカルbuild情報の変更時に再解決する。`docs/ai/v5-operations.md`参照。
