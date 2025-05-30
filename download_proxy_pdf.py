import os
import requests
from urllib.parse import urlparse, unquote
from pathlib import Path
import time

ITEMS_PER_PAGE = 12
TOTAL_PAGES = 100
OUTPUT_DIR = Path(__file__).resolve().parent / "pdfs"
API_URL_TEMPLATE = (
    "https://mazhilis.parlam.kz/api/core/deputy-requests"
    "?limit={limit}&offset={offset}&q=&answer=null"
    "&convocation_id=null&deputy_id=null&request_date=null&session_id=null"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PDFDownloader/1.0)"
}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_page(page: int):
    offset = (page - 1) * ITEMS_PER_PAGE
    url = API_URL_TEMPLATE.format(limit=ITEMS_PER_PAGE, offset=offset)
    response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
    response.raise_for_status()
    return response.json().get("result", [])


def download_pdf(url: str, file_path: Path):
    # Преобразуем ссылку, если она была перекодирована
    decoded_url = unquote(url)

    # Убираем лишние редиректы и включаем стриминг
    response = requests.get(decoded_url, headers=HEADERS, stream=True, allow_redirects=True, timeout=10, verify=False)

    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code} при скачивании {decoded_url}")

    with open(file_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)


def main():
    for page in range(1, TOTAL_PAGES + 1):
        try:
            print(f"\n🔄 Страница {page}...")
            items = fetch_page(page)

            for item in items:
                document_url = item.get("document_url")
                item_id = item.get("id")

                if not document_url or not item_id:
                    print("⚠️ Пропущен item без document_url или id")
                    continue

                parsed_url = urlparse(document_url)
                ext = os.path.splitext(parsed_url.path)[1] or ".pdf"
                file_name = f"{item_id}{ext}"
                file_path = OUTPUT_DIR / file_name

                if file_path.exists():
                    print(f"✔️ Уже есть: {file_name}")
                    continue

                print(f"⬇️ Скачиваем: {file_name}")
                try:
                    download_pdf(document_url, file_path)
                    print(f"✅ Скачан: {file_name}")
                except Exception as e:
                    print(f"❌ Ошибка при скачивании {document_url}: {e}")
            time.sleep(0.5)  # ⏱️ пауза 500 мс между страницами
        except Exception as e:
            print(f"❌ Ошибка на странице {page}: {e}")

    print("\n🎉 Все страницы обработаны.")


if __name__ == "__main__":
    main()
