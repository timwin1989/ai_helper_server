from search_and_respond import query_openai


def is_probably_kazakh(text: str) -> bool:
    """
    Проверяет, содержит ли текст казахские специфические буквы.
    """
    kazakh_chars = "әғқңөұүһі"
    return any(char in text.lower() for char in kazakh_chars)

def translate_kazakh_to_russian(text: str) -> str:
    """
    Перевод текста с казахского на русский с помощью GPT (OpenAI API).
    Если текст не на казахском — возвращает его без изменений.
    """
    text = text.strip()
    if not text or len(text) < 5:
        return text

    if not is_probably_kazakh(text):
        print("⏭️ Текст не похож на казахский, перевод не требуется.")
        return text

    system_prompt = "Ты профессиональный переводчик. Переводи текст с казахского на русский язык точно и грамотно, без добавления лишней информации."
    prompt = f"Переведи следующий текст с казахского на русский:\n\n{text}"
    print("🔁 Перевод выполнен.")
    translated_text = query_openai(system_prompt, prompt, temperature=0)
    return translated_text


# from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
# import torch
# import re

# # Настройка устройства
# device = 'cuda' if torch.cuda.is_available() else 'cpu'

# # Загрузка модели и токенизатора один раз при импорте модуля
# model_name = 'deepvk/kazRush-kk-ru'
# model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device)
# tokenizer = AutoTokenizer.from_pretrained(model_name)

# def split_sentences(text: str) -> list[str]:
#     """
#     Простая разбивка текста на предложения.
#     """
#     # Заменяем ! ? на . и разбиваем по точке
#     text = re.sub(r'[!?]', '.', text)
#     return [s.strip() for s in text.split('.') if s.strip()]

# def translate_kazakh_to_russian(text: str) -> str:
#     """
#     Перевод текста с казахского на русский по предложениям (без nltk).
#     """
#     if not text or len(text.strip()) < 5:
#         return text

#     sentences = split_sentences(text)
#     translated_sentences = []

#     for sentence in sentences:
#         try:
#             inputs = tokenizer(sentence, return_tensors='pt', truncation=True, max_length=512).to(device)
#             with torch.no_grad():
#                 outputs = model.generate(
#                     **inputs,
#                     num_beams=5,
#                     max_new_tokens=128,
#                     no_repeat_ngram_size=3,
#                     early_stopping=True
#                 )
#             translated = tokenizer.decode(outputs[0], skip_special_tokens=True)
#             translated_sentences.append(translated)
#         except Exception as e:
#             print(f"⚠️ Ошибка в предложении:\n{sentence}\n{e}")
#             translated_sentences.append(sentence)

#     return " ".join(translated_sentences)

# if __name__ == "__main__":
#     sample_text = "Қазақстан — Шығыс Еуропа мен Орталық Азияда орналасқан мемлекет. Ол бай табиғи ресурстарға ие."
#     translated = translate_kazakh_to_russian(sample_text)
#     print("➡ Перевод:", translated)
