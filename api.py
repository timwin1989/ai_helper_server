from fastapi import FastAPI, Form, UploadFile, File, Form
from fastapi.responses import JSONResponse
from typing import Optional
from pydantic import BaseModel
import requests as httpx
import tempfile
from pathlib import Path
from docx import Document  # Для .docx
import uuid
import re
import subprocess
from search_and_respond import extract_text_from_pdf, query_openai, search_by_title_summary, generate_answer
from utils import save_html_to_pdf, save_to_docx
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from uuid import uuid4
from bs4 import BeautifulSoup
from kazakh_translator import translate_kazakh_to_russian

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # или ["*"] для всех
    allow_credentials=True,
    allow_methods=["*"],  # или ['POST']
    allow_headers=["*"],
)
# Раздача PDF-файлов из папки "answers"
app.mount("/answers", StaticFiles(directory="answers"), name="answers")
app.mount("/pdfs", StaticFiles(directory="pdfs"), name="pdfs")


# def force_translate_to_russian(text: str) -> str:
#     paragraphs = [p for p in text.split("\n") if len(p.strip()) > 5]
#     translated_paragraphs = []
#     for paragraph in paragraphs:
#         try:
#             translated =  translate_kazakh_to_russian(paragraph)
#             translated_paragraphs.append(translated)
#         except Exception as e:
#             print(f"⚠️ Ошибка при переводе абзаца: {e}")
#             translated_paragraphs.append(paragraph)
#     return "\n\n".join(translated_paragraphs)

    
def extract_source_and_clean_text(text: str):
    text = text.lstrip()

    match = re.search(r'\[Источник:\s*"?([^"\]]+)"?\]', text)
    if match:
        source = match.group(1).strip()
        cleaned_text = text[:match.start()] + text[match.end():]
        if source.lower() == "без источника":
            return None, cleaned_text
        return source, cleaned_text

    return None, text

def clean_html_code_block(text: str) -> str:
    # Удаляем блоки с ```html ... ```
    text = re.sub(r"```html\s*([\s\S]*?)\s*```", r"\1", text, flags=re.IGNORECASE)
    # Удаляем просто ``` ... ```
    text = re.sub(r"```\s*([\s\S]*?)\s*```", r"\1", text)
    return text.strip()

# 🧾 JSON-схема
class SummarizeRequestParams(BaseModel):
    url: str
    lang: str
    
    
class UrlRequest(BaseModel):
    url: str
    short_context: str
    lang: str

# === API endpoint для резюмирования запроса по URL ===
@app.post("/summarize-request")
async def summarize_request(data: SummarizeRequestParams):
    file_url = data.url
    lang = data.lang
    system_promps = f"""Ты — официальный помощник. Сделай краткое, деловое резюме запроса.
        Ответ переведи на: '{lang}'.
        Сформируй результат в виде HTML с корректной разметкой: 
        используй <p> для абзацев, сохраняй логические отступы и структуру текста.
        Важно не добавляй пояснений не в начале не в конце ни каких! типа: ```html
    """

    try:
        response = httpx.get(file_url, verify=False)
        if response.status_code != 200:
            return {"error": "Не удалось скачать PDF"}

        # Сохраняем PDF временно
        uid = uuid.uuid4().hex
        temp_path = Path(f"temp/request_{uid}.pdf")
        temp_path.parent.mkdir(exist_ok=True)
        with open(temp_path, "wb") as f:
            f.write(response.content)

        request_text = extract_text_from_pdf(str(temp_path))
        if not request_text.strip():
            return JSONResponse(
                content={"error": "PDF-файл не содержит текст. Возможно, он состоит только из изображений."},
                status_code=400
            )
        summary = query_openai(system_promps, request_text+f"""Ответ переведи на язык: {lang}""")
        # if USE_OLLAMA:
        #     summary = summarize_with_ollama(translated_text)
        # else:
        #     summary = summarize_with_openai(translated_text, lang)

        return {"summary": clean_html_code_block(summary)}
    except Exception as e:
        return {"error": str(e)}

def extract_doc_text(doc_path: str) -> str:
    result = subprocess.run(["antiword", doc_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        return result.stdout.decode("utf-8")
    else:
        raise RuntimeError(f"Ошибка при извлечении текста: {result.stderr.decode()}")
    
@app.post("/generate-request/")
async def generate_by_upload(
    file: Optional[UploadFile] = File(None),
    short_context: str = Form(...),
    lang: str = Form(...),
    content_text: Optional[str] = Form(None)
):
    try:
        uid = uuid.uuid4().hex
        request_text = ""

        # === 1. Обработка файла, если он есть ===
        if file:
            ext = file.filename.split('.')[-1].lower()
            temp_dir = Path("requests")
            temp_dir.mkdir(exist_ok=True)
            temp_path = temp_dir / f"request_{uid}.{ext}"

            with open(temp_path, "wb") as f:
                f.write(await file.read())

            if ext == "pdf":
                request_text = extract_text_from_pdf(str(temp_path))
                if not request_text.strip():
                    return JSONResponse(
                        content={"error": "PDF-файл не содержит текст. Возможно, он состоит только из изображений."},
                        status_code=400
                    )
            elif ext == "docx":
                doc = Document(str(temp_path))
                request_text = "\n".join([para.text for para in doc.paragraphs])
                if not request_text.strip():
                    return JSONResponse(
                        content={"error": "docx-файл не содержит текст. Возможно, он состоит только из изображений."},
                        status_code=400
                    )
            elif ext == "doc":
                request_text = extract_doc_text(str(temp_path))
                if not request_text.strip():
                    return JSONResponse(
                        content={"error": "doc-файл не содержит текст. Возможно, он состоит только из изображений."},
                        status_code=400
                    )
            else:
                return JSONResponse(content={"error": f"Неподдерживаемый формат: .{ext}"}, status_code=400)

        # === 2. Если файла нет, берём content_text ===
        elif content_text:
            request_text = content_text
        else:
            return JSONResponse(
                content={"error": "Необходимо прикрепить файл или передать текст"},
                status_code=400
            )

        # === Перевод текста ===
        translated_request_text = translate_kazakh_to_russian(request_text)

        # === Поиск и генерация ===
        results = search_by_title_summary(short_context)
        similar_docs = [
            {
                "text": r["text"],
                "context_text": r.get("context_text", r["text"]),
                "source": r.get("source", "неизвестный файл")
            }
            for r in results
        ]

        system_prompt = f"""Ты — официальный помощник депутата Мажилиса Парламента Республики Казахстан.
        Твоя задача — помочь сформулировать официальный депутатский запрос на основании предоставленных данных.

        Используй деловой, официальный стиль. Структурируй текст логично: введение, суть обращения, конкретные вопросы или предложения.

        Если указаны фрагменты официальных ответов (например, из предыдущих запросов), используй их как обоснование или ссылку. В начале результата укажи источник: 
        если использован фрагмент, напиши [Источник: "имя_файла.pdf"], если нет — напиши [Источник: "без источника"].

        Ответ должен быть на языке: {lang}.

        Сформируй результат в виде HTML с корректной разметкой:
        — используй <p> для абзацев;
        — не добавляй никаких пояснений;
        — не пиши ничего кроме самого HTML-контента;
        — сохраняй официальную структуру текста: обращение, суть проблемы, обоснование, формулировка запроса.
        """
        # print(translated_request_text)
        
        # просмотреть содержимое индекса
        # with open("faiss_index/meta.json", "r", encoding="utf-8") as f:
        #     meta = json.load(f)

        # for i, item in enumerate(meta[:10]):  # покажем первые 10
        #     print(f"🔹 [{i}] context_text: {item.get('context_text', '')[:100]}")

        ai_response = generate_answer(translated_request_text, similar_docs, system_prompt, lang, use_openai=True)

        # === Сохраняем PDF ===
        Path("answers").mkdir(exist_ok=True)
        pdf_file = f"{uid}_answer.pdf"
        source, cleaned = extract_source_and_clean_text(ai_response["ai_answer"])
        save_html_to_pdf(cleaned, pdf_file)

        return {
            "fragments_list": ai_response["fragments_list"],
            "text": clean_html_code_block(cleaned),
            "pdf_url": f"/answers/{pdf_file}",
            "file_contenxt_source": source
        }

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# @app.post("/generate-by-url/")
# async def generate_by_url(payload: UrlRequest):
#     file_url = payload.url
#     short_context = payload.short_context
#     lang = payload.lang 

#     print("short_context: "+short_context)
#     try:
#         # Загружаем PDF
#         response = httpx.get(file_url, verify=False)
#         if response.status_code != 200:
#             return {"error": "Не удалось скачать PDF"}

#         # Уникальное имя
#         uid = uuid.uuid4().hex
#         request_path = Path(f"requests/request_{uid}.pdf")
#         request_path.parent.mkdir(exist_ok=True)

#         with open(request_path, "wb") as f:
#             f.write(response.content)

#         # Извлекаем текст и ищем похожие
#         request_text = extract_text_from_pdf(str(request_path))
#         translated_request_text = translate_kazakh_to_russian(short_context+": " + request_text)
 
#         # Находим похожие по переводу
#         results = search(translated_request_text, short_context)
#         similar_docs = [{"text": r["text"], "source": r.get("source", "неизвестный файл")} for r in results]

#         system_prompt = f"""Ты — официальный помощник Правительства Республики Казахстан.
#             Отвечай строго, официальным деловым языком. Ответ переведи на язык: {lang}.
#             чтобы составить официальный ответ от имени, к которому обращаются в запросе.
#             Сформируй точный, структурированный официальный ответ. Используй информацию из релевантных фрагментов.
#             Сформируй результат в виде HTML с корректной разметкой: 
#             используй <p> для абзацев, сохраняй логические отступы и структуру текста.
#             Не добавляй пояснений. Не добавляй ничего кроме HTML-содержимого ответа.
#             В начале ответа укажи источник релевантного фрагмента если какой то использовал, если не использовал то пиши что без источника, пример: если есть: [Источник: "8863_10026.pdf"] если нет: [Источник: "без источника"]
#         """
#         # Генерируем ответ
#         ai_response = generate_answer(translated_request_text, similar_docs, system_prompt, lang, use_openai=True)

#         # Сохраняем
#         Path("answers").mkdir(exist_ok=True)
#         pdf_file = f"{uid}_answer.pdf"
#         #print("DEBUG: ai_answer type =", type(ai_response["ai_answer"]))
#         source, cleaned = extract_source_and_clean_text(ai_response["ai_answer"])
#         save_html_to_pdf(cleaned, pdf_file)

#         return {
#             "fragments_list": ai_response["fragments_list"],
#             "text": clean_html_code_block(cleaned),
#             "pdf_url": f"/answers/{pdf_file}",
#             "file_contenxt_source": source
#         }

#     except Exception as e:
#         return {"error": str(e)}
    
# 📄 HTML → PDF
@app.post("/generate-pdf-from-html/")
async def generate_pdf_from_html(html: str = Form(...)):
    filename = f"{uuid4().hex}_from_html.pdf"
    save_html_to_pdf(html, filename)
    return {
        "message": "✅ PDF создан из HTML",
        "pdf_url": f"/answers/{filename}"
    }
@app.post("/generate-docx-from-html/")
async def generate_docx_from_html(html: str = Form(...)):
    filename = f"{uuid4().hex}_from_html.docx"
    save_to_docx(html, filename)
    return {
        "message": "✅ DOCX создан из HTML",
        "docx_url": f"/answers/{filename}"
    }

@app.post("/translate-html/")
async def translate_html(html: str = Form(...), target_lang: str = Form(...)):
    try:
        # Разбор HTML
        soup = BeautifulSoup(html, "html.parser")
        # Сформируй результат в виде HTML с корректной разметкой, 
        # используй <p> для абзацев, сохраняй логические отступы и структуру текста.

        # Перевод каждого текстового элемента
        for tag in soup.find_all(string=True):
            original_text = tag.strip()
            if original_text:
                system_promps = "Ты профессиональный переводчик"
                prompt = f"""
                    Переведи следующий текст на {target_lang}.

                    Сохрани стиль, но не добавляй пояснений, только перевод
                    {original_text}
                """
                translated = query_openai(system_promps, prompt)
                tag.replace_with(translated)

        return {
            "message": f"✅ Перевод выполнен на {target_lang}",
            "translated_html": clean_html_code_block(str(soup))
        }

    except Exception as e:
        return {"error": str(e)}