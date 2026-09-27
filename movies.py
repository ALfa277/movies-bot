from pathlib import Path
import asyncio
import time
import os
import logging
import sqlite3
import requests
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

def H(value):
    return html.escape(str(value)) if value is not None else ""

TOKEN_FILE = "token.txt"
DB_FILE = "movies.db"
TMDB_TOKEN_FILE = "tmdb_token.txt"

def get_tmdb_token():
    if not os.path.exists(TMDB_TOKEN_FILE):
        return ""
    return "".join(open(TMDB_TOKEN_FILE, "r", encoding="utf-8").read().split())

TMDB_HEADERS = {
    "Authorization": f"Bearer {get_tmdb_token()}",
    "accept": "application/json",
}

def get_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    token = "".join(open(TOKEN_FILE, "r", encoding="utf-8").read().split())
    return token if token else None

def db():
    return sqlite3.connect(DB_FILE)

def init_entertainment_tables():
    conn = sqlite3.connect("movies.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            tmdb_id INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            title TEXT,
            poster TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, tmdb_id, media_type)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER NOT NULL,
            tmdb_id INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            title TEXT,
            poster TEXT,
            watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS video_files (
            tmdb_id INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            season_number INTEGER,
            episode_number INTEGER,
            file_id TEXT NOT NULL,
            PRIMARY KEY (tmdb_id, media_type, season_number, episode_number)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_admins (
            user_id INTEGER PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content_type TEXT NOT NULL,
            year INTEGER,
            rating REAL DEFAULT 0,
            country TEXT,
            genre TEXT,
            description TEXT,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_user(user):
    conn = db()
    conn.execute("""
        INSERT OR REPLACE INTO users
        (user_id, username, first_name)
        VALUES (?, ?, ?)
    """, (user.id, user.username or "", user.first_name or ""))
    conn.commit()
    conn.close()

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🎬 الأفلام", callback_data="movies"),
            InlineKeyboardButton("📺 المسلسلات", callback_data="series"),
        ],
        [
            InlineKeyboardButton("🍥 الأنمي", callback_data="anime"),
        ],
        [
            InlineKeyboardButton("🔥 الأكثر شعبية", callback_data="popular"),
            InlineKeyboardButton("🆕 جديد", callback_data="new"),
        ],
        [
            InlineKeyboardButton("⭐ الأعلى تقييماً", callback_data="top"),
            InlineKeyboardButton("❤️ قائمتي", callback_data="favorites"),
        ],
        [
            InlineKeyboardButton("🔎 بحث", callback_data="search"),
            InlineKeyboardButton("🕐 السجل", callback_data="history"),
            InlineKeyboardButton("👤 ملفي", callback_data="profile"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 الرئيسية", callback_data="home")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    text = "🎬 <b>عالم الترفيه</b>\n\nمرحباً بك 👋\nاختر القسم الذي تريد تصفحه من القائمة بالأسفل:"
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=main_menu())

BROWSE_CATEGORIES = {
    "movies":  {"title": "🎬 الأفلام",          "kind": "movie_list", "endpoint": "movie/popular"},
    "series":  {"title": "📺 المسلسلات",         "kind": "tv_list",    "endpoint": "tv/popular"},
    "anime":   {"title": "🍥 الأنمي",            "kind": "anime_list", "endpoint": "discover/tv"},
    "popular": {"title": "🔥 الأكثر شعبية",      "kind": "mixed_list", "endpoint": "trending/all/week"},
    "new":     {"title": "🆕 جديد",              "kind": "mixed_list", "endpoint": "trending/all/day"},
    "top":     {"title": "⭐ الأعلى تقييماً (أفلام)", "kind": "movie_list", "endpoint": "movie/top_rated"},
}

def is_anime_item(item):
    genres = item.get("genre_ids") or []
    language = item.get("original_language")
    countries = item.get("origin_country") or []
    return 16 in genres and (language == "ja" or "JP" in countries)

async def tmdb_browse(update, context, category, page=1):
    query = update.callback_query
    if query:
        await query.answer()
    info = BROWSE_CATEGORIES.get(category)
    if not info:
        return
    try:
        params = {"language": "ar-SA", "page": page}
        if info["kind"] == "anime_list":
            params.update({"with_genres": "16", "with_origin_country": "JP", "sort_by": "popularity.desc"})
        response = requests.get(f"https://themoviedb.org{info['endpoint']}", headers=TMDB_HEADERS, params=params, timeout=15)
        if response.status_code != 200:
            return
        data = response.json()
        results = data.get("results") or []
        total_pages = min(data.get("total_pages") or 1, 500)
        if info["kind"] == "tv_list":
            results = [r for r in results if not is_anime_item(r)]
        rows = []
        for item in results[:20]:
            media_type = item.get("media_type") or ("tv" if info["kind"] in ("tv_list", "anime_list") else "movie")
            if media_type not in ("movie", "tv"):
                continue
            title = item.get("title") or item.get("name") or "بدون اسم"
            rows.append((media_type, item.get("id"), title))
        text = f"{info['title']} - صفحة {page}\n\n👇 اختر من القائمة:"
        buttons = []
        for media_type, item_id, title in rows:
            icon = "🎬" if media_type == "movie" else "📺"
            buttons.append([InlineKeyboardButton(f"{icon} {title[:40]}", callback_data=f"tmdb_{media_type}_{item_id}")])
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"browse_{category}_{page - 1}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"browse_{category}_{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
        if query:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        logging.exception("TMDB browse error")

async def browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    category = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 1
    await tmdb_browse(update, context, category, page)

async def profile(update: Update):
    user = update.effective_user
    conn = db()
    favorites = conn.execute("SELECT COUNT(*) FROM favorites WHERE user_id = ?", (user.id,)).fetchone()[0]
    conn.close()
    text = f"<b>ملفك الشخصي</b>\n\n🆔 ID: <code>{user.id}</code>\n👤 الاسم: {user.first_name}\n❤️ المفضلة: {favorites}"
    await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=back_button())

async def handle_search_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_search"):
        return
    search_text = update.message.text.strip()
    context.user_data["waiting_search"] = False
    try:
        movie_res = requests.get("https://themoviedb.orgsearch/movie", headers=TMDB_HEADERS, params={"query": search_text, "language": "ar-SA", "include_adult": "false"}, timeout=15)
        tv_res = requests.get("https://themoviedb.orgsearch/tv", headers=TMDB_HEADERS, params={"query": search_text, "language": "ar-SA", "include_adult": "false"}, timeout=15)
        movies = movie_res.json().get("results", [])[:5]
        tvs = tv_res.json().get("results", [])[:5]
        if not movies and not tvs:
            await update.message.reply_text("❌ لم يتم العثور على نتائج.")
            return
        text = "🔎 <b>نتائج البحث:</b>"
        buttons = []
        for item in movies:
            title = item.get("title") or "فيلم"
            buttons.append([InlineKeyboardButton(f"🎬 {title[:35]}", callback_data=f"tmdb_movie_{item['id']}")])
        for item in tvs:
            title = item.get("name") or "مسلسل"
