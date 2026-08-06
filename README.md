# Novel Downloader v2

「小説家になろう」と「カクヨム」の作品を個人利用向けEPUBとして保存し、
本棚・更新話数・取得済み本文を端末間で同期するStreamlitアプリです。

## 主な機能

- 表紙付きのレスポンシブ本棚（PCは2列、スマートフォンは画面幅に追従）
- 小説家になろう・カクヨムの作品取得
- 章ごとのEPUBとZIP生成
- 全作品の更新確認
- 更新のある作品を本棚カードへ表示
- 取得済み本文のSupabaseキャッシュ
- 新着話だけ取得し、保存済み本文と結合した完全版EPUBの再生成
- `403` / `429` / 一時的なサーバー障害を区別する通信制御

## セットアップ

### 1. Python環境

Python 3.11以降を推奨します。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. Supabase

初回セットアップは [SUPABASE_SETUP.md](SUPABASE_SETUP.md) を参照してください。
既存環境をv2へ更新する場合は、SupabaseのSQL Editorで
`SUPABASE_CHAPTER_CACHE_SETUP.sql` を一度だけ実行します。

### 3. Streamlit Secrets

ローカルでは `.streamlit/secrets.toml`、Streamlit Community Cloudでは
アプリ設定のSecretsへ次を登録します。

```toml
SUPABASE_URL="https://xxxxx.supabase.co"
SUPABASE_SECRET_KEY="sb_secret_xxxxx"
COOKIE_PASSWORD="十分に長いランダム文字列"
```

`SUPABASE_SECRET_KEY` をGitHub、`.env.local`、画面側のコードへ保存しないでください。

### 4. 起動

```powershell
streamlit run app.py
```

## 更新方法

- **最新版（新着話のみ取得）**: 保存済み本文を使い、新着話だけ取得して完全版を生成します。
- **全話を再取得**: キャッシュを使わず、現在公開されている全話を取得し直します。

本文キャッシュ導入前に登録した作品は、最初の一回だけ全話取得が必要です。

## 通信方針

アクセス先へ負荷を掛けないよう、同じホストへのアクセス間隔をアプリ全体で共有し、
`Retry-After` と段階的な再試行に従います。アクセス制限を受けた場合は処理を停止します。

必要に応じて次の環境変数で待機時間を長くできます。

```text
NOVEL_REQUEST_INTERVAL=2.5
NOVEL_REQUEST_JITTER=0.75
```

## 自動テスト

実サイトへアクセスせず、URL判定・キャッシュ差分・通信エラー・EPUB構造を検査します。

```powershell
python -m unittest discover -s tests -v
```

## 注意

- 取得した作品は個人利用の範囲で扱ってください。
- サイトの利用規約、robots設定、公開状態を尊重してください。
- サイト側の仕様変更により解析処理の更新が必要になる場合があります。
