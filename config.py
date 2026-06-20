from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMPORTS_DIR = DATA_DIR / "imports"
EXPORTS_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "trips.db"
STATION_CACHE_PATH = DATA_DIR / "stations.json"
GEOMETRY_CACHE_DIR = DATA_DIR / "geometries"
KNOWN_PLACES_PATH = DATA_DIR / "known_places.json"
HOME_PATH = DATA_DIR / "home.json"

EKIDATA_BASE = "https://ny-a.github.io/ekidata/api"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 駅マスタ取得対象（ekidata 都道府県コード 1=北海道 … 47=沖縄）
KANTO_PREFECTURE_CODES = [9, 10, 11, 12, 13, 14]
ALL_PREFECTURE_CODES = list(range(1, 48))
# 全国対応（既存キャッシュは不足分のみ追加取得）
PREFECTURE_CODES = ALL_PREFECTURE_CODES

SEGMENT_COLORS = [
    "#e63946", "#457b9d", "#2a9d8f", "#e9c46a",
    "#f4a261", "#264653", "#8338ec", "#fb5607",
]

# 閲覧専用（家族共有）: VIEW_ONLY=1 で POST/PUT/DELETE を拒否し / → /destinations へ
VIEW_ONLY = os.getenv("VIEW_ONLY", "").lower() in ("1", "true", "yes")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0" if VIEW_ONLY else "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "5050"))
