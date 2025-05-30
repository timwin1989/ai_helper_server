import fitz
import json
import faiss
from pathlib import Path
from difflib import SequenceMatcher
from sentence_transformers import SentenceTransformer, CrossEncoder

import requests
from utils import save_html_to_pdf, save_to_docx
from openai import OpenAI
import os
from dotenv import load_dotenv
from topic_utils import infer_topic
load_dotenv()
SUMMARY_CACHE_PATH = "faiss_index/summary_cache.json"
# === Настройки ===
INDEX_PATH = "faiss_index/index.faiss"
META_PATH = "faiss_index/meta.json"
REQUESTS_DIR = Path("requests")
TOP_K = 4
MODEL_NAME = "mixedbread-ai/mxbai-embed-large-v1" # модель для поиска

client = OpenAI()

# Загрузка данных из файла в словарь
with open(SUMMARY_CACHE_PATH, "r", encoding="utf-8") as f:
    summary_cache = json.load(f)

# === Загрузка модели и индекса ===
def load_model():
    print(f"🔍 Загрузка модели эмбеддингов: {MODEL_NAME}")
    return SentenceTransformer(MODEL_NAME)
def load_reranker():
    print("🔍 Загрузка cross-encoder для rerank...")
    return CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

reranker = load_reranker()
model = load_model()
if Path(INDEX_PATH).exists():
    index = faiss.read_index(INDEX_PATH)
else:
    index = faiss.IndexFlatL2(1024)  # или 768, если другая модель

if Path(META_PATH).exists():
    with open(META_PATH, encoding="utf-8") as f:
        metadata = json.load(f)
else:
    print("⚠️ META файл не найден, создаётся пустой список.")
    metadata = []

# === Вспомогательные функции ===
def extract_text_from_pdf(path):
    doc = fitz.open(path)
    return "\n".join(page.get_text() for page in doc)

def extract_topic_from_query(query: str) -> str:
    # Простейший вызов на основе твоего indexer'а
    keywords = query.lower()
    return infer_topic(keywords)

def filter_similar_chunks(ranked_docs, threshold=0.85):
    filtered = []
    for doc in ranked_docs:
        if all(SequenceMatcher(None, doc["text"], prev["text"]).ratio() < threshold for prev in filtered):
            filtered.append(doc)
    return filtered

def search_by_title_summary(title_query: str, top_k=TOP_K):
    # Комбинируем заголовок и краткое содержание
    combined_texts = [
        f"{m.get('title', '')}. {m.get('summary', '')}".strip()
        for m in metadata
    ]

    # Эмбеддинг запроса и документов
    doc_vectors = model.encode(combined_texts)
    query_vector = model.encode([title_query])

    # Временный индекс и поиск
    temp_index = faiss.IndexFlatL2(doc_vectors.shape[1])
    temp_index.add(doc_vectors)
    distances, indices = temp_index.search(query_vector, top_k)

    # Собираем результаты
    retrieved = [metadata[i] for i in indices[0] if i < len(metadata)]
    return retrieved


def search(query, title: str = "", top_k=TOP_K):
    topic = extract_topic_from_query(query)
    topic_matched = [m for m in metadata if m.get("topic") == topic] or metadata  # fallback на всё

    # # Попытка 1: ищем только по title
    # if title:
    #     combined_texts = [m.get("title", "") for m in topic_matched]
    #     vectors = model.encode(combined_texts)
    #     query_vec = model.encode([title])
    #     temp_index = faiss.IndexFlatL2(vectors.shape[1])
    #     temp_index.add(vectors)
    #     distances, indices = temp_index.search(query_vec, top_k)

    #     retrieved = [topic_matched[i] for i in indices[0] if i < len(topic_matched)]
    #     reranked = rerank_results(query, retrieved)
    #     deduped = filter_similar_chunks(reranked)

    #     # Если ничего не нашли по заголовку — fallback на title + text
    #     if deduped:
    #         return deduped[:top_k]

    # Попытка 2: по title + text
    
    combined_texts = [f"{m.get('title', '')} {m.get('text', '')}".strip() for m in topic_matched]
    query_vec = model.encode([query])
    doc_vecs = model.encode(combined_texts)

    temp_index = faiss.IndexFlatL2(doc_vecs.shape[1])
    temp_index.add(doc_vecs)
    distances, indices = temp_index.search(query_vec, top_k * 2)

    retrieved = []
    for i in indices[0]:
        if i < len(topic_matched):
            doc = topic_matched[i]
            doc["__combined_text__"] = combined_texts[i]
            retrieved.append(doc)

    reranked = rerank_results(query, retrieved)
    deduped = filter_similar_chunks(reranked)

    return deduped[:top_k]

def rerank_results(query, retrieved):
    if not retrieved:
        return []

    pairs = [(query, doc["text"]) for doc in retrieved]
    scores = reranker.predict(pairs)
    scored_docs = list(zip(retrieved, scores))
    ranked = sorted(scored_docs, key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in ranked]

def query_ollama(prompt, model_ollama="mistral:instruct"):
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model_ollama, "prompt": prompt, "stream": False}
        )
        data = response.json()
        return data.get("response", "[Ошибка в генерации]")
    except Exception as e:
        return f"[Ошибка подключения к Ollama: {e}]"

def query_openai(system_prompt: str, prompt: str, temperature: int = 1, model_open_ai: str = "gpt-4o") -> str:
    try:
        response = client.chat.completions.create(
            model=model_open_ai,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
        )
        content = response.choices[0].message.content.strip()

        # 🔍 Удаляем обёртки ```json и ```
        if content.startswith("```json") or content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()

        # ✂️ Удаляем пояснение в начале, если оно есть
        for prefix in [
            "html",
        ]:
            if content.startswith(prefix):
                content = content[len(prefix):].strip()
                break  # только первую подходящую строку удаляем

        return content

    except Exception as e:
        return f"[Ошибка OpenAI: {e}]"

def generate_answer(request_text, similar_docs, system_prompt: str, lang: str = "Русский", use_openai=False):


    shortened_docs = []
    fragments_list = []
    for i, doc in enumerate(similar_docs):
        # Проверяем на основе text, чтобы он точно прошёл фильтр
        base_text = doc.get('text', '').strip().replace("\n", " ")
        if len(base_text) < 20:
            continue
        if len(base_text) > 2000:
            base_text = base_text[:2000] + "..."

        # Выводим всегда context_text, даже если фильтрация шла по text
        context = doc.get('context_text', '').strip().replace("\n", " ")
        shortened_docs.append(f"[Фрагмент {i+1} — источник: {doc['source']}]\n{context}")

        source = doc.get("source")
        summary_entry = summary_cache.get(source)

        title = summary_entry.get("title") if isinstance(summary_entry, dict) else None

        fragments_list.append({
            "title": title,
            "file_name": source,
            "context": context
        })

    if not shortened_docs:
        shortened_docs.append("[Нет подходящих фрагментов для информации.]")
 

    similar_text_block = "\n\n".join(shortened_docs)


    prompt = f"""
        Вот текст депутатского запроса:
        {request_text}

        Вот релевантные фрагменты из официальных источников:
        {similar_text_block}
    """

    print("\n📤 Промпт, отправленный в LLM:\n")
    print(prompt)

    if use_openai:
        return {
            "ai_answer": query_openai(system_prompt, prompt+f"""Ответ переведи на язык: {lang}"""),
            "fragments_list": fragments_list
        }
    else:
        return {
            "ai_answer": query_ollama(system_prompt, prompt+f"""Ответ переведи на язык: {lang}"""),
            "fragments_list": fragments_list
        }

# === Основной запуск ===
if __name__ == "__main__":
    request_files = list(REQUESTS_DIR.glob("*.pdf"))
    if not request_files:
        print("❌ В папке 'requests/' нет PDF-запросов.")
        exit(1)

    request_path = request_files[0]
    request_text = extract_text_from_pdf(str(request_path))
    print(f"\n📄 Обрабатывается запрос из файла: {request_path.name}")

    retrieved_docs = search(request_text, "Заголовок")
    similar_docs = [{"text": r["text"], "source": r.get("source", "неизвестный файл")} for r in retrieved_docs]

    if not similar_docs:
        print("❗ Ничего релевантного не найдено.")
        similar_docs = [{"text": "(нет данных)", "source": "N/A"}]

    final = generate_answer(request_text, similar_docs, "Русский", use_openai=True)

    print("\n✅ Сгенерированный ответ:\n")
    print(final)

    save_html_to_pdf(final, "generateAnswer.pdf")
