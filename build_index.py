import fitz  # PyMuPDF
import faiss
import json
import os
import re
import docx
from pathlib import Path
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from hashlib import md5
from search_and_respond import query_openai
from kazakh_translator import translate_kazakh_to_russian
from topic_utils import infer_topic

SUMMARY_CACHE_PATH = "faiss_index/summary_cache.json"
summary_cache = {}
if Path(SUMMARY_CACHE_PATH).exists():
    with open(SUMMARY_CACHE_PATH, "r", encoding="utf-8") as f:
        summary_cache = json.load(f)

# Настройки
CHUNK_SIZE = 1000
INDEX_PATH = "faiss_index/index.faiss"
META_PATH = "faiss_index/meta.json"
META_JSONL = "faiss_index/meta.jsonl"

os.makedirs("faiss_index", exist_ok=True)

model = SentenceTransformer("mixedbread-ai/mxbai-embed-large-v1")

if Path(INDEX_PATH).exists():
    index = faiss.read_index(INDEX_PATH)
else:
    index = faiss.IndexFlatL2(1024)

if Path(META_PATH).exists():
    with open(META_PATH, encoding="utf-8") as f:
        metadata = json.load(f)
else:
    metadata = []

already_indexed = set((m["source"], md5(m["text"].encode()).hexdigest()) for m in metadata)


def force_translate_to_russian(text: str) -> str:
    text = text.strip()
    if not text or len(text) < 30:
        return text

    try:
        translated = translate_kazakh_to_russian(text)

        return translated
    except Exception as e:
        print(f"⚠️ Ошибка при переводе: {e}")
        return text

def extract_text_from_pdf(path):
    try:
        doc = fitz.open(path)
        return "\n".join([page.get_text() for page in doc])
    except Exception as e:
        print(f"⚠️ Не удалось прочитать PDF {path}: {e}")
        return ""

def extract_text_from_file(file: Path) -> str:
    if file.suffix.lower() == ".pdf":
        return extract_text_from_pdf(file)
    elif file.suffix.lower() == ".docx":
        return extract_text_from_docx(file)
    else:
        print(f"⚠️ Неподдерживаемый формат: {file.name}")
        return ""

def chunk_text_with_overlap(text: str, size=1000, overlap=200):
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + size
        chunk = " ".join(words[start:end])
        chunks.append(chunk.strip())
        start += size - overlap
    return chunks

def infer_date(text):
    match = re.search(r"\d{1,2}\s+[а-яА-Я]+\s+20\d{2}", text)
    return match.group(0) if match else None

def extract_text_from_docx(path):
    try:
        doc = docx.Document(path)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        print(f"⚠️ Не удалось прочитать {path}: {e}")
        return ""

def summarize_text(text: str, filename: str) -> dict:
    if filename in summary_cache:
        return summary_cache[filename]

    try:
        system_prompt = (
            "Ты — официальный помощник депутата. Прочитай текст и верни JSON-объект с двумя ключами:\n"
            "1. `title` — короткий заголовок (до 100 символов), отражающий суть обращения;\n"
            "2. `summary` — краткое содержание (до 1700 символов) самого важного.\n"
            "Ответ строго в формате JSON, без пояснений."
        )
        response = query_openai(system_prompt, text)

        try:
            parsed = json.loads(response)
            summary_cache[filename] = parsed
            with open(SUMMARY_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(summary_cache, f, ensure_ascii=False, indent=2)
            return parsed
        except Exception as e:
            print(f"⚠️ Ошибка разбора JSON: {e}\nОтвет: {response}")
            return {"title": filename, "summary": text[:400] + "..."}

    except Exception as e:
        print(f"⚠️ Ошибка при генерации выжимки: {e}")
        return {"title": filename, "summary": text[:400] + "..."}

all_files = [f for f in Path("pdfs").iterdir() if f.suffix.lower() in [".pdf", ".docx"] and not f.name.startswith("~")]
for file in tqdm(all_files, desc="📄 Индексация документов"):
    try:
        full_text = extract_text_from_file(file)
        if not full_text.strip():
            print(f"⚠️ Пустой документ: {file.name}")
            continue

        translated_full_text = force_translate_to_russian(full_text) 
        summary = summarize_text(translated_full_text, file.name)
        print("📌 Заголовок:", summary["title"])

        chunks = chunk_text_with_overlap(full_text, size=CHUNK_SIZE, overlap=200)

        new_chunks = []
        for chunk in chunks:
            translated = force_translate_to_russian(chunk)
            chunk_hash = md5(translated.encode()).hexdigest()
            if (file.name, chunk_hash) not in already_indexed:
                new_chunks.append((translated, chunk_hash))

        if not new_chunks:
            continue

        embeddings = model.encode([chunk for chunk, _ in new_chunks])
        index.add(embeddings)

        for chunk, chunk_hash in new_chunks:
            item = {
                "source": file.name,
                "title": summary["title"],
                "document": file.stem,
                "text": chunk,
                "hash": chunk_hash,
                "context_text": summary["summary"],
                "chunk_length": len(chunk),
                "preview": chunk[:80].replace("\n", " ") + "...",
                "date": infer_date(chunk),
                "topic": infer_topic(chunk),
                "lang": "ru"
            }
            metadata.append(item)

    except Exception as e:
        print(f"❌ Ошибка при обработке {file.name}: {e}")

faiss.write_index(index, INDEX_PATH)

with open(META_PATH, "w", encoding="utf-8") as f:
    json.dump(metadata, f, ensure_ascii=False, indent=2)

with open(META_JSONL, "w", encoding="utf-8") as f:
    for entry in metadata:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

print("✅ Индексация завершена.")
