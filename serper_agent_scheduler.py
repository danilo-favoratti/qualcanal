#!/usr/bin/env python3

import os
import requests
import time
import json
import concurrent.futures
from datetime import datetime
import pytz
from agents import Agent, Runner, WebSearchTool
import http.client

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# --- Direct Serper API Functions ---

def search_serper_api(query, location="Brazil", gl="br", hl="pt-br", tbs="", engine="google"):
    """
    Performs a direct search query to the Google Serper API without going through the agent.
    """
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return {"error": "SERPER_API_KEY environment variable not set."}

    search_url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "location": location,
        "gl": gl,
        "hl": hl,
        "engine": engine,
    }

    if tbs != "":
        payload["tbs"] = tbs

    try:
        response = requests.post(search_url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Error during Serper API request: {e}"}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}


def search_for_team_calendar(team_name):
    """
    Search for the match calendar of a specific team and return the results.
    """
    query = f"calendario oficial de partidas do {team_name}"
    
    print(f"Searching calendar for: {team_name}\n")
    result = search_serper_api(query)
    
    # Check if the search was successful
    if "error" in result:
        print(f"Error searching calendar for {team_name}: {result['error']}")
        return {"team": team_name, "error": result["error"], "data": None}
        
    print(f"Found calendar results for {team_name}")
    return {"team": team_name, "error": None, "data": result}


def search_where_to_watch(team1, team2):
    """
    Search for where to watch the match between two teams.
    """
    query = f"onde assistir {team1} x {team2}"
    
    print(f"Searching where to watch: {team1} vs {team2}\n")
    result = search_serper_api(query, tbs="qdr:w")
    
    # Check if the search was successful
    if "error" in result:
        print(f"Error searching where to watch {team1} vs {team2}: {result['error']}")
        return {"team1": team1, "team2": team2, "error": result["error"], "data": None}
        
    print(f"Found viewing options for {team1} vs {team2}")
    return {"team1": team1, "team2": team2, "error": None, "data": result}


# --- Agent Setup ---

def setup_agent():
    """
    Setup and return an OpenAI Agent for processing the collected search results.
    """
    # Check for OpenAI API Key
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable not set.")
        exit(1)

    # Create an agent for processing search results
    agent = Agent(
        name="FootballMatchParser",
        instructions="""
        You are a specialized agent for processing search results about upcoming football matches in Brazil.
        Your task is to extract information about future matches for each team, identify where they can be watched,
        and format the results into a structured JSON.
        """,
        model="gpt-4o",
        #tools=[WebSearchTool()]
    )
    
    return agent


# Add this helper function after the setup_agent function
def extract_json_from_response(text):
    """
    Extract valid JSON from an LLM response that might contain Markdown formatting.
    
    This handles several cases:
    1. Response is already valid JSON
    2. Response contains JSON inside Markdown code blocks (```json ... ```)
    3. Response has explanatory text before/after the JSON
    
    Returns:
        dict or list: The parsed JSON data
        or None if no valid JSON could be extracted
    """
    if not text:
        return None
        
    # First try: assume the entire text is valid JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Not valid JSON, so try to extract it from Markdown code blocks or other text
        pass
    
    # Try to extract JSON from Markdown code blocks
    import re
    # Look for JSON within code blocks (with or without the json language tag)
    code_block_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    matches = re.findall(code_block_pattern, text)
    
    # Try each code block until we find valid JSON
    for potential_json in matches:
        try:
            return json.loads(potential_json)
        except json.JSONDecodeError:
            continue
    
    # If we didn't find JSON in code blocks, try to find the largest subset of the text that is valid JSON
    # Start by looking for text that starts with { or [ and ends with } or ]
    json_pattern = r"(\{[\s\S]*\}|\[[\s\S]*\])"
    matches = re.findall(json_pattern, text)
    
    # Try each potential JSON block, starting with the longest
    matches.sort(key=len, reverse=True)
    for potential_json in matches:
        try:
            return json.loads(potential_json)
        except json.JSONDecodeError:
            continue
    
    # No valid JSON found
    return None


# --- First Step: Find Next Matches ---

def scrape_url(url):
    """
    Scrape a URL via Serper scrape API.
    """
    conn = http.client.HTTPSConnection("scrape.serper.dev")
    payload = json.dumps({"url": url})
    headers = {
        "X-API-KEY": os.getenv("SERPER_API_KEY"),
        "Content-Type": "application/json"
    }
    conn.request("POST", "/", payload, headers)
    res = conn.getresponse()
    data = res.read()
    try:
        return json.loads(data.decode("utf-8"))
    except:
        return {}


def find_next_matches():
    """
    First step: For each team, search for their match calendar and
    extract the next upcoming match using an agent.
    Returns a dictionary of team names and their next opponent.
    """
    # Use fixed filenames
    calendar_results_file = 'calendar_results.json'
    next_matches_file = 'next_matches.json'
    
    print(f"Starting calendar search task at {time.strftime('%Y-%m-%d %H:%M:%S')}...")

    # Load teams data
    try:
        with open('teams.json', 'r', encoding='utf-8') as f:
            series_data = json.load(f)
    except FileNotFoundError:
        print("Error: teams.json not found.")
        return {}
    except json.JSONDecodeError:
        print("Error: Could not decode JSON from teams.json.")
        return {}

    # Extract all teams with their series and image
    teams_with_series = []
    for series in series_data:
        series_name = series.get('serie', 'Unknown')
        for team_obj in series.get('teams', []):
            if isinstance(team_obj, dict):
                teams_with_series.append({"team": team_obj, "serie": series_name})
            else:
                teams_with_series.append({"team": {"name": team_obj, "image": None}, "serie": series_name})

    if not teams_with_series:
        print("No teams found in teams.json.")
        return {}

    print(f"Found {len(teams_with_series)} teams. Starting parallel calendar searches...")

    # Setup the agent once
    agent = setup_agent()
    
    # Dictionary to store next matches for each team
    next_matches = {}
    # Store raw results for debugging
    all_calendar_results = []

    brasilia_tz = pytz.timezone('America/Sao_Paulo')
    current_datetime_brt = datetime.now(brasilia_tz).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- Perform parallel searches for calendars ---    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        # Submit all search tasks
        future_to_team = {
            executor.submit(search_for_team_calendar, team_info["team"]["name"]): team_info 
            for team_info in teams_with_series
        }
        
        # Process results as they complete
        for future in concurrent.futures.as_completed(future_to_team):
            team_info = future_to_team[future]
            team_name = team_info["team"]["name"]
            
            try:
                search_result = future.result()
                all_calendar_results.append(search_result)
                
                # Check if search failed
                if search_result.get("error"):
                    print(f"Skipping agent processing for {team_name} due to search error: {search_result['error']}")
                    continue

                # --- Agent Processing for calendar data --- 
                print(f"Processing calendar results for {team_name} with agent...")
                
                # Prepare the prompt for the agent
                organic = search_result["data"].get("organic", [])
                links = [item.get("link") for item in organic]
                # Scrape first 3 calendar URLs in parallel
                scraped_calendar_list = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as scrape_executor:
                    futures = {scrape_executor.submit(scrape_url, url): url for url in links[:3]}
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            scraped_calendar_list.append(future.result())
                        except:
                            continue

                calendar_prompt = f'''## TASK: EXTRACT NEXT MATCH FOR {team_name}

You have been given links for the team calendar of: {team_name}.
Your job is to:
1. Find the next upcoming match for {team_name} starting from today (Brasilia Time).
2. Get the team name of the opponent (do not use 3 letters, like NAU or FLU. Use the full team name). Also "Ida" or "Vida" or "Vidal" is not a team. Ignore them and search others.
3. Extract details for only that single next match: opponent team and date/time in ISO8601 format.
4. Format the result into a structured JSON.

## CURRENT DATETIME (Brasilia Time)
{current_datetime_brt}

## SEARCH RESULTS FOR {team_name} CALENDAR
```json
{json.dumps(scraped_calendar_list, ensure_ascii=False)}
```

## EXPECTED OUTPUT FORMAT
```json
{{
  "next_match": {{
    "opponent": "<Opponent Team Name>",
    "datetime_brt": "<ISO8601 DateTime in Brasilia TimeZone>"
  }}
}}
```

IMPORTANT INSTRUCTIONS:
- Only include the very next match after yesterday.
- Ensure datetime_brt is in ISO8601 format with Brasilia timezone (-03:00)
- If no future matches are found, return `{{"next_match": null}}`
- Only include the JSON object in your response, no additional text.
- Do not consider any result related to junior soccer or feminine soccer. Just masculine adult soccer.

Please provide the structured JSON with the next match for {team_name}:'''

                try:
                    # Run the agent with the calendar data
                    agent_result = Runner.run_sync(agent, calendar_prompt)
                    
                    if agent_result.final_output:
                        parsed_match_json = extract_json_from_response(agent_result.final_output)
                        
                        if parsed_match_json and isinstance(parsed_match_json, dict) and "next_match" in parsed_match_json:
                            if parsed_match_json["next_match"]:
                                opponent = parsed_match_json["next_match"].get("opponent")
                                datetime_brt = parsed_match_json["next_match"].get("datetime_brt")
                                
                                # Store the next match info
                                next_matches[team_name] = {
                                    "opponent": opponent,
                                    "datetime_brt": datetime_brt
                                }
                                print(f"Next match for {team_name}: vs {opponent} at {datetime_brt}")
                            else:
                                print(f"No upcoming matches found for {team_name}")
                                next_matches[team_name] = None
                        else:
                            print(f"Failed to extract valid JSON from agent response for {team_name}.")
                            with open(f"calendar_error_{team_name}.txt", 'w', encoding='utf-8') as f_err:
                                f_err.write(agent_result.final_output or "No agent output.")
                    else:
                        print(f"No output received from the agent for {team_name}.")
                        
                except Exception as agent_e:
                    print(f"Error during agent processing for {team_name}: {agent_e}")

            except Exception as search_e:
                print(f"Error processing calendar search for {team_name}: {search_e}")
    
    print(f"Completed all calendar searches and agent processing.")
    
    # Save the next matches results
    try:
        with open(next_matches_file, 'w', encoding='utf-8') as f:
            json.dump(next_matches, f, indent=2, ensure_ascii=False)
        print(f"Next matches data saved to {next_matches_file}")
    except Exception as e:
        print(f"Error saving next matches data: {e}")
        
    # Optional: Save raw calendar results
    try:
        with open(calendar_results_file, 'w', encoding='utf-8') as f:
            json.dump(all_calendar_results, f, indent=2, ensure_ascii=False)
        print(f"Raw calendar results saved to {calendar_results_file}")
    except Exception as e:
        print(f"Error saving raw calendar results: {e}")
        
    return next_matches


# --- Second Step: Find Where to Watch ---

def find_where_to_watch(next_matches):
    """
    Second step: For each team with a next match, search for where to watch the game
    and process the results with an agent.
    """
    if not next_matches:
        print("No next matches found. Skipping 'where to watch' search.")
        return {}
        
    # Use fixed filenames
    watch_results_file = 'watch_results.json'
    output_file = 'match_results.json'
    
    print(f"Starting 'where to watch' search task at {time.strftime('%Y-%m-%d %H:%M:%S')}...")

    # Load teams data for reference
    try:
        with open('teams.json', 'r', encoding='utf-8') as f:
            series_data = json.load(f)
    except FileNotFoundError:
        print("Error: teams.json not found.")
        return
    except json.JSONDecodeError:
        print("Error: Could not decode JSON from teams.json.")
        return

    # Create a lookup for teams with their series and image
    teams_lookup = {}
    for series in series_data:
        series_name = series.get('serie', 'Unknown')
        for team_obj in series.get('teams', []):
            if isinstance(team_obj, dict):
                teams_lookup[team_obj.get("name")] = {"image": team_obj.get("image"), "serie": series_name}
            else:
                teams_lookup[team_obj] = {"image": None, "serie": series_name}

    # Setup the agent
    agent = setup_agent()
    
    # Dictionary to store processed results, organized by series
    processed_data_by_series = {series.get('serie'): [] for series in series_data}
    # Store raw results for debugging
    all_watch_results = []

    brasilia_tz = pytz.timezone('America/Sao_Paulo')
    current_datetime_brt = datetime.now(brasilia_tz).strftime("%Y-%m-%dT%H:%M:%S%z")

    # Create a list of team pairs to search for
    team_pairs = []
    for team_name, match_info in next_matches.items():
        if match_info and match_info.get("opponent"):
            team_pairs.append({
                "team1": team_name,
                "team2": match_info.get("opponent"),
                "datetime_brt": match_info.get("datetime_brt")
            })

    print(f"Found {len(team_pairs)} team pairs to search for viewing options.")

    # --- Perform parallel searches for where to watch ---    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        # Submit all search tasks
        future_to_pair = {
            executor.submit(search_where_to_watch, pair["team1"], pair["team2"]): pair
            for pair in team_pairs
        }
        
        # Process results as they complete
        for future in concurrent.futures.as_completed(future_to_pair):
            pair_info = future_to_pair[future]
            team1 = pair_info["team1"]
            team2 = pair_info["team2"]
            datetime_brt = pair_info["datetime_brt"]
            series_name = teams_lookup.get(team1, {}).get("serie", "Unknown")
            
            try:
                search_result = future.result()
                all_watch_results.append(search_result)
                
                # Check if search failed
                if search_result.get("error"):
                    print(f"Skipping agent processing for {team1} vs {team2} due to search error: {search_result['error']}")
                    # Add placeholder with partial info
                    processed_data_by_series[series_name].append({
                        "name": team1,
                        "image": teams_lookup.get(team1, {}).get("image"),
                        "matches": [{
                            "adversary": team2,
                            "datetime_brt": datetime_brt,
                            "channels": []
                        }],
                        "error": f"Search failed: {search_result['error']}"
                    })
                    continue

                # --- Agent Processing for viewing options --- 
                print(f"Processing viewing options for {team1} vs {team2} with agent...")
                
                # Prepare the prompt for the agent
                organic = search_result["data"].get("organic", [])
                links = [item.get("link") for item in organic]
                # Scrape first 3 viewing URLs in parallel
                scraped_viewing_list = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as scrape_executor:
                    futures = {scrape_executor.submit(scrape_url, url): url for url in links[:3]}
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            scraped_viewing_list.append(future.result())
                        except:
                            continue

                watch_prompt = f'''## TASK: EXTRACT VIEWING OPTIONS FOR FOOTBALL MATCH

You have been given search results for viewing options for a match between:
- Team 1: {team1}
- Team 2: {team2}
- Date/Time (BRT): {datetime_brt}

Your job is to:
1. Parse these results to find all TV channels and streaming services where this match can be watched.
2. Format the result into a structured JSON.
3. Remove youtube channels. Remove GE channels.
4. Remove comments only channels.
5. Remove narration only channels.

## SEARCH RESULTS FOR VIEWING OPTIONS
```json
{json.dumps(scraped_viewing_list, ensure_ascii=False)}
```

## EXPECTED OUTPUT FORMAT
```json
{{
  "channels": [
    {{"name": "<Channel Name>", "url": "<Channel URL or null if not available>"}},
    {{"name": "<Channel Name>", "url": "<Channel URL or null if not available>"}}
  ]
}}
```

IMPORTANT INSTRUCTIONS:
- Include both TV channels and streaming services
- Remove any duplicate channels
- If no viewing options are found, return `{{"channels": []}}`
- For TV channels without URLs, use `null` for the URL field
- Only include the JSON object in your response, no additional text

Please provide the structured JSON with viewing options for {team1} vs {team2}:'''

                try:
                    # Run the agent with the viewing options data
                    agent_result = Runner.run_sync(agent, watch_prompt)
                    
                    if agent_result.final_output:
                        parsed_channels_json = extract_json_from_response(agent_result.final_output)
                        
                        if parsed_channels_json and isinstance(parsed_channels_json, dict) and "channels" in parsed_channels_json:
                            # Successfully parsed channels
                            match_output = {
                                "name": team1,
                                "image": teams_lookup.get(team1, {}).get("image"),
                                "matches": [{
                                    "adversary": team2,
                                    "datetime_brt": datetime_brt,
                                    "channels": parsed_channels_json["channels"]
                                }]
                            }
                            processed_data_by_series[series_name].append(match_output)
                            print(f"Successfully processed viewing options for {team1} vs {team2}")
                        else:
                            print(f"Failed to extract valid JSON from agent response for {team1} vs {team2}.")
                            # Add placeholder with partial info
                            processed_data_by_series[series_name].append({
                                "name": team1,
                                "image": teams_lookup.get(team1, {}).get("image"),
                                "matches": [{
                                    "adversary": team2,
                                    "datetime_brt": datetime_brt,
                                    "channels": []
                                }],
                                "error": "Agent failed to return valid channels JSON"
                            })
                            with open(f"watch_error_{team1}_vs_{team2}.txt", 'w', encoding='utf-8') as f_err:
                                f_err.write(agent_result.final_output or "No agent output.")
                    else:
                        print(f"No output received from the agent for {team1} vs {team2}.")
                        processed_data_by_series[series_name].append({
                            "name": team1,
                            "image": teams_lookup.get(team1, {}).get("image"),
                            "matches": [{
                                "adversary": team2,
                                "datetime_brt": datetime_brt,
                                "channels": []
                            }],
                            "error": "No agent output"
                        })
                        
                except Exception as agent_e:
                    print(f"Error during agent processing for {team1} vs {team2}: {agent_e}")
                    processed_data_by_series[series_name].append({
                        "name": team1,
                        "image": teams_lookup.get(team1, {}).get("image"),
                        "matches": [{
                            "adversary": team2,
                            "datetime_brt": datetime_brt,
                            "channels": []
                        }],
                        "error": f"Agent processing exception: {agent_e}"
                    })

            except Exception as search_e:
                print(f"Error processing search for {team1} vs {team2}: {search_e}")
                processed_data_by_series[series_name].append({
                    "name": team1,
                    "image": teams_lookup.get(team1, {}).get("image"),
                    "matches": [{
                        "adversary": team2,
                        "datetime_brt": datetime_brt,
                        "channels": []
                    }],
                    "error": f"Search future failed: {search_e}"
                })
    
    print(f"Completed all 'where to watch' searches and agent processing.")
    
    # --- Add teams without matches ---
    for series_name, series_info in processed_data_by_series.items():
        team_names_with_matches = [team_info["name"] for team_info in series_info]
        
        for team_name, team_data in teams_lookup.items():
            if team_data.get("serie") == series_name and team_name not in team_names_with_matches:
                # Add team with empty matches
                processed_data_by_series[series_name].append({
                    "name": team_name,
                    "image": team_data.get("image"),
                    "matches": []
                })
    
    # --- Combine results into final structure --- 
    final_processed_data = {"series": []}
    for series_name, teams in processed_data_by_series.items():
        if teams:  # Only add series that have teams
            final_processed_data["series"].append({"name": series_name, "teams": teams})
        
    # Save the combined processed results
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_processed_data, f, indent=2, ensure_ascii=False)
        print(f"Processed agent results saved to {output_file}")
    except Exception as e:
        print(f"Error saving processed agent results: {e}")
        
    # Optional: Save raw search results
    try:
        with open(watch_results_file, 'w', encoding='utf-8') as f:
            json.dump(all_watch_results, f, indent=2, ensure_ascii=False)
        print(f"Raw watch results saved to {watch_results_file}")
    except Exception as e:
        print(f"Error saving raw watch results: {e}")

    # Print summary
    print(f"Results summary:")
    for series in final_processed_data["series"]:
        series_name = series.get("name", "Unknown")
        team_count = len(series.get("teams", []))
        # Count matches, excluding teams that had errors
        matches_count = sum(len(team.get("matches", [])) for team in series.get("teams", []) if "error" not in team)
        error_count = sum(1 for team in series.get("teams", []) if "error" in team)
        print(f"  - {series_name}: {team_count} teams ({error_count} errors), {matches_count} upcoming matches found")
    
    print("-" * 20)
    print(f"Task completed at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    return final_processed_data


# --- Combined Two-Step Process ---

def fetch_and_process_football_matches():
    """
    Main function implementing the two-step approach:
    1. Find the next match for each team
    2. Find where to watch each match
    """
    print(f"Starting two-step football match search at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Step 1: Find next matches for each team
    next_matches = find_next_matches()
    
    # Step 2: Find where to watch each match
    if next_matches:
        final_results = find_where_to_watch(next_matches)
        return final_results
    else:
        print("No next matches found. Process stopped after step 1.")
        return None


# --- Main Loop ---
if __name__ == "__main__":
    print("Running two-step match search process...")
    fetch_and_process_football_matches()
    