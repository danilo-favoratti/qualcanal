#!/usr/bin/env python3

import os
import requests
import schedule
import time
import json
import concurrent.futures
from datetime import datetime
import pytz
from agents import Agent, Runner, WebSearchTool

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# --- Direct Serper API Functions ---

def search_serper_api(query, location="Brazil", gl="br", hl="pt-br", tbs="qdr:d", engine="google"):
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
        "tbs": tbs,
        "engine": engine,
    }

    try:
        response = requests.post(search_url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Error during Serper API request: {e}"}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}


def search_for_team(team_name):
    """
    Search for matches for a specific team and return the results.
    """
    query = f"onde assistir próximo jogo {team_name}"
    
    print(f"Searching for: {team_name}\n")
    result = search_serper_api(query)
    
    # Check if the search was successful
    if "error" in result:
        print(f"Error searching for {team_name}: {result['error']}")
        return {"team": team_name, "error": result["error"], "data": None}
        
    print(f"Found results for {team_name}")
    return {"team": team_name, "error": None, "data": result}


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
        tools=[
            WebSearchTool()
        ]
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


# --- Main Task Function ---

def fetch_and_process_all_teams_matches():
    """
    Performs parallel searches for all teams and then processes the results individually with an agent.
    """
    # Use fixed filenames instead of timestamped ones
    raw_results_file = 'search_results.json'
    output_file = 'match_results.json'
    
    print(f"Starting team search task at {time.strftime('%Y-%m-%d %H:%M:%S')}...")

    # Load teams data
    try:
        with open('teams.json', 'r', encoding='utf-8') as f:
            series_data = json.load(f)
    except FileNotFoundError:
        print("Error: teams.json not found.")
        return
    except json.JSONDecodeError:
        print("Error: Could not decode JSON from teams.json.")
        return

    # Extract all teams with their series and image
    teams_with_series = []
    for series in series_data:
        series_name = series.get('serie', 'Unknown')
        for team_obj in series.get('teams', []):
            if isinstance(team_obj, dict):
                teams_with_series.append({"team": team_obj, "serie": series_name})
            else: # Handle old format if necessary, though teams.json should be updated
                teams_with_series.append({"team": {"name": team_obj, "image": None}, "serie": series_name})

    if not teams_with_series:
        print("No teams found in teams.json.")
        return

    print(f"Found {len(teams_with_series)} teams. Starting parallel searches...")

    # Setup the agent once
    agent = setup_agent()
    
    # Dictionary to store processed results, organized by series
    processed_data_by_series = {series.get('serie'): [] for series in series_data}
    # Store raw results temporarily if needed for debugging (optional)
    all_search_results_for_debug = [] 

    brasilia_tz = pytz.timezone('America/Sao_Paulo')
    current_datetime_brt = datetime.now(brasilia_tz).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- Perform parallel searches and process results individually ---    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all search tasks
        future_to_team = {
            executor.submit(search_for_team, team_info["team"]["name"]):
            # Pass only the name to search_for_team
            team_info 
            for team_info in teams_with_series
        }
        
        # Process results as they complete
        for future in concurrent.futures.as_completed(future_to_team):
            team_info = future_to_team[future]
            team_name = team_info["team"]["name"]
            team_image = team_info["team"].get("image") # Get image path
            series_name = team_info["serie"]
            
            try:
                search_result = future.result()
                all_search_results_for_debug.append(search_result) # Optional: store raw result
                
                # Check if search failed
                if search_result.get("error"):
                    print(f"Skipping agent processing for {team_name} due to search error: {search_result['error']}")
                    # Optionally add a placeholder to the final results
                    processed_data_by_series[series_name].append({
                        "name": team_name,
                        "image": team_image,
                        "matches": [],
                        "error": f"Search failed: {search_result['error']}"
                    })
                    continue

                # --- Agent Processing for this team --- 
                print(f"Processing search results for {team_name} with agent...")
                
                # Prepare the prompt for the agent for a SINGLE team's data
                single_team_prompt = f'''## TASK: PARSE FOOTBALL MATCH DATA FOR A SINGLE TEAM - NEXT GAME ONLY

You have been given search results for the team: {team_name}.
Your job is to:
1. Parse these results to find **all upcoming matches** for {team_name}.
2. Identify the **chronologically closest future match**.
3. Extract details for **only that single next match**: opponent team, date/time (ISO8601 Brasilia), and viewing channels.
4. Format the result into a structured JSON containing **at most one match**.

## CURRENT DATETIME (Brasilia Time)
{current_datetime_brt}

## SEARCH RESULTS FOR {team_name}
```json
{json.dumps(search_result["data"]["organic"], ensure_ascii=False)}
```

## EXPECTED OUTPUT FORMAT

The output should be a JSON object. The "matches" array should contain **zero or one** match object (only the very next future game).
```json
{{
  "matches": [
    {{
      "adversary": "<Opponent Team>",
      "datetime_brt": "<ISO8601 DateTime Brasilia TimeZone>",
      "channels": [
        {{"name": "<Channel Name>", "url": "<Channel URL>"}}
      ]
    }}
  ]
}}
```

IMPORTANT INSTRUCTIONS:
- Find all future matches first, then select ONLY the one happening soonest.
- If multiple games are on the same closest date, pick one arbitrarily.
- Ensure datetime_brt is in ISO8601 format with Brasilia timezone (-03:00).
- Remove any duplicate channels for the selected match.
- If no future matches are found, return an empty "matches" list: `{{"matches": []}}`.
- Only include the JSON object (starting with {{ and ending with }}) in your response, no additional text or explanations.

Please provide the structured JSON containing only the next future match for {team_name}:'''

                try:
                    # Run the agent with the single team data
                    agent_result = Runner.run_sync(agent, single_team_prompt)
                    
                    if agent_result.final_output:
                        parsed_team_json = extract_json_from_response(agent_result.final_output)
                        
                        if parsed_team_json and isinstance(parsed_team_json, dict) and "matches" in parsed_team_json:
                            # Successfully parsed matches, add team name and image
                            team_output = {
                                "name": team_name,
                                "image": team_image,
                                "matches": parsed_team_json["matches"]
                            }
                            processed_data_by_series[series_name].append(team_output)
                            print(f"Successfully processed agent results for {team_name}")
                        else:
                            print(f"Failed to extract valid JSON 'matches' from agent response for {team_name}.")
                            # Add placeholder if agent failed
                            processed_data_by_series[series_name].append({
                                "name": team_name,
                                "image": team_image,
                                "matches": [],
                                "error": "Agent failed to return valid match JSON"
                            })
                            # Optional: Save raw agent output for this team
                            with open(f"agent_error_{team_name}.txt", 'w', encoding='utf-8') as f_err:
                                 f_err.write(agent_result.final_output or "No agent output.")
                    else:
                        print(f"No output received from the agent for {team_name}.")
                        processed_data_by_series[series_name].append({"name": team_name, "image": team_image, "matches": [], "error": "No agent output"})
                        
                except Exception as agent_e:
                    print(f"Error during agent processing for {team_name}: {agent_e}")
                    processed_data_by_series[series_name].append({"name": team_name, "image": team_image, "matches": [], "error": f"Agent processing exception: {agent_e}"})

            except Exception as search_e:
                print(f"Error processing search future for {team_info.get('team', {}).get('name', 'Unknown Team')}: {search_e}")
                # Add placeholder if search future itself failed
                processed_data_by_series[team_info["serie"]].append({
                    "name": team_info.get('team', {}).get('name', 'Unknown Team'),
                    "image": team_info.get('team', {}).get('image'),
                    "matches": [],
                    "error": f"Search future failed: {search_e}"
                })
    
    print(f"Completed all searches and agent processing.")
    
    # --- Combine results into final structure --- 
    final_processed_data = {"series": []}
    for series_name, teams in processed_data_by_series.items():
        final_processed_data["series"].append({"name": series_name, "teams": teams})
        
    # Save the combined processed results
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_processed_data, f, indent=2, ensure_ascii=False)
        print(f"Processed agent results saved to {output_file}")
    except Exception as e:
        print(f"Error saving processed agent results: {e}")
        
    # Optional: Save raw search results if needed
    try:
        with open(raw_results_file, 'w', encoding='utf-8') as f:
            json.dump(all_search_results_for_debug, f, indent=2, ensure_ascii=False)
        print(f"Raw search results saved to {raw_results_file}")
    except Exception as e:
        print(f"Error saving raw search results: {e}")

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
    

# --- Scheduling ---

# print("Setting up schedule...")
# Schedule the task at 02:00 and 10:00 local time
# schedule.every().day.at("02:00").do(run_agent_task)
# schedule.every().day.at("10:00").do(fetch_and_process_all_teams_matches)

# print("Scheduler started. Waiting for scheduled times (02:00 and 10:00)...")
print(f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

# --- Main Loop ---
if __name__ == "__main__":
    print("Running initial task immediately for testing...")
    fetch_and_process_all_teams_matches()
    # print("Initial task finished. Starting scheduler loop...")

    # while True:
    #   schedule.run_pending()
    #   time.sleep(60)  # Check every minute

