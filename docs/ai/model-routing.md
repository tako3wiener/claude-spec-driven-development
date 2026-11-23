# GPT-6 LunaとClaude Codeのモデル接続

Claude CodeとOpenAI APIはAPI形式が異なるため、OpenAI APIエンドポイントをそのまま `ANTHROPIC_BASE_URL` に設定しても通常は動作しません。**Anthropic Messages API互換のゲートウェイ**を使い、ゲートウェイ内で `gpt-6-luna` に変換してください。ゲートウェイはツール呼出、ストリーミング、コンテキスト、使用量、エラー変換、Subagent委譲を検証済みである必要があります。

## 概念例（シェル。具体値はゲートウェイ提供元に従う）
```sh
# ゲートウェイがAnthropic互換APIを実装している場合の概念例。
export ANTHROPIC_BASE_URL="https://YOUR-ANTHROPIC-COMPATIBLE-GATEWAY.example"
# 認証変数・トークンはゲートウェイのマニュアルに従って設定し、Gitには記録しない。
export ANTHROPIC_CUSTOM_MODEL_OPTION="gpt-6-luna"
claude --model gpt-6-luna
```

※ これは**接続が確認済みの場合にのみ**意味のある例。認証変数やAPI互換性はゲートウェイ実装ごとに異なります。

## 必須スモークテスト
- `/status` で意図したモデル選択を確認し、実際の送信先/下流モデルをゲートウェイのログで検証。
- 小さなJavaファイルを読ませて要約させる。
- 独立したsubagentを1回起動し、実際のモデル・委譲が動くことを確認。
- 許可された使い捨てブランチで小さなテストを生成/実行し、ツール結果が正しく解釈されることを確認。
- 不一致・ツール不具合があれば導入を停止し、ゲートウェイ側を修正。

公式参照:
- Claude Code model configuration: https://code.claude.com/docs/en/model-config
- Claude Code gateways: https://code.claude.com/docs/en/llm-gateway
- OpenAI GPT-6 Luna: https://developers.openai.com/api/docs/models/gpt-6-luna

## v5 (R19 条件付き)
モデル名や`claude --model`の出力だけでは、実際の下流モデルとツール呼び出し成功を証明できない。Gateway責任者が**実際のルーティングとツール使用可否**を記録し、外部署名したJSON受領書を用意できる場合は次で検証する。

```bash
python3 scripts/model_probe.py --receipt /path/to/operator-route.json \
  --signature /path/to/operator-route.sig --expected-model gpt-6-luna
```

JSONには`downstream_model`, `gateway_request_id`, `tool_use_verified`, 任意の`cost`オブジェクトを含める。受領書の署名公開鍵は`SDD_APPROVAL_PUBLIC_KEY`を参照し、秘密鍵はClaude Codeの権限外に配置する。ネットワーク疎通や費用上限を**自動設定する機能ではない**。Anthropicは非ClaudeモデルへのGateway経由利用をサポート対象として保証していないため、実検証に失敗した場合は未検証とする。
