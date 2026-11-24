# v5.4 独立インストール・コードベース運用

ツールの配置先とJava Gitリポジトリは別ディレクトリ。既存のアプリへのコピー方式は互換機能です。独立方式では常にツールの sdd ランチャーを使ってください。

## 参照パス

- ツール側: scripts/, docs/ai/, templates/, examples/playwright-java/, Claude CodeのSkills/Agents/設定。DEMO_ONLYのサンプルを正式E2Eに使わない。
- Java側: docs/spec/, docs/changes/, .claude/local/, 設定されたJUnit・Playwright証跡保存先、実際のソースとGit差分。
- sdd run は指定Gitルートをcwdにし、ツール側の読み込み用設定と外部Skills/Agentsを一時ディレクトリに生成する。ツール側のコマンドを絶対パスに変換し、ツールの位置と作業先を混同しない。既存プロジェクトの設定を置き換えない。
- sdd exec は同じプロファイルで人間の操作・テスト補助コマンドを実行する。docs/ai/v5-operations.mdの旧相対コマンド python3 scripts/NAME.py ... は sdd exec --profile NAME NAME.py ... に読み替える。
- SDD_PROJECT_ROOT、SDD_EXECUTION_CONTEXT、SDD_CHANGE_ID はランチャーから渡す。Claude Codeのセッション途中で上書きしない。

## 環境とプロファイル

local はホストのターミナル、devcontainer はコンテナ内のターミナルから起動する。execution は指定した実行場所のラベルで、実コンテナであることを認証する仕組みではない。Gitルートは起動するOSのファイルシステムで解決する。Dev ContainerではJDK・ビルドツール・ブラウザもコンテナ内に用意する。コンテナ内のlocalhostはホストのlocalhostとは限らない。接続先とネットワーク隔離は実環境で再確認する。

プロファイルを複数作成できる。異なるGitリポジトリの設定・承認・証跡は分かれている。同じ物理チェックアウトをホスト/コンテナで共有する場合はローカル状態も共有され得るので並行利用不可。実行環境または絶対パスの変更時は project-setup init --force を明示承認後に再実行し、G2/NET再承認と正式E2E再実行を行う。NET記録とG6対象E2Eは現在の設定ハッシュに照合する。

## 承認の実行例

```bash
sdd exec --profile my-app approval_gate.py record-g2 --change-id CHG-001
sdd exec --profile my-app approval_gate.py record-net --change-id CHG-001 --allowed-origin http://localhost:8080 --isolation-mode container-egress-policy
sdd exec --profile my-app approval_gate.py record-g6 --change-id CHG-001 --run-dir reports/e2e/CHG-001/RUN-ID
```

URL・隔離方式・保存先・RUN-IDは実際に承認した値に変更する。sdd はインストール先のsddの絶対パスまたはPATHに追加したコマンド。人が別ターミナルで実行し、Claudeが承認を作らない。電子署名・承認鍵は不要。Hookは補助ガードであってOSアクセス制御の代わりではない。

## ダッシュボード

Java側のパスを --repo に指定する。Dev Container上でのみ読めるファイルは同じコンテナでダッシュボードを実行し、必要ならVS Codeのポート転送を使用する。ホストから見える共有マウントがある場合も、ホスト側の実際のパスを指定する。承認表示はローカル記録の整合確認であって実行環境の独立証明ではない。
