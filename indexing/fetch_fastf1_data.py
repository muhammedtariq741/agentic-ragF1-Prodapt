import fastf1
import pandas as pd
import os

def fetch_season_data(year):
    print(f"Fetching schedule for {year}...")
    schedule = fastf1.get_event_schedule(year)
    # Filter out testing
    races = schedule[schedule['EventFormat'] != 'testing']
    
    # We only want completed races (date in the past)
    races = races[races['EventDate'] < '2026-04-22']
    
    rows = []
    
    for _, race in races.iterrows():
        round_num = race['RoundNumber']
        grand_prix = race['EventName']
        circuit = race['Location']  # Or try to get exact circuit name if available, fastf1 location is usually country/city
        date = race['EventDate'].strftime('%Y-%m-%d')
        
        print(f"  Fetching Round {round_num}: {grand_prix}...")
        try:
            session = fastf1.get_session(year, round_num, 'R')
            session.load(telemetry=False, weather=False, messages=False) # Only need results
            results = session.results
            
            for _, driver in results.iterrows():
                # Extract fastest lap time if available
                fl_time = ""
                if pd.notna(driver.get('FastestLapTime')):
                    fl_td = driver['FastestLapTime']
                    minutes = fl_td.components.minutes
                    seconds = fl_td.components.seconds
                    millis = fl_td.components.milliseconds
                    if minutes > 0:
                        fl_time = f"{minutes}:{seconds:02d}.{millis:03d}"
                    else:
                        fl_time = f"{seconds}.{millis:03d}"

                row = {
                    "season": year,
                    "round": round_num,
                    "grand_prix": grand_prix,
                    "circuit": race['Location'], # Fastf1 doesn't cleanly expose full circuit name, location is close enough for alias map
                    "date": date,
                    "driver": driver['FullName'],
                    "driver_code": driver['Abbreviation'],
                    "driver_nationality": driver.get('Country', ''), # FastF1 results might not have nationality directly, we'll see
                    "constructor": driver['TeamName'],
                    "grid_position": driver['GridPosition'],
                    "finish_position": driver['ClassifiedPosition'] if pd.notna(driver['ClassifiedPosition']) else 0,
                    "position_text": driver['Position'] if pd.notna(driver['Position']) else "R",
                    "points": driver['Points'],
                    "laps_completed": driver['Laps'],
                    "status": driver['Status'],
                    "fastest_lap_rank": "", # Not easily available without telemetry
                    "fastest_lap_time": fl_time
                }
                
                # Fix position_text logic
                if row["position_text"] == 0.0 or str(row["position_text"]).upper() == 'NAN':
                    row["position_text"] = "R"
                else:
                    try:
                        row["position_text"] = str(int(float(row["position_text"])))
                    except:
                        pass
                
                # Fix finish_position type
                try:
                    row["finish_position"] = int(float(row["finish_position"]))
                except:
                    row["finish_position"] = 20 # Fallback
                
                # Fix grid_position
                try:
                    row["grid_position"] = int(float(row["grid_position"]))
                except:
                    row["grid_position"] = 0
                
                rows.append(row)
        except Exception as e:
            print(f"    Failed to fetch {grand_prix}: {e}")
            
    return rows

if __name__ == "__main__":
    os.makedirs('/tmp/fastf1_cache', exist_ok=True)
    fastf1.Cache.enable_cache('/tmp/fastf1_cache')
    
    all_rows = []
    all_rows.extend(fetch_season_data(2024))
    all_rows.extend(fetch_season_data(2025))
    
    df = pd.DataFrame(all_rows)
    
    # Ensure directory exists
    os.makedirs('data/structured', exist_ok=True)
    
    # Save to CSV
    csv_path = 'data/structured/f1_results.csv'
    df.to_csv(csv_path, index=False)
    print(f"Successfully saved {len(df)} rows to {csv_path}")
