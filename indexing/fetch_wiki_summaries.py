import wikipedia
import os

def fetch_wiki(title, output_path):
    print(f"Fetching '{title}'...")
    try:
        page = wikipedia.page(title, auto_suggest=False)
        content = page.content
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Saved {len(content)} characters to {output_path}")
    except Exception as e:
        print(f"Error fetching {title}: {e}")

if __name__ == "__main__":
    fetch_wiki("2024 Formula One World Championship", "data/documents/2024_season_review.txt")
    fetch_wiki("2025 Formula One World Championship", "data/documents/2025_season_review.txt")
