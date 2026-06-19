# Supabase セットアップガイド

git add .
git commit -m "update"
git push origin main

## 1. Supabase でプロジェクトを作成

1. [https://app.supabase.com](https://app.supabase.com) にアクセス
2. GitHub アカウントで新規登録またはログイン
3. **「New project」** をクリック
4. プロジェクト名を入力（例：`novel-downloader`）
5. 強力なデータベースパスワードを設定
6. リージョンを選択（日本なら `Tokyo (ap-northeast-1)`）
7. **「Create new project」** をクリック

## 2. テーブルを作成

### SQL エディタでテーブルを作成

1. Supabase ダッシュボードの左側から **「SQL Editor」** をクリック
2. **「New query」** をクリック
3. 下記の SQL を全てコピー&ペーストして実行

```sql
-- ユーザーテーブル
CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 小説テーブル
CREATE TABLE IF NOT EXISTS novels (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  url VARCHAR(500) NOT NULL,
  title VARCHAR(255) NOT NULL,
  cover_image TEXT,
  latest_chapter INT DEFAULT 0,
  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (email) REFERENCES users(email),
  UNIQUE(email, url)
);

-- ダウンロード履歴テーブル
CREATE TABLE IF NOT EXISTS downloads (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  novel_id BIGINT NOT NULL,
  chapters INT DEFAULT 0,
  file_path VARCHAR(500),
  downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (email) REFERENCES users(email),
  FOREIGN KEY (novel_id) REFERENCES novels(id)
);
```

## 3. API キーを取得

1. Supabase ダッシュボードの左側から **「Project Settings」** → **「API」** をクリック
2. 以下の情報をコピー：
   - **Project URL**
   - **Anon key** (公開 API キー)

## 4. 環境変数を設定

### ローカル環境用 (.env.local)

プロジェクトルートに `.env.local` ファイルを作成：

```
SUPABASE_URL=https://pbtawxjpkrystmchmmjo.supabase.co
SUPABASE_KEY=sb_publishable_N7iVoEgiSYA3KMkXSJjxnw_mPNW0Okt
```

### Streamlit Cloud にデプロイする場合

1. GitHub にプッシュ
2. [Streamlit Community Cloud](https://share.streamlit.io/) にアクセス
3. リポジトリを選択してデプロイ
4. デプロイ後、**「Manage app」** → **「Secrets」** をクリック
5. 下記を追加：

```
SUPABASE_URL="https://xxxxx.supabase.co"
SUPABASE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

## 5. アプリをテスト

1. ローカルで実行：
```bash
streamlit run app.py
```

2. 新規登録でメールアドレスを入力
3. 小説 URL を登録
4. ダウンロード機能をテター

## トラブルシューティング

### エラー: "SUPABASE_URL and SUPABASE_KEY are required"

→ 環境変数が設定されていません。上記の手順 4 を確認してください。

### テーブルが作成できない

→ SQL Editor で各クエリを個別に実行してください。

### 表紙画像が保存されない

→ メモリ制限の可能性があります。大きい画像は圧縮してください。

---

**設定完了後、app.py を再度デプロイしてください！**
