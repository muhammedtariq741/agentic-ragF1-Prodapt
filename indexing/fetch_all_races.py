import wikipedia
import os
import pandas as pd

def fetch_individual_race_pages():
    # Load the structured data to get the list of all races
    df = pd.read_csv('data/structured/f1_results.csv')
    
    # Get unique season and grand_prix combinations
    races = df[['season', 'grand_prix']].drop_duplicates()
    
    os.makedirs('data/documents', exist_ok=True)
    
    count = 0
    for _, row in races.iterrows():
        year = row['season']
        gp = row['grand_prix']
        
        # Wikipedia article titles are usually "2024 Bahrain Grand Prix"
        title = f"{year} {gp}"
        
        # We will format the filename as "2024_Bahrain_Grand_Prix.txt"
        filename = title.replace(" ", "_") + ".txt"
        output_path = os.path.join('data/documents', filename)
        
        print(f"Fetching '{title}'...")
        try:
            page = wikipedia.page(title, auto_suggest=False)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(page.content)
            count += 1
        except Exception as e:
            print(f"  Error fetching {title}: {e}")
            
    print(f"\nSuccessfully downloaded {count} individual race reports!")

if __name__ == "__main__":
    fetch_individual_race_pages()
