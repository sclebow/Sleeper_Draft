## This is an app that uses the Sleeper Fantasy Football API to create a draft board.
## It uses Streamlit for the GUI

print("\n" * 10)

import truststore
truststore.inject_into_ssl()

import streamlit as st
from sleeper_wrapper import League
import pandas as pd

from mock_draft_scraper import scrape_dynasty_nerds

st.set_page_config(
    page_title="Sleeper Draft Board",
    layout="wide",
)

DEFAULT_LEAGUE_ID = "1312558939023364096"

@st.cache_data(ttl=21600) # Cache league data for 6 hours
def get_data(league_id):
    league = League(league_id)
    league_data = league.get_league()
    users = league.get_users()
    traded_picks = league.get_traded_picks()
    rosters = league.get_rosters()
    standings = league.get_standings(rosters, users)

    return league_data, users, traded_picks, rosters, standings

league_data, users, traded_picks, rosters, standings = get_data(DEFAULT_LEAGUE_ID)

number_of_teams = len(rosters)

for roster in rosters:
    # Write Owner information to Rosters dict
    matching_user = next(
        (user for user in users if user["user_id"] == roster["owner_id"]),
        None,
    )

    roster["owner_display_name"] = matching_user["display_name"]
    roster["team_name"] = matching_user["metadata"]["team_name"]

    # Calculate points
    roster["points_for"] = roster["settings"]["fpts"] + roster["settings"]["fpts_decimal"] / 100
    roster["points_against"] = roster["settings"]["fpts_against"] + roster["settings"]["fpts_against_decimal"] / 100
    roster["potential_max_points"] = roster["settings"]["ppts"] + roster["settings"]["ppts_decimal"] / 100

# Write the owner_ids to the traded_picks dict
for pick in traded_picks:
    owner_roster = next(
        (roster for roster in rosters if roster["roster_id"] == pick["owner_id"]),
        None,
    )
    pick["owner_id"] = owner_roster["owner_id"] if owner_roster else None
    pick["owner_display_name"] = owner_roster["owner_display_name"] if owner_roster else None

    previous_owner_roster = next(
        (roster for roster in rosters if roster["roster_id"] == pick["previous_owner_id"]),
        None,
    )
    pick["previous_owner_id"] = previous_owner_roster["owner_id"] if previous_owner_roster else None
    pick["previous_owner_display_name"] = previous_owner_roster["owner_display_name"] if previous_owner_roster else None

# Get number of playoff teams
number_of_playoff_teams = league_data["settings"]["playoff_teams"]

# Build the draft order, by first sorting by wins, with points_for as a tiebreaker.
# Then by reverse max points possible for the non-playoff teams.
draft_order = sorted(
    rosters,
    key=lambda roster: (
        roster["settings"]["wins"],
        roster["points_for"],
    ),
    reverse=True,
)

playoff_teams = draft_order[:number_of_playoff_teams][::-1]
non_playoff_teams = draft_order[number_of_playoff_teams:]

# Sort non-playoff teams by potential max points, lowest first.
non_playoff_teams = sorted(
    non_playoff_teams,
    key=lambda roster: roster["potential_max_points"],
)

draft_order = non_playoff_teams + playoff_teams

# Build the draft board
number_of_draft_rounds = league_data["settings"]["draft_rounds"]
number_of_picks = number_of_teams * number_of_draft_rounds

next_draft_season = str(int(league_data["season"]) + 1)
number_of_years_of_tradable_picks = 3

draft_dict = {}

for year in range(int(next_draft_season), int(next_draft_season) + number_of_years_of_tradable_picks): 
    for pick in range(1, number_of_picks + 1):
        round = (pick - 1) // number_of_teams + 1
        original_owner = draft_order[(pick - 1) % number_of_teams]["owner_display_name"]

        # Check if the pick has been traded
        traded_pick = next(
            (
                traded_pick
                for traded_pick in traded_picks
                if traded_pick["season"] == str(year)
                and traded_pick["round"] == round
                and traded_pick["roster_id"] == draft_order[(pick - 1) % number_of_teams]["roster_id"]
            ),
            None,
        )

        draft_dict[(year, pick)] = {
            "year": str(year),
            "round": round,
            "pick_in_round": (pick - 1) % number_of_teams + 1,
            "shortened": f"{round}.{(pick - 1) % number_of_teams + 1:02d}",
            "original_owner": original_owner,
            "current_owner": traded_pick["owner_display_name"] if traded_pick else original_owner,
            "traded": traded_pick is not None,
        }


df = pd.DataFrame.from_dict(draft_dict, orient="index")

# # 2027 draft board
# # Hide year, round, pick_in_round and index columns
# st.write("2027 Draft Board")
# st.dataframe(df[df["year"] == "2027"].drop(columns=["year", "round", "pick_in_round", "original_owner"]).reset_index(drop=True))

# # Pivot the draft board to show the picks for each current_owner
# for owner, picks in df.groupby("current_owner"):
#     st.subheader(f"{owner} - Number of picks: {len(picks)}")
#     st.dataframe(picks)

@st.cache_data(ttl=21600)
def get_mock_draft_data():
    mock_drafts, mock_picks = scrape_dynasty_nerds()
    return mock_drafts, mock_picks

mock_drafts, mock_picks = get_mock_draft_data()

st.write("Mock Drafts")
st.dataframe(mock_drafts)

# Using only the most recent mock draft for each player and position, project the average draft position.
if not mock_picks.empty:
    include_strings = [
        "superflex",
    ]

    ignore_strings = [
        "tep",
        "1qb",
    ]

    latest_mock_ids = (
        mock_drafts[
            mock_drafts["title"].str.contains("|".join(include_strings), case=False, na=False)
            & ~mock_drafts["title"].str.contains("|".join(ignore_strings), case=False, na=False)
        ]
        .sort_values("published")
        .drop_duplicates(subset="mock_id", keep="last")["mock_id"]
    )
    most_recent_mock_id = latest_mock_ids.iloc[-1]
    latest_picks = mock_picks[mock_picks["mock_id"] == most_recent_mock_id]

    mock_number_of_teams = latest_picks["pick_in_round"].max()
    # Calculate overall pick from round and pick_in_round
    latest_picks["overall_pick"] = (
        (latest_picks["round"] - 1) * mock_number_of_teams + latest_picks["pick_in_round"]
    )

    # Using the overall_pick, and the league settings, correct round and pick_in_round to match the league's number of teams.
    latest_picks["round"] = (latest_picks["overall_pick"] - 1) // number_of_teams + 1
    latest_picks["pick_in_round"] = (latest_picks["overall_pick"] - 1) % number_of_teams + 1

    st.subheader("Most Recent Mock Draft")
    st.dataframe(latest_picks.reset_index(drop=True))

    # Project picks for league
    draft_board_2027 = df[df["year"] == "2027"].copy()

    # Add overall_pick to the draft board for matching mock picks to draft slots.
    draft_board_2027["overall_pick"] = (
        (draft_board_2027["round"] - 1) * number_of_teams
        + draft_board_2027["pick_in_round"]
    )
    st.dataframe(draft_board_2027)

    # Merge mock_picks into the 2027 draft board, keeping player, position, school
    # Use overall_pick to match mock picks to draft board slots.
    draft_board_2027 = draft_board_2027.merge(
        latest_picks[["overall_pick", "player", "position", "school"]],
        on="overall_pick",
        how="left",
    ).drop(columns="overall_pick")
    
    st.subheader("2027 Draft Board")
    st.dataframe(
        draft_board_2027.drop(
            columns=["year", "round", "pick_in_round", "original_owner", "traded"]
        ).reset_index(drop=True)
    )

    # Pivot to show picks by current_owner
    for current_owner in draft_board_2027["current_owner"].unique():
        st.subheader(current_owner)
        team_df = (
            draft_board_2027[draft_board_2027["current_owner"] == current_owner]
            .drop(
                columns=["year", "round", "pick_in_round", "original_owner", "traded", "current_owner"]
            )
            .reset_index(drop=True)
        )
        df_string = team_df.to_string(index=False, header=False)
        st.text(df_string)
