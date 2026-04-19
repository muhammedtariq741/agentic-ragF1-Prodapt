"""Fetch F1 Race Results from Jolpica-F1 API (Ergast successor)."""
import csv
import os
import time
import requests

BASE_URL = "https://api.jolpi.ca/ergast/f1"
SEASONS = [2023, 2024]
OUTPUT_CSV = "data/structured/f1_results.csv"


def fetch_season_results(season: int) -> list[dict]:
    print(f"\nFetching {season} season...")
    all_races = []
    offset = 0
    limit = 100

    while True:
        url = f"{BASE_URL}/{season}/results.json?limit={limit}&offset={offset}"
        print(f"  GET {url}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        races = data["MRData"]["RaceTable"]["Races"]
        total = int(data["MRData"]["total"])
        all_races.extend(races)
        results_so_far = sum(len(r.get("Results", [])) for r in all_races)
        print(f"  Got {len(races)} races, {results_so_far} results so far (of {total})")
        if results_so_far >= total or len(races) == 0:
            break
        offset += limit
        time.sleep(0.5)

    rows = []
    for race in all_races:
        round_num = int(race["round"])
        grand_prix = race["raceName"]
        circuit = race.get("Circuit", {}).get("circuitName", "Unknown")
        date = race.get("date", "Unknown")
        for result in race.get("Results", []):
            driver_info = result.get("Driver", {})
            constructor = result.get("Constructor", {})
            fastest_lap = result.get("FastestLap", {})
            rows.append({
                "season": season, "round": round_num, "grand_prix": grand_prix,
                "circuit": circuit, "date": date,
                "driver": f"{driver_info.get('givenName', '')} {driver_info.get('familyName', '')}".strip(),
                "driver_code": driver_info.get("code", ""),
                "driver_nationality": driver_info.get("nationality", ""),
                "constructor": constructor.get("name", ""),
                "grid_position": int(result.get("grid", 0)),
                "finish_position": int(result.get("position", 0)),
                "position_text": result.get("positionText", ""),
                "points": float(result.get("points", 0)),
                "laps_completed": int(result.get("laps", 0)),
                "status": result.get("status", ""),
                "fastest_lap_rank": fastest_lap.get("rank", ""),
                "fastest_lap_time": fastest_lap.get("Time", {}).get("time", ""),
            })
    return rows


def main():
    print("F1 Data Fetcher - Jolpica API")
    all_rows = []
    for season in SEASONS:
        all_rows.extend(fetch_season_results(season))
        time.sleep(1)

    if all_rows:
        os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\nSaved {len(all_rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
