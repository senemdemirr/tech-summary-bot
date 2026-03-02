import os
import re
from pathlib import Path
from openai import OpenAI
import unicodedata
import hashlib

TXT_ROOT = Path("txtler")     
HTML_ROOT = Path("htmller")   

EDITOR_PROMPT = os.environ["EDITOR_PROMPT"]

MODEL = "gpt-4o"
TEMPERATURE = 0.4
MAX_TOKENS = 2500

key = os.environ["OPENAI_API_KEY"]
if not key:
    raise ValueError("OPENAI_API_KEY bulunamadı..")

client = OpenAI(api_key=key)

def normalize_text(text):
    text = text.lower()
    
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("utf-8")
    
    return text

def contains_error(text):
    normalized = normalize_text(text)
    return bool(re.search(r"icerik islen(e)?medi", normalized))

def remove_emoji(text: str) -> str:
    emoji_pattern = re.compile(
        "[" "\U00010000-\U0010ffff" "]",
        flags=re.UNICODE
    )
    return emoji_pattern.sub(r"", text)

def slugify(text: str, max_length: int = 60) -> str:
    text = text.lower()

    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")

    text = re.sub(r"[^a-z0-9]+", "-", text)

    text = text.strip("-")
    text = text[:max_length].rstrip("-")

    short_hash = hashlib.md5(text.encode()).hexdigest()[:6]

    return f"{text}-{short_hash}"
    
def extract_title_from_html(html: str) -> str:
    match = re.search(r"<h1>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return "icerik"

def should_ignore_dir(dir_name: str) -> bool:
    return dir_name.startswith("_")

def txt_to_html_via_gpt(user_text: str) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": EDITOR_PROMPT},
            {"role": "user", "content": user_text}
        ],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS
    )
    html_output = response.choices[0].message.content
    return remove_emoji(html_output)

def process_all_txts():
    if not TXT_ROOT.exists():
        raise FileNotFoundError(f"TXT_ROOT bulunamadı: {TXT_ROOT.resolve()}")

    HTML_ROOT.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped = 0

    for path in TXT_ROOT.rglob("*.txt"):

        rel = path.relative_to(TXT_ROOT) 

        if any(should_ignore_dir(part) for part in rel.parts[:-1]): 
            skipped += 1
            continue

        user_text = path.read_text(encoding="utf-8", errors="replace")

        html_output = txt_to_html_via_gpt(user_text)

        title = extract_title_from_html(html_output)
        data_check = contains_error(title)
        if(not data_check):
            slug = slugify(title)
    
            out_dir = HTML_ROOT / rel.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            
            out_path = out_dir / f"{slug}.html"
    
            
            out_path.parent.mkdir(parents=True, exist_ok=True)
    
            out_path.write_text(html_output, encoding="utf-8")
    
            processed += 1
            print(f"✅ {path}  ->  {out_path}")
        else:
            print("veri anlamsız olduğu için işlenemedi")

    print("\n==== ÖZET ====")
    print(f"İşlenen dosya: {processed}")
    print(f"Atlanan ( _ klasörleri ): {skipped}")
    print(f"Çıktı klasörü: {HTML_ROOT.resolve()}")

process_all_txts()
