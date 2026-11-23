---
name: tool-setup
description: Javaの開発/E2Eツールを独立診断し、実依存版の解決とPlaywrightブラウザ取得を人の承認後に実行する。
disable-model-invocation: true
---

# v5 ツール診断 /tool-setup

1. `python3 scripts/project_setup.py check`で対象コードベースと環境を確認。
2. `python3 scripts/toolchain.py doctor --phase setup`、`--phase dev`を実行。**devではE2Eモジュールの存在を要求しない。**
3. `python3 scripts/toolchain.py doctor --phase e2e`でPlaywright Javaの依存解決記録とブラウザ取得記録を確認。
4. Maven親POM/Gradleカタログ等の実際の版を得るため、`toolchain.py resolve --approve-resolution`を使用。**実行前にネットワーク・Maven/Gradleプラグイン・build logic実行の影響を人に提示し承認を得る。** `doctor/plan`はネットワークへアクセスしない。
5. `python3 scripts/toolchain.py plan`で取得コマンド・ツール版・接続元を提示し、別途承認した場合のみ`python3 scripts/toolchain.py install --apply --approve-downloads`。OS全体/JDK/ビルドツールの自動更新は実施しない。
6. 再診断。ブラウザBOOTSTRAPPEDは起動検証ではないため、G4 E2Eで実起動確認する。失敗・未解決はBLOCKED。

詳細は`docs/ai/v5-operations.md`。取得コマンドのフラグは人の承認そのものではなく、署名G2やOS権限とは別物。
