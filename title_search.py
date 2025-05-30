import json
import faiss
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

META_PATH = "faiss_index/meta.json"
INDEX_PATH = "faiss_index/title_index.faiss"
TITLES_PATH = "faiss_index/title_list.json"
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# === Загрузка модели и заголовков ===
model = SentenceTransformer(EMBEDDING_MODEL)

def load_titles_and_sources():
    with open(META_PATH, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    titles = [doc["title"] for doc in metadata]
    sources = [doc["source"] for doc in metadata]
    return titles, sources

# === Создание или загрузка индекса ===
def build_or_load_index():
    titles, sources = load_titles_and_sources()

    if Path(INDEX_PATH).exists() and Path(TITLES_PATH).exists():
        index = faiss.read_index(INDEX_PATH)
        with open(TITLES_PATH, "r", encoding="utf-8") as f:
            loaded_titles = json.load(f)
        return index, loaded_titles, sources

    print("🔧 Индекс не найден. Создаю заново...")
    vectors = model.encode(titles)
    index = faiss.IndexFlatL2(vectors.shape[1])
    index.add(np.array(vectors))

    Path("faiss_index").mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    with open(TITLES_PATH, "w", encoding="utf-8") as f:
        json.dump(titles, f, ensure_ascii=False, indent=2)

    return index, titles, sources

# === Поиск похожих заголовков ===
def search_similar_titles(query_title: str, top_k: int = 5):
    index, titles, sources = build_or_load_index()
    query_vector = model.encode([query_title])
    distances, indices = index.search(np.array(query_vector), top_k)

    results = []
    for i in indices[0]:
        results.append({
            "title": titles[i],
            "source": sources[i]
        })
    return results
