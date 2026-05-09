import os
from supabase import create_client, Client
from datetime import datetime
import base64
from io import BytesIO
from dotenv import load_dotenv

# .env.local を読み込む（ローカル実行用）
load_dotenv('.env.local')

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

def get_supabase_client():
    """Streamlit Cloudとローカル実行の両方に対応"""
    try:
        import streamlit as st
        url = st.secrets.get("SUPABASE_URL") or SUPABASE_URL
        key = st.secrets.get("SUPABASE_KEY") or SUPABASE_KEY
    except:
        url = SUPABASE_URL
        key = SUPABASE_KEY

    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables are required")

    return create_client(url, key)

# グローバルクライアント（ローカル実行用）
supabase: Client = get_supabase_client()


def create_tables():
    """テーブルを初期化（初回のみ）"""
    # ユーザーテーブル
    supabase.table("users").select("*").limit(1).execute()
    
    # 小説テーブル
    supabase.table("novels").select("*").limit(1).execute()
    
    # ダウンロード履歴テーブル
    supabase.table("downloads").select("*").limit(1).execute()


def register_user(email: str):
    """ユーザーを登録"""
    try:
        result = supabase.table("users").insert({
            "email": email,
            "created_at": datetime.now().isoformat()
        }).execute()
        return True, "登録完了"
    except Exception as e:
        if "duplicate" in str(e).lower():
            return False, "このメールアドレスは既に登録されています"
        return False, str(e)


def get_user_by_email(email: str):
    """メールアドレスからユーザーを取得"""
    try:
        result = supabase.table("users").select("*").eq("email", email).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        return None


def get_user_novels(email: str):
    """ユーザーの登録済み小説一覧を取得"""
    try:
        result = supabase.table("novels").select("*").eq("email", email).execute()
        return result.data if result.data else []
    except Exception as e:
        return []


def register_novel(email: str, url: str, title: str, cover_image=None):
    """小説を登録"""
    try:
        # 表紙画像をbase64に変換
        cover_data = None
        if cover_image:
            cover_data = base64.b64encode(cover_image.read()).decode('utf-8')
        
        result = supabase.table("novels").insert({
            "email": email,
            "url": url,
            "title": title,
            "cover_image": cover_data,
            "latest_chapter": 0,
            "registered_at": datetime.now().isoformat()
        }).execute()
        
        if result.data:
            return True, "小説を登録しました", result.data[0]["id"]
        return False, "登録に失敗しました"
    except Exception as e:
        if "duplicate" in str(e).lower():
            return False, "この小説は既に登録されています", None
        return False, str(e), None


def update_latest_chapter(novel_id: int, chapter_count: int):
    """最新の章数を更新"""
    try:
        result = supabase.table("novels").update({
            "latest_chapter": chapter_count,
            "updated_at": datetime.now().isoformat()
        }).eq("id", novel_id).execute()
        return True
    except Exception as e:
        return False


def record_download(email: str, novel_id: int, chapter_count: int, file_path: str):
    """ダウンロード履歴を記録"""
    try:
        result = supabase.table("downloads").insert({
            "email": email,
            "novel_id": novel_id,
            "chapters": chapter_count,
            "file_path": file_path,
            "downloaded_at": datetime.now().isoformat()
        }).execute()
        return True
    except Exception as e:
        return False


def get_download_history(email: str, novel_id: int):
    """小説のダウンロード履歴を取得"""
    try:
        result = supabase.table("downloads").select("*").eq("email", email).eq("novel_id", novel_id).order("downloaded_at", desc=True).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        return None


def delete_novel(novel_id: int):
    """小説を削除"""
    try:
        supabase.table("novels").delete().eq("id", novel_id).execute()
        return True
    except Exception as e:
        return False


def update_cover_image(novel_id: int, cover_image):
    """表紙画像を更新"""
    try:
        cover_data = base64.b64encode(cover_image.read()).decode('utf-8')
        supabase.table("novels").update({
            "cover_image": cover_data
        }).eq("id", novel_id).execute()
        return True
    except Exception as e:
        return False
